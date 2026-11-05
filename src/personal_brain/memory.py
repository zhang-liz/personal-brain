"""Agent memory on the Personal Brain graph: remember, find, ask and forget.

Shared by the MCP server (mcp_server.py), the viewer's live API (server.py) and
the forget command (forget.py). The agent has no tool that deletes.
Every write hangs off a Note node, so each fact can be traced to the note that
made it, and forgotten with it. People you forget go in data/forgotten.json, so
a rebuild (load_graph.py) leaves them out.
"""

import hashlib
import json
import os
import re
import time
import uuid
from datetime import date

from falkordb import FalkorDB, Edge, Node, Path as GraphPath
from redis.exceptions import ResponseError

from personal_brain.companies import core, near_company, one_edit, same_company, typo
from personal_brain.load_graph import EVENT_ROLE_RE, FORGOTTEN, TOPIC_RE, city_of, create_search_indexes, fold, forgotten_list, norm, without_accents

from personal_brain.notes_backup import every_row, sync_company
from personal_brain.paths import SOCKET_TIMEOUT_S, env_file_url, open_url
MAX_ROWS = 50        # rows ask() hands back to the agent
MAX_CHARS = 40000    # and how much text: clients refuse a reply that is too long, and the agent then gets nothing
FIND_SHOWN = 10      # matches the find tool shows
WHY_CHARS = 300      # how much of a note find about shows
OUTBOUND = re.compile(r"load.*csv", re.I | re.S)    # no parsing on purpose: a comment or a line break between the words must not get past it
TIMEOUT_MS = 5000    # longest an ask() query may run
SAVE_TIME_LIMIT_S = 300    # a note still saving after this long stopped halfway
LOST_SAVES = set()         # saves in this process that failed and could not clean up, because the connection was down

FIELDS = ("title", "location", "email")    # facts remember can set or correct on a person

SCHEMA = """Nodes: Me (you), Person, Company, School, Skill, Topic, Event, Investor, City, Note, FollowUp.
Every node has id. Every node but Note and FollowUp has name. Person: headline, title, company, location, email, connected_on, messages,
last_message (the day of your last LinkedIn message, for "who did I lose touch with": sort it, and people with none never wrote).
Company: well_funded, funding, employees. Event: date, upcoming, summary, url.
Edges:
(Me)-[:CONNECTED {on}]->(Person)
(Person)-[:WORKED_AT {title, start, end, current, noticed_on, left}]->(Company)   end is only on past jobs.
For "who changed jobs": noticed_on is the day a new LinkedIn export first showed this job, and left names what they left.
(Person)-[:STUDIED_AT]->(School), (Person)-[:HAS_SKILL]->(Skill), (Person)-[:HAS_EXPERIENCE {years}]->(Skill)
(Person)-[:INTO]->(Topic), (Person)-[:LIVES_IN]->(City)
(Person)-[:KNOWS {via, place}]-(Person)   inferred: coworkers or classmates at the same time
(Me)-[:ATTENDED]->(Event), (Person)-[:MET_AT]->(Event)
(Company)-[:FUNDED_BY {lead, round}]->(Investor)
(Event {upcoming: true})-[:HOSTED_BY {via}]->(Person or Company), (Event)-[:ABOUT]->(Topic), (Event)-[:HELD_IN]->(City)
(Me)-[:WROTE]->(Note {text, on})-[:ABOUT]->(Person), (Me)-[:MET {on, note}]->(Person)
People you met may be MET but not CONNECTED: for "my network", match both.
(FollowUp {text, open, on, due, done_on})-[:FOR]->(Person)   things the user said they would do. open is false when done.
Topics: AI Agents, Agent Memory, Knowledge Graphs, RAG & Search, LLMs & GenAI, Developer Relations,
Community & Events, Partnerships, Investing, Founder, Podcast & Media."""


def connect(graph=None):
    """The graph on FalkorDB Cloud (FALKORDB_URL in the environment or .env), else local FalkorDB."""
    url = os.environ["FALKORDB_URL"] if "FALKORDB_URL" in os.environ else env_file_url()
    db = open_url(url) if url else FalkorDB(host="localhost", port=6379, socket_timeout=SOCKET_TIMEOUT_S)
    return db.select_graph(graph or os.environ.get("PB_GRAPH", "network")), ("FalkorDB Cloud" if url else "local FalkorDB")


def setup(g):
    """You, and full-text search on names. Safe to run on every start: it only fills in what is missing."""
    g.query("MERGE (me:Me {id: 'me'}) ON CREATE SET me.name = 'Me'")   # an empty graph works too
    g.query("UNWIND $topics AS t MERGE (:Topic {id: t, name: t})", {"topics": list(TOPIC_RE)})
    for label in ["Person", "Company", "Event"]:
        # Older graphs have no folded name, or an empty one on non-Latin names: fill it in, 5,000 nodes at a time.
        last = -1
        while rows := g.query(f"MATCH (n:{label}) WHERE id(n) > $last AND (n.search IS NULL OR n.search = '')"
                              " RETURN id(n), n.name ORDER BY id(n) LIMIT 5000", {"last": last}).result_set:
            g.query("UNWIND $rows AS r MATCH (n) WHERE id(n) = r[0] SET n.search = r[1]",
                    {"rows": [[i, fold(name)] for i, name in rows]})
            last = rows[-1][0]
    create_search_indexes(g)
    # Older graphs held the build date as the end of a current job.
    g.query("MATCH ()-[w:WORKED_AT {current: true}]->() WHERE w.end IS NOT NULL SET w.end = NULL")


# ---------- find ----------

def search_query(text):
    """Full-text query: every word must match, as a prefix or with 1 typo ("jnas" finds Jonas)."""
    return " ".join(f"({w}*|%{w}%)" if len(w) >= 4 else f"{w}*" for w in fold(text).split())


# What to show next to each match, so the agent (and you) can tell two Jonases apart.
# Small separate queries on purpose. FalkorDB 6 returns wrong rows, or none, when one query goes on from a search result,
# has a second OPTIONAL MATCH after a collect, or has a pattern comprehension with an edge property.
FIND = {
    "Person": "RETURN node.id, {headline: node.headline, connected_on: node.connected_on, title: node.title,"
              " location: node.location, email: node.email, last_message: node.last_message}",
    "Company": "OPTIONAL MATCH (node)<-[:WORKED_AT {current: true}]-(p:Person) "
               "WITH node, count(p) AS people "
               "RETURN node.id, {people_you_know_there: people, funding: node.funding}",
    "Event": "RETURN node.id, {date: node.date, upcoming: node.upcoming}",
}
FIND_LISTS = {
    "works_at": "MATCH (node:Person)-[:WORKED_AT {current: true}]->(c:Company) WHERE node.id IN $ids RETURN node.id, collect(c.name)",
    "follow_ups": "MATCH (f:FollowUp {open: true})-[:FOR]->(node:Person) WHERE node.id IN $ids RETURN node.id, collect({id: f.id, text: f.text, due: f.due})",
}


def find(g, text, label=None, limit=10):
    """People, companies and events whose name matches, best first."""
    if label and label not in FIND:
        raise ValueError(f"Unknown label {label}. Use Person, Company or Event.")
    query = search_query(text)
    if not query:
        return []
    out = []
    for lab in [label] if label else list(FIND):
        rows = g.ro_query("CALL db.idx.fulltext.queryNodes($label, $q) YIELD node, score "
                          f"RETURN node.id, node.name, score ORDER BY score DESC LIMIT {int(limit)}", {"label": lab, "q": query}).result_set
        ids = {"ids": [row[0] for row in rows]}
        details = dict(g.ro_query(f"MATCH (node:{lab}) WHERE node.id IN $ids {FIND[lab]}", ids).result_set) if rows else {}
        for field, cypher in FIND_LISTS.items() if lab == "Person" and rows else ():
            for i, values in g.ro_query(cypher, ids).result_set:
                details[i][field] = values
        out += [{"label": lab, "id": i, "name": name, "score": round(score, 2),
                 **{k: v for k, v in details.get(i, {}).items() if v not in (None, "", [])}} for i, name, score in rows]
    return sorted(out, key=lambda m: -m["score"])[:limit]


def find_shown(g, text, label=None):
    """What the find tool shows. It says when there are more, so that the agent does not take 10 people for all of them."""
    rows = find(g, text, label, FIND_SHOWN + 1)
    reply = {"found": min(len(rows), FIND_SHOWN), "matches": rows[:FIND_SHOWN]}
    if not rows:
        reply["message"] = "No match. Try part of the name, or use remember to add them as a new person."
    elif len(rows) > FIND_SHOWN:
        reply.update(more=True, message="There are more matches. Use more of the name, or a label, or count them with ask.")
    return reply


# ---------- about ----------

# Where about() looks, best first: your own words come before what LinkedIn says.
ABOUT = {
    "note": "MATCH (x:Note)-[r:ABOUT]->(p:Person) WITH p, x, id(r) AS at WHERE {}",
    "topic": "MATCH (p:Person)-[r:INTO]->(t:Topic) WITH p, t, id(r) AS at WHERE {}",
    "skill": "MATCH (p:Person)-[r:HAS_SKILL|HAS_EXPERIENCE]->(s:Skill) WITH p, s, id(r) AS at WHERE {}",
    "headline": "MATCH (p:Person) WITH p, id(p) AS at WHERE {}",
}
ABOUT_FIELD = {"note": "x.text", "topic": "t.name", "skill": "s.name", "headline": "p.headline"}
ABOUT_LIMIT = 20


def has_word_starts(text, words):
    """Every word is in the text, at the start of a word: "ai" is not in "email". Scripts with no spaces match anywhere."""
    text = text.lower()
    return all(re.search(r"(?<!\w)" + re.escape(w), text) if w.isascii() else w in text for w in words)


def about(g, text):
    """People by what they do or what you talked about: words in notes, topics, skills and headlines."""
    words = text.lower().split()
    if not words:
        raise ValueError("Give some words to look for.")
    people = {}
    for rank, (source, cypher) in enumerate(ABOUT.items()):
        field = ABOUT_FIELD[source]
        where = f"all(w IN $words WHERE toLower({field}) CONTAINS w)"
        seen = set()    # a skill can come through two links
        for pid, name, found in every_row(g, cypher.format(where), f"p.id, p.name, {field}", {"words": words}, TIMEOUT_MS):
            if (pid, found) not in seen and has_word_starts(found, words):
                seen.add((pid, found))
                person = people.setdefault(pid, {"id": pid, "name": name, "rank": rank, "why": []})
                person["why"].append({"from": source, "text": found[:WHY_CHARS]})
    best = sorted(people.values(), key=lambda p: (p["rank"], -len(p["why"]), p["name"]))[:ABOUT_LIMIT]
    jobs = dict(g.ro_query("MATCH (p:Person) WHERE p.id IN $ids OPTIONAL MATCH (p)-[:WORKED_AT {current: true}]->(c:Company)"
                           " RETURN p.id, collect(c.name)", {"ids": [p["id"] for p in best]}).result_set)
    return {"found": len(people), "people": [{"id": p["id"], "name": p["name"], "works_at": jobs.get(p["id"], []), "why": p["why"][:3]}
                                             for p in best]}


# ---------- remember ----------

def close(a, b):
    """Same name give or take accents, case and 1 typo per word of 4+ letters (the rule find uses)."""
    wa, wb = fold(a).split(), fold(b).split()
    return len(wa) == len(wb) and all(x == y or (min(len(x), len(y)) >= 4 and one_edit(x, y)) for x, y in zip(wa, wb))


def resolve(g, name, company, person_id, new_person):
    """Which Person is this? Returns (person, None) when sure, (None, candidates) when not."""
    if person_id:
        rows = g.ro_query("MATCH (p:Person {id: $id}) RETURN p.name", {"id": person_id}).result_set
        if not rows:
            raise ValueError(f"No person with id {person_id}. Use find to look it up.")
        return {"id": person_id, "name": rows[0][0], "new": False}, None
    matches = [] if new_person else find(g, name, "Person", 20)
    same = [m for m in matches if fold(m["name"]) == fold(name)]
    if not same:
        # A typo of someone you know, or a new person with a similar name: ask, never merge on a guess.
        near = [m for m in matches if close(m["name"], name)]
        if near:
            return None, near
        return {"id": "note-" + uuid.uuid4().hex[:12], "name": name, "new": True}, None
    if company:
        # Any job in their history counts, past or current.
        jobs = dict(g.ro_query("MATCH (p:Person) WHERE p.id IN $ids OPTIONAL MATCH (p)-[:WORKED_AT]->(c:Company) "
                               "RETURN p.id, collect(c.id)", {"ids": [m["id"] for m in same]}).result_set)
        key = norm(company)
        at = [m for m in same if any(same_company(c, key) for c in jobs.get(m["id"], []))]
        if len(at) == 1:
            return {"id": at[0]["id"], "name": at[0]["name"], "new": False}, None
    elif len(same) == 1:
        return {"id": same[0]["id"], "name": same[0]["name"], "new": False}, None
    # Two people with this name, or one who never worked at this company (moved jobs, or someone else?)
    return None, same


def meeting_date(on):
    """The day of the meeting, as YYYY-MM-DD. No date means today."""
    if not on:
        return date.today().isoformat()
    try:
        day = date.fromisoformat(on)
    except (TypeError, ValueError):
        raise ValueError(f"on must be a date like 2026-11-04, not {on}.") from None
    if day > date.today():
        raise ValueError(f"{on} is in the future. remember saves meetings that already happened.")
    return day.isoformat()


def as_said(text):
    """Text for telling a retry from new words: case, accents and spacing go. "C++" and "C#" stay two things."""
    return " ".join(without_accents(text).lower().split()).rstrip(".!,;: ")


def repeat_key(company, event, text, day, fields):
    """Two notes with this key about one person are one meeting saved twice, from an agent's retry."""
    return hashlib.sha256(json.dumps([as_said(company), as_said(event), as_said(text), day, sorted(fields.items())]).encode()).hexdigest()[:16]


def saved_before(g, key, person, company, event, note):
    """The notes this same call saved before, as (note id, person id, person name)."""
    ids = [person["id"]]
    if person["new"] and (company or event or note):
        # A retry with new_person=true looks for a person it may have just made.
        ids = [m["id"] for m in find(g, person["name"], "Person", 20) if fold(m["name"]) == fold(person["name"])]
    return g.ro_query("MATCH (n:Note {key: $key})-[:ABOUT]->(p:Person) WHERE p.id IN $ids RETURN n.id, p.id, p.name",
                      {"key": key, "ids": ids}).result_set


def still_true(g, nid, pid, fields, company, follow_up, ended_jobs=None):
    """A retry changes nothing. If what the earlier note said has changed since, the same words are news."""
    if fields:
        now = g.ro_query("MATCH (p:Person {id: $pid}) RETURN " + ", ".join(f"p.{f}" for f in fields), {"pid": pid}).result_set[0]
        if list(now) != list(fields.values()):
            return False
    if company:
        jobs = g.ro_query("MATCH (:Person {id: $pid})-[:WORKED_AT {current: true}]->(c:Company) RETURN c.id", {"pid": pid}).result_set
        if not any(same_company(c, norm(company) or company) for (c,) in jobs):
            return False
    if follow_up:
        rows = g.ro_query("MATCH (f:FollowUp {note: $nid}) RETURN f.open", {"nid": nid}).result_set
        if not (rows and rows[0][0]):
            return False
    if ended_jobs:
        # They went back to the job since, so ending it again is news.
        jobs = g.ro_query("MATCH (:Person {id: $pid})-[:WORKED_AT {current: true}]->(c:Company) RETURN c.id, c.name", {"pid": pid}).result_set
        if any(c == norm(job) or fold(name) == fold(job) for c, name in jobs for job in ended_jobs):
            return False
    return True


def jobs_that_ended(now, ended_jobs, name):
    """The company ids in ended_jobs. Every one must be a current job."""
    ended = []
    for job in ended_jobs:
        match = [cid for cid, cname in now if cid == norm(job) or fold(cname) == fold(job)]
        if not match:
            jobs = ", ".join(cname for _, cname in now) or "none"
            raise ValueError(f"{job} is not a current job of {name}. Their current jobs: {jobs}.")
        ended += match
    return ended


def same_event(a, b):
    """Same event give or take "and" and "&", case and accents."""
    return event_words(a) == event_words(b)


def near_event(a, b):
    """1 letter off per word of 4+ letters. It can be a typo or another event, so only the user can say. A year must match exactly."""
    wa, wb = event_words(a), event_words(b)
    return wa != wb and len(wa) == len(wb) and all(
        x == y or (min(len(x), len(y)) >= 4 and not any(c.isdigit() for c in x + y) and typo(x, y)) for x, y in zip(wa, wb))


def event_words(name):
    return [w for w in fold(name).split() if w != "and"]


def set_fields(q, fields, company_id, now):
    """Title, city and email go on the person. Each note keeps what it replaced, so forgetting it brings that back."""
    for field, value in fields.items():
        # First the undo, then the change: a save that stops at any step can still be undone.
        q(f"MATCH (n:Note {{id: $nid}}), (p:Person {{id: $pid}})"
          f" SET n.prev_{field} = coalesce(p.{field}, ''), n.prev_{field}_note = coalesce(p.{field}_note, ''), n.set_{field} = true")
        if field == "location":
            cities = q("MATCH (:Person {id: $pid})-[:LIVES_IN]->(c:City) RETURN c.id, c.name")
            q("MATCH (n:Note {id: $nid}) SET n.prev_cities = $ids, n.prev_city_names = $names",
              ids=[c for c, _ in cities], names=[n for _, n in cities])
            q("MATCH (:Person {id: $pid})-[r:LIVES_IN]->() DELETE r")
            city = city_of(value, ())
            if city:
                q("MERGE (c:City {id: $cid}) ON CREATE SET c.name = $city, c.source = 'agent', c.note = $nid"
                  " WITH c MATCH (p:Person {id: $pid}) CREATE (p)-[:LIVES_IN {note: $nid}]->(c)", cid=norm(city), city=city)
        q(f"MATCH (p:Person {{id: $pid}}) SET p.{field} = $value, p.{field}_note = $nid", value=value)
    job = company_id or (now[0][0] if len(now) == 1 else "")
    if "title" in fields and job:
        q("MATCH (:Person {id: $pid})-[w:WORKED_AT {current: true}]->(:Company {id: $job})"
          " SET w.prev_title = CASE WHEN w.title_note IS NULL THEN coalesce(w.title, '') ELSE w.prev_title END,"
          " w.title_log = CASE WHEN w.title_log IS NULL AND w.title_note IS NOT NULL THEN [w.title_note, w.title]"
          " ELSE coalesce(w.title_log, []) END + [$nid, $title], w.title = $title, w.title_note = $nid",
          job=job, title=fields["title"])


def undo_fields(g, nid, note, pid):
    """Put back what this note replaced. If a later note replaced it again, that later note now remembers the older value."""
    for field in FIELDS:
        if not note.get(f"set_{field}"):
            continue
        before, before_note = note.get(f"prev_{field}"), note.get(f"prev_{field}_note")
        later = g.ro_query(f"MATCH (m:Note {{prev_{field}_note: $nid}}) RETURN m.id", {"nid": nid}).result_set
        if later:
            g.query(f"MATCH (m:Note {{id: $later}}) SET m.prev_{field} = $before, m.prev_{field}_note = $before_note"
                    + (", m.prev_cities = $cities, m.prev_city_names = $names" if field == "location" else ""),
                    {"later": later[0][0], "before": before, "before_note": before_note, "cities": note.get("prev_cities", []),
                     "names": note.get("prev_city_names", [])})
            continue
        g.query(f"MATCH (p:Person {{id: $pid}}) SET p.{field} = $before, p.{field}_note = $before_note",
                {"pid": pid, "before": before or None, "before_note": before_note or None})
        if field == "location":
            # The city may have gone with another person's note. MERGE: a save that stopped early still has the old link.
            ids, names = note.get("prev_cities", []), note.get("prev_city_names", [])
            cities = [{"id": c, "name": names[i] if i < len(names) else c} for i, c in enumerate(ids)]
            g.query("UNWIND $cities AS city MERGE (c:City {id: city.id})"
                    " ON CREATE SET c.name = city.name, c.source = 'agent', c.note = $before_note"
                    " WITH c MATCH (p:Person {id: $pid}) MERGE (p)-[r:LIVES_IN]->(c) SET r.note = $before_note",
                    {"pid": pid, "cities": cities, "before_note": before_note or None})
    # Each job keeps its own list of titles from notes, so a note goes from the middle of it with no effect on other jobs.
    for edge, log, first, title_now, note_now in g.ro_query(
            "MATCH (p:Person {id: $pid})-[w:WORKED_AT]->() WHERE w.title_note = $nid OR $nid IN coalesce(w.title_log, [])"
            " RETURN id(w), w.title_log, w.prev_title, p.title, p.title_note", {"pid": pid, "nid": nid}).result_set:
        if log is None:    # from 0.3.1, which kept no list: the job follows the person
            title, title_note = (title_now, note_now) if note_now else (first or None, None)
            log = [title_note, title] if note_now else []
        else:
            at = log.index(nid) if nid in log else None
            log = log if at is None else log[:at] + log[at + 2:]
            title, title_note = (log[-1], log[-2]) if log else (first or None, None)
        g.query("MATCH ()-[w]->() WHERE id(w) = $edge"
                " SET w.title = $title, w.title_note = $note, w.title_log = $log, w.prev_title = $first",
                {"edge": edge, "title": title, "note": title_note, "log": log or None, "first": first if log else None})


def due_date(due, follow_up):
    if not due:
        return None
    if not follow_up:
        raise ValueError("due is the date of a follow_up, and you gave no follow_up.")
    try:
        return date.fromisoformat(due).isoformat()
    except (TypeError, ValueError):
        raise ValueError(f"due must be a date like 2026-11-04, not {due}.") from None


def clean_half_saved(g):
    """A save that stopped halfway, when the connection dropped, must not confuse this one.

    Another session may be saving right now, so only a save this process lost, or an old one, is half-saved."""
    now = time.time()
    for nid, since in g.ro_query("MATCH (n:Note) WHERE n.saving IS NOT NULL RETURN n.id, n.saving").result_set:
        if nid in LOST_SAVES or since is True or now - since > SAVE_TIME_LIMIT_S:
            try:
                forget_note(g, nid)
            except ValueError:
                pass    # another session cleaned it first
            LOST_SAVES.discard(nid)


def remember(g, name, company="", event="", note="", person_id="", new_person=False, on="", ended_jobs=None,
             title="", location="", email="", follow_up="", due="", follow_up_done="", new_event=False):
    """Save one meeting (who, where they work, where you met, what you talked about, when), or correct a fact about someone."""
    name, company, event, note, title, location, email, follow_up = (
        " ".join((x or "").split()) for x in (name, company, event, note, title, location, email, follow_up))
    due = due_date(due, follow_up)
    done = None
    if follow_up_done:
        rows = g.ro_query("MATCH (f:FollowUp {id: $id})-[:FOR]->(p:Person) RETURN p.id, f.text, f.open", {"id": follow_up_done}).result_set
        if not rows or (person_id and rows[0][0] != person_id):
            raise ValueError(f"No follow-up {follow_up_done} for this person. Use find to see their open follow-ups.")
        done = rows[0]
        person_id = done[0]
    if not name and not person_id:
        raise ValueError("Give a name, or a person_id from find.")
    fields = {f: v for f, v in zip(FIELDS, (title, location, email)) if v}
    met = bool(note or event or company) or not (fields or follow_up or done or ended_jobs)    # a correction or a to-do is not a meeting
    day = meeting_date(on)
    clean_half_saved(g)
    person, candidates = resolve(g, name, company, person_id, new_person)
    if person is None:
        return {"status": "needs_choice", "candidates": candidates,
                "message": f"Not sure who {name} is: these people have the same or a similar name. Ask the user, "
                           "then call remember again with person_id for one of them, or new_person=true for someone new."}

    if met:
        text = note or " ".join(x for x in [f"Met {person['name']}", company and f"from {company}", event and f"at {event}"] if x)
    else:
        text = ". ".join(x for x in ["Updated " + ", ".join(f"{f}: {v}" for f, v in fields.items()) if fields else "",
                                     f"Follow up: {follow_up}" if follow_up else "", f"Done: {done[1]}" if done else "",
                                     f"Ended: {', '.join(ended_jobs)}" if ended_jobs else ""] if x)
    extra = {**fields, "follow_up": follow_up, "due": due or "", "done": follow_up_done, "ended": sorted(as_said(j) for j in ended_jobs or [])}
    key = repeat_key(company, event, text, day, {k: v for k, v in extra.items() if v})
    before = next((b for b in saved_before(g, key, person, company, event, note or fields or follow_up)
                   if still_true(g, b[0], b[1], fields, company, follow_up, ended_jobs)), None)
    if before:
        return {"status": "already_saved", "note_id": before[0], "person": {"id": before[1], "name": before[2], "new": False},
                "changes": [], "ms": 0.0, "undo": f"personal-brain forget note {before[0]}"}

    if done and not done[2]:
        raise ValueError(f"The follow-up {follow_up_done} is already done.")
    pid = person["id"]
    now = g.ro_query("MATCH (:Person {id: $pid})-[:WORKED_AT {current: true}]->(c:Company) RETURN c.id, c.name",
                     {"pid": pid}).result_set
    cid = ""
    if company:
        said = norm(company) or company    # an emoji has no words: use it as it is
        same_job = [(c, n) for c, n in now if same_company(c, said)]
        known = [] if same_job else [(m["id"], m["name"]) for m in find(g, core(said), "Company", 5) if core(norm(m["name"])) == core(said)]
        cid, company = (same_job or known or [(said, company)])[0]
        new_job = not same_job
        said_other_name = bool(same_job) and core(said) != core(cid)
    else:
        new_job = said_other_name = False
    near = [cname for c, cname in now if near_company(c, said)] if new_job and not known and ended_jobs is None else []
    if near:
        return {"status": "needs_company", "person": person, "current_jobs": near,
                "message": f"{company} is one letter off {near[0]}, a current job of {person['name']}. Ask the user if it is the same company. "
                           f"If it is, call remember again with company={near[0]}. If it is a new job, call again with ended_jobs: "
                           "the jobs that ended (an empty list if none ended)."}
    if new_job and ended_jobs is None and len(now) > 1:
        jobs = [cname for _, cname in now]
        return {"status": "needs_jobs", "person": person, "current_jobs": jobs,
                "message": f"{person['name']} has {len(jobs)} current jobs: {', '.join(jobs)}. Ask the user which ones ended, then call "
                           "remember again with ended_jobs (an empty list if none ended)."}
    ended = [c for c, _ in now] if ended_jobs is None else jobs_that_ended(now, ended_jobs, person["name"])
    if ended_jobs and not new_job and cid in ended:
        raise ValueError(f"Nothing was saved: {company} is in both company and ended_jobs. Pick one.")
    matches = find(g, " ".join(event_words(event)) or event, "Event", 5) if event else []
    same_events = sorted((m for m in matches if same_event(m["name"], event)), key=lambda m: fold(m["name"]) != fold(event))
    near_events = [m for m in matches if near_event(m["name"], event)]
    if near_events and not same_events and not new_event:
        return {"status": "needs_event", "person": person, "candidates": near_events,
                "message": f"{event} is one letter off {near_events[0]['name']}. Ask the user if it is the same event. If it is, call remember "
                           f"again with event={near_events[0]['name']}. If it is another event, call again with new_event=true."}

    nid = "note-" + uuid.uuid4().hex[:12]
    fid = "followup-" + uuid.uuid4().hex[:12] if follow_up else ""
    changes, ms = [], 0.0

    def q(cypher, **params):
        nonlocal ms
        r = g.query(cypher, {"nid": nid, "pid": pid, "day": day, **params})
        ms += r.run_time_ms
        return r.result_set

    def save():
        q("MATCH (me:Me) CREATE (me)-[:WROTE]->(:Note {id: $nid, text: $text, on: $day, source: 'agent', saving: $since, saved_at: $since})", text=text, since=time.time())
        if person["new"]:
            # The note's text stays on the Note, so forgetting the note removes it. The job step below sets the company.
            q("CREATE (:Person {id: $pid, name: $name, search: $search, company: '', headline: '',"
              " source: 'agent', note: $nid, messages: 0, enriched: false, event_contact: $ec})",
              name=person["name"], search=fold(person["name"]), ec=bool(EVENT_ROLE_RE.search(note)))
            changes.append(f"new person: {person['name']}")
        q("MATCH (n:Note {id: $nid}), (p:Person {id: $pid}) CREATE (n)-[:ABOUT]->(p)")
        if met:
            q("MATCH (me:Me), (p:Person {id: $pid}) CREATE (me)-[:MET {on: $day, note: $nid}]->(p)")

        if company:
            q("MERGE (c:Company {id: $cid})"
              " ON CREATE SET c.name = $company, c.search = $search, c.source = 'agent', c.note = $nid",
              cid=cid, company=company, search=fold(company))
            if said_other_name:
                changes.append(f"same job as {company}")
            if not new_job:
                # This note also says so, so the job stays if the note that started it goes.
                q("MATCH (:Person {id: $pid})-[w:WORKED_AT {current: true}]->(:Company {id: $cid}), (n:Note {id: $nid})"
                  " WHERE w.note IS NOT NULL SET n.job_note = w.note", cid=cid)
            if new_job:
                # A new job: end the old ones instead of deleting them, so the history stays.
                q("MATCH (p:Person {id: $pid}), (n:Note {id: $nid}) SET n.prev_company = coalesce(p.company, '')")
                # One query, so a save that stops cannot leave the old job ended and the new one missing.
                q("MATCH (p:Person {id: $pid}), (c:Company {id: $cid})"
                  " CREATE (p)-[:WORKED_AT {current: true, since: $day, source: 'agent', note: $nid}]->(c)"
                  " SET p.company = $company, p.headline = CASE WHEN p.source = 'agent' THEN $company ELSE p.headline END"
                  " WITH p MATCH (p)-[w:WORKED_AT {current: true}]->(old:Company) WHERE old.id IN $ended AND old.id <> $cid"
                  " SET w.current = false, w.ended_on = $day, w.ended_by = $nid", cid=cid, company=company, ended=ended)
                names = ", ".join(n for c, n in now if c in ended)
                changes.append(f"works at {company} now" + (f" (ended: {names})" if names else ""))
                coworkers = q("MATCH (p:Person {id: $pid}), (o:Person)-[:WORKED_AT {current: true}]->(:Company {id: $cid})"
                              " WHERE o.id <> $pid AND NOT (p)-[:KNOWS]-(o) WITH p, o LIMIT 400"
                              " CREATE (p)-[:KNOWS {via: 'coworkers', place: $company, note: $nid}]->(o) RETURN count(o)",
                              cid=cid, company=company)
                if coworkers and coworkers[0][0]:
                    changes.append(f"knows {coworkers[0][0]} coworker{'s' if coworkers[0][0] > 1 else ''}")

        if ended_jobs and not new_job:
            # They left with no new job. First the mark for the undo, then the change.
            q("MATCH (n:Note {id: $nid}) SET n.ended_only = true")
            q("MATCH (:Person {id: $pid})-[w:WORKED_AT {current: true}]->(old:Company) WHERE old.id IN $ended"
              " SET w.current = false, w.ended_on = $day, w.ended_by = $nid", ended=ended)
            sync_company(g, pid)
            changes.append("ended: " + ", ".join(n for c, n in now if c in ended))

        if event:
            eid = same_events[0]["id"] if same_events else "note-" + (norm(event) or event).replace(" ", "-")
            q("MERGE (e:Event {id: $eid})"
              " ON CREATE SET e.name = $event, e.search = $search, e.date = $day, e.confirmed = true,"
              " e.source = 'agent', e.note = $nid"
              " WITH e MATCH (me:Me), (p:Person {id: $pid})"
              " MERGE (me)-[a:ATTENDED]->(e) ON CREATE SET a.note = $nid"
              " CREATE (p)-[:MET_AT {on: $day, inferred: false, note: $nid}]->(e)",
              eid=eid, event=event, search=fold(event))
            changes.append(f"met at {same_events[0]['name'] if same_events else event}")

        for topic in [t for t, rx in TOPIC_RE.items() if rx.search(note)]:
            q("MATCH (p:Person {id: $pid}), (t:Topic {id: $t}) MERGE (p)-[i:INTO]->(t) ON CREATE SET i.note = $nid", t=topic)
            changes.append(f"into {topic}")

        set_fields(q, fields, cid, now)
        changes.extend(f"{f}: {v}" for f, v in fields.items())

        if follow_up:
            q("MATCH (p:Person {id: $pid}) CREATE (f:FollowUp {id: $fid, text: $todo, open: true, on: $day, source: 'agent', note: $nid})"
              "-[:FOR {note: $nid}]->(p) SET f.due = $due", fid=fid, todo=follow_up, due=due)
            changes.append(f"follow up: {follow_up}" + (f" by {due}" if due else ""))
        if done:
            q("MATCH (f:FollowUp {id: $fid}) SET f.open = false, f.done_on = $day, f.done_by = $nid", fid=follow_up_done)
            changes.append(f"done: {done[1]}")

        # Last step: only a full save has a key, so a retry after a failure saves again.
        q("MATCH (n:Note {id: $nid}) SET n.key = $key, n.saving = NULL", key=key)

    try:
        save()
    except Exception:
        try:
            forget_note(g, nid)
        except Exception:
            LOST_SAVES.add(nid)    # the connection is still down: the next remember cleans up
        raise

    return {"status": "saved", "note_id": nid, "person": person, "changes": changes, "ms": round(ms, 2),
            **({"follow_up_id": fid} if fid else {}), "undo": f"personal-brain forget note {nid}"}


# "met NAME [from COMPANY] [at EVENT][, note]". A dot ends the name only with a space after it, and never after "Dr", "St" or an initial.
MET_RE = re.compile(
    r"^\s*(?:i\s+)?(?:just\s+)?met\s+(?P<name>.+?)"
    r"(?:\s+(?:from|of)\s+(?P<company>.+?))?"
    r"(?:\s+(?:at|during)\s+(?P<event>.+?))?"
    r"\s*(?:(?:(?<!\bdr)(?<!\bmr)(?<!\bms)(?<!\bmrs)(?<!\bprof)(?<!\bst)(?<!\b[a-z])\.(?=\s|$)|[;:!]|,|\s[-–—]\s)\s*(?P<note>.*))?$", re.I)


def parse_met(text):
    """The viewer's text box: one line about someone you met, in parts. None if it is not about a meeting."""
    m = MET_RE.match(text.strip())
    if not m:
        return None
    parts = {k: (v or "").strip(" ") for k, v in m.groupdict().items()}
    return parts if parts["name"] else None


# ---------- ask ----------

def shown(key):
    """Bookkeeping fields stay out of what the agent sees."""
    return key not in ("search", "key", "saving", "saved_at", "job_note", "title_log", "ended_only") and not key.endswith("_note") and not key.startswith(("prev_", "set_"))


def plain(v):
    """FalkorDB values as JSON."""
    if isinstance(v, Node):
        return {"label": (v.labels or [""])[0], **{k: x for k, x in v.properties.items() if shown(k)}}
    if isinstance(v, Edge):
        return {"type": v.relation, **{k: x for k, x in v.properties.items() if shown(k)}}
    if isinstance(v, GraphPath):
        return {"nodes": [plain(n) for n in v.nodes()], "edges": [plain(e) for e in v.edges()]}
    if isinstance(v, list):
        return [plain(x) for x in v]
    if isinstance(v, dict):
        return {k: plain(x) for k, x in v.items()}
    return v


def ask(g, cypher, params=None):
    """A read-only Cypher query (GRAPH.RO_QUERY) with a time limit. Writes are refused by FalkorDB itself."""
    # GRAPH.RO_QUERY allows LOAD CSV, and its URL can hold anything from the graph. So a hidden instruction in a headline could send your network to any server.
    if OUTBOUND.search(cypher):
        raise ValueError("ask cannot use LOAD CSV, because it can send your data to the internet. "
                         "If these words are part of a value, pass the value as a $parameter.")
    g.schema.clear()    # a rebuild renumbers field names, and this client may be older than the rebuild
    try:
        r = g.ro_query(cypher, params or {}, timeout=TIMEOUT_MS)
    except ResponseError as e:
        if "timed out" not in str(e):
            raise
        raise ValueError(f"The query took more than {TIMEOUT_MS // 1000} seconds. Start from one named node, use a shorter path, or add a LIMIT.") from None
    rows, chars = [], 0
    for row in r.result_set[:MAX_ROWS]:
        row = [plain(v) for v in row]
        chars += len(json.dumps(row, ensure_ascii=False, default=str))
        if chars > MAX_CHARS:
            break
        rows.append(row)
    reply = {"columns": [h[1] for h in r.header], "rows": rows, "truncated": len(rows) < len(r.result_set), "ms": round(r.run_time_ms, 2)}
    if reply["truncated"]:    # FalkorDB stops at 10,000 rows, so the number of rows it sent is not the total
        reply["message"] = (f"Showing {len(rows)} rows of an unknown total. For a total, use count(). "
                            "To see other rows, return fewer fields, or use ORDER BY and LIMIT.")
    else:
        reply["row_count"] = len(rows)
    return reply


# ---------- forget ----------

def old_notes_on_job(g, nid, note, pid, job, job_name):
    """Notes from 0.3.1 that also named this job. They have no job_note, but their retry key holds the words they were given."""
    found = []
    params = {"pid": pid, "nid": nid, "on": note.get("on", "")}
    rows = g.ro_query("MATCH (m:Note)-[:ABOUT]->(p:Person {id: $pid}) WHERE m.id <> $nid AND m.saved_at IS NULL AND m.job_note IS NULL"
                      " AND m.key IS NOT NULL AND m.on >= $on RETURN properties(m), properties(p) ORDER BY m.on", params).result_set
    for m, person in rows:
        ids = {"pid": pid, "m": m["id"]}
        events = [e for (e,) in g.ro_query("MATCH (:Person {id: $pid})-[:MET_AT {note: $m}]->(e:Event) RETURN e.name", ids).result_set] or [""]
        extra = {f: person[f] for f in FIELDS if m.get(f"set_{f}") and person.get(f"{f}_note") == m["id"]}
        for text, due in g.ro_query("MATCH (f:FollowUp {note: $m}) RETURN f.text, f.due", ids).result_set:
            extra.update(follow_up=text, due=due or "")
        for (done,) in g.ro_query("MATCH (f:FollowUp {done_by: $m}) RETURN f.id", ids).result_set:
            extra["done"] = done
        extra = sorted((k, v) for k, v in extra.items() if v)
        said = {hashlib.sha256(json.dumps([fold(company), fold(event), fold(m["text"]), m["on"], extra]).encode()).hexdigest()[:16]
                for company in (job, job_name) for event in events}
        if m["key"] in said:
            found.append(m["id"])
    return found


def forget_note(g, nid):
    """Undo one note: every relationship and node it made, and any job it ended. What other notes say stays."""
    counts = {"jobs_reopened": 0}
    params = {"nid": nid}
    g.schema.clear()    # a rebuild renumbers field names, and this client may be older than the rebuild
    rows = g.ro_query("MATCH (n:Note {id: $nid}) RETURN n", params).result_set
    if not rows:
        raise ValueError(f"No note with id {nid}.")
    note = rows[0][0].properties
    people = [p for (p,) in g.ro_query("MATCH (:Note {id: $nid})-[:ABOUT]->(p:Person) RETURN p.id", params).result_set]
    # Nodes this note made or touched. Each one that came from a note goes once nothing uses it.
    touched = {i for row in g.ro_query("MATCH (a)-[{note: $nid}]->(b) RETURN id(a), id(b)", params).result_set for i in row}
    touched |= {i for (i,) in g.ro_query("MATCH (x {note: $nid}) RETURN id(x)", params).result_set}
    touched |= {i for (i,) in g.ro_query("MATCH (:Note {id: $nid})-[:ABOUT]->(p) RETURN id(p)", params).result_set}

    started = g.ro_query("MATCH ()-[w:WORKED_AT {note: $nid}]->(c) RETURN w.ended_by, c.id, c.name", params).result_set
    ended_by, job, job_name = started[0] if started else (None, None, None)
    # A later note says this person has this job too, so the job stays and belongs to that note now.
    also = g.ro_query("MATCH (m:Note {job_note: $nid}) RETURN m.id", params).result_set if started else []
    if started and not also:
        also = [[m] for pid in people for m in old_notes_on_job(g, nid, note, pid, job, job_name)]
        g.query("MATCH (m:Note) WHERE m.id IN $ids SET m.job_note = $nid", {**params, "ids": [m for (m,) in also]})
    if also:
        keep = {**params, "keep": also[0][0], "job": job}
        g.query("MATCH (m:Note {job_note: $nid}) SET m.job_note = CASE WHEN m.id = $keep THEN NULL ELSE $keep END", keep)
        g.query("MATCH ()-[w:WORKED_AT {note: $nid}]->() SET w.note = $keep", keep)
        g.query("MATCH ()-[k:KNOWS {via: 'coworkers', note: $nid}]->() SET k.note = $keep", keep)
        g.query("MATCH (c:Company {id: $job, note: $nid}) SET c.note = $keep", keep)
        g.query("MATCH ()-[w:WORKED_AT {ended_by: $nid}]->() SET w.ended_by = $keep", keep)
        g.query("MATCH (n:Note {id: $nid}), (m:Note {id: $keep}) SET m.prev_company = coalesce(n.prev_company, '')", keep)
    elif ended_by:
        # A later note ended the job this note started, so that later note now undoes what this note ended.
        later = {**params, "later": ended_by}
        g.query("MATCH (n:Note {id: $nid}), (m:Note {id: $later}) SET m.prev_company = coalesce(n.prev_company, '')", later)
        g.query("MATCH ()-[w:WORKED_AT {ended_by: $nid}]->() SET w.ended_by = $later", later)
    else:
        if started:
            # If a later note moved on from this company, that note now moves on from the one before.
            next_note = g.ro_query("MATCH (m:Note)-[:ABOUT]->(p:Person) WHERE p.id IN $people AND m.id <> $nid"
                                   " AND m.prev_company = $name AND p.company <> $name AND coalesce(m.saved_at, 0) >= $at"
                                   " RETURN m.id ORDER BY coalesce(m.saved_at, 0) LIMIT 1",
                                   {**params, "people": people, "name": job_name, "at": note.get("saved_at", 0)}).result_set
            if next_note:
                g.query("MATCH (n:Note {id: $nid}), (m:Note {id: $later}) SET m.prev_company = coalesce(n.prev_company, '')",
                        {**params, "later": next_note[0][0]})
            else:
                g.query("MATCH (n:Note {id: $nid})-[:ABOUT]->(p:Person) SET p.company = coalesce(n.prev_company, '')", params)
        counts["jobs_reopened"] = g.query("MATCH ()-[w:WORKED_AT {ended_by: $nid}]->()"
                                          " SET w.current = true, w.ended_on = NULL, w.ended_by = NULL"
                                          " RETURN count(w)", params).result_set[0][0]
    # You still attended an event if someone else's note or the import says you met them there.
    g.query("MATCH (:Me)-[a:ATTENDED {note: $nid}]->(:Event)<-[m:MET_AT]-() WHERE coalesce(m.note, '') <> $nid"
            " WITH a, collect(m.note)[0] AS other SET a.note = other", params)

    g.query("MATCH (f:FollowUp {done_by: $nid}) SET f.open = true, f.done_on = NULL, f.done_by = NULL", params)
    lost_topics = [t for (t,) in g.ro_query("MATCH ()-[:INTO {note: $nid}]->(t:Topic) RETURN t.id", params).result_set]
    counts["facts"] = int(g.query("MATCH ()-[r {note: $nid}]->() DELETE r", params).relationships_deleted)
    for pid in people:
        undo_fields(g, nid, note, pid)
    g.query("MATCH (n:Note {id: $nid}) DETACH DELETE n", params)
    for pid in people:
        if started or counts["jobs_reopened"] or note.get("ended_only"):
            sync_company(g, pid, only_with_jobs=not note.get("ended_only"))
        rebuild_from_notes(g, pid, lost_topics)
    # A person from a note goes with their last note, even if a later note linked a coworker to them.
    g.query("MATCH (p:Person {source: 'agent'}) WHERE id(p) IN $ids AND NOT (:Note)-[:ABOUT]->(p)"
            " WITH p MATCH (p)-[k:KNOWS {via: 'coworkers'}]-() DELETE k", {"ids": sorted(touched)})
    counts["nodes"] = int(g.query("MATCH (x) WHERE id(x) IN $ids AND x.source = 'agent' OPTIONAL MATCH (x)-[r]-()"
                                  " WITH x, count(r) AS used WHERE used = 0 DELETE x",
                                  {"ids": sorted(touched)}).nodes_deleted)
    return {"forgot": "note", "id": nid, **counts}


def rebuild_from_notes(g, pid, lost_topics):
    """After a note goes: a topic it took away stays if another note says it too. Graphs from older versions kept note text in the headline."""
    notes = g.ro_query("MATCH (n:Note)-[:ABOUT]->(:Person {id: $pid}) RETURN n.id, n.text", {"pid": pid}).result_set
    for nid, text in notes:
        for topic in [t for t in lost_topics if TOPIC_RE[t].search(text)]:
            g.query("MATCH (p:Person {id: $pid}), (t:Topic {id: $t}) MERGE (p)-[i:INTO]->(t) ON CREATE SET i.note = $nid",
                    {"pid": pid, "t": topic, "nid": nid})
    g.query("MATCH (p:Person {id: $pid, source: 'agent'}) SET p.headline = coalesce(p.company, ''), p.event_contact = $ec",
            {"pid": pid, "ec": any(EVENT_ROLE_RE.search(text) for _, text in notes)})


def forget_person(g, pid):
    """Delete a person, every note about them, and keep them out of future rebuilds."""
    rows = g.ro_query("MATCH (p:Person {id: $id}) RETURN p.name", {"id": pid}).result_set
    if not rows:
        raise ValueError(f"No person with id {pid}. Use find to look it up.")
    notes = [n for (n,) in g.ro_query("MATCH (n:Note)-[:ABOUT]->(:Person {id: $id}) RETURN n.id", {"id": pid}).result_set]
    # A person from a note has no LinkedIn id yet, so a later import can only know them by name.
    # If the graph has someone else with this name, they are another person: only the id goes on the list.
    namesakes = g.ro_query("MATCH (p:Person {search: $search}) WHERE p.id <> $id RETURN count(p)",
                           {"search": fold(rows[0][0]), "id": pid}).result_set[0][0]
    by_name = {"name:" + fold(rows[0][0])} if pid.startswith("note-") and not namesakes else set()
    # The list first: if the delete stops here, a rebuild still leaves them out, and running forget again finishes it.
    forgotten = forgotten_list(FORGOTTEN)
    FORGOTTEN.parent.mkdir(parents=True, exist_ok=True)
    temp = FORGOTTEN.with_suffix(".tmp")
    temp.write_text(json.dumps(sorted(forgotten | {pid} | by_name), indent=1), encoding="utf-8")
    temp.replace(FORGOTTEN)
    for nid in notes:
        forget_note(g, nid)
    g.query("MATCH (p:Person {id: $id}) DETACH DELETE p", {"id": pid})
    return {"forgot": "person", "name": rows[0][0], "notes": len(notes), "kept_out_of_rebuilds": True}


def forget(g, what, id):
    if what == "note":
        return forget_note(g, id)
    if what == "person":
        return forget_person(g, id)
    raise ValueError("what must be 'note' or 'person'.")


def forget_all_notes(g):
    """Undo every note (the viewer's reset button). Returns how many nodes went."""
    deleted = 0
    for (nid,) in every_row(g, "MATCH (n:Note) WITH n, id(n) AS at WHERE true", "n.id"):
        deleted += forget_note(g, nid)["nodes"] + 1
    return deleted
