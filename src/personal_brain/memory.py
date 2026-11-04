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
import uuid
from datetime import date
from pathlib import Path

from falkordb import FalkorDB, Edge, Node, Path as GraphPath

from personal_brain.load_graph import EVENT_ROLE_RE, FORGOTTEN, TOPIC_RE, create_search_indexes, fold, norm

from personal_brain.paths import HOME, env_file_url
MAX_ROWS = 50        # rows ask() hands back to the agent
TIMEOUT_MS = 5000    # longest an ask() query may run

SCHEMA = """Nodes: Me (you), Person, Company, School, Skill, Topic, Event, Investor, City, Note.
Every node has id and name. Person: headline, title, company, location, connected_on, messages.
Company: well_funded, funding, employees. Event: date, upcoming, summary, url.
Edges:
(Me)-[:CONNECTED {on}]->(Person)
(Person)-[:WORKED_AT {title, start, end, current}]->(Company)
(Person)-[:STUDIED_AT]->(School), (Person)-[:HAS_SKILL]->(Skill), (Person)-[:HAS_EXPERIENCE {years}]->(Skill)
(Person)-[:INTO]->(Topic), (Person)-[:LIVES_IN]->(City)
(Person)-[:KNOWS {via, place}]-(Person)   inferred: coworkers or classmates at the same time
(Me)-[:ATTENDED]->(Event), (Person)-[:MET_AT]->(Event)
(Company)-[:FUNDED_BY {lead, round}]->(Investor)
(Event {upcoming: true})-[:HOSTED_BY {via}]->(Person or Company), (Event)-[:ABOUT]->(Topic), (Event)-[:HELD_IN]->(City)
(Me)-[:WROTE]->(Note {text, on})-[:ABOUT]->(Person), (Me)-[:MET {on, note}]->(Person)
Topics: AI Agents, Agent Memory, Knowledge Graphs, RAG & Search, LLMs & GenAI, Developer Relations,
Community & Events, Partnerships, Investing, Founder, Podcast & Media."""


def connect(graph=None):
    """The graph on FalkorDB Cloud (FALKORDB_URL in the environment or .env), else local FalkorDB."""
    url = os.environ["FALKORDB_URL"] if "FALKORDB_URL" in os.environ else env_file_url()
    db = FalkorDB.from_url(url) if url else FalkorDB(host="localhost", port=6379)
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


# ---------- find ----------

def search_query(text):
    """Full-text query: every word must match, as a prefix or with 1 typo ("prya" finds Priya)."""
    return " ".join(f"({w}*|%{w}%)" if len(w) >= 4 else f"{w}*" for w in fold(text).split())


# What to show next to each match, so the agent (and you) can tell two Priyas apart.
FIND = {
    "Person": "OPTIONAL MATCH (node)-[:WORKED_AT {current: true}]->(c:Company) "
              "WITH node, score, collect(c.name) AS now "
              "RETURN node.id, node.name, score, {headline: node.headline, works_at: now, connected_on: node.connected_on}",
    "Company": "OPTIONAL MATCH (node)<-[:WORKED_AT {current: true}]-(p:Person) "
               "WITH node, score, count(p) AS people "
               "RETURN node.id, node.name, score, {people_you_know_there: people, funding: node.funding}",
    "Event": "RETURN node.id, node.name, score, {date: node.date, upcoming: node.upcoming}",
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
                          f"{FIND[lab]} ORDER BY score DESC LIMIT {int(limit)}", {"label": lab, "q": query}).result_set
        out += [{"label": lab, "id": i, "name": name, "score": round(score, 2),
                 **{k: v for k, v in detail.items() if v not in (None, "", [])}} for i, name, score, detail in rows]
    return sorted(out, key=lambda m: -m["score"])[:limit]


# ---------- remember ----------

def one_edit(a, b):
    """True when b is a with at most one letter added, dropped or changed."""
    if abs(len(a) - len(b)) > 1:
        return False
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    return a[i + 1:] == b[i + 1:] or a[i:] == b[i + 1:] or a[i + 1:] == b[i:]


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
        at = [m for m in same if any(c == key or c.startswith(key + " ") or key.startswith(c + " ")
                                     for c in jobs.get(m["id"], []))]
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


def repeat_key(company, event, text, day):
    """Two notes with this key about one person are one meeting saved twice, from an agent's retry."""
    return hashlib.sha256(json.dumps([fold(company), fold(event), fold(text), day]).encode()).hexdigest()[:16]


def saved_before(g, key, person, company, event, note):
    """The note this same call saved before, as (note id, person id, person name), or None."""
    ids = [person["id"]]
    if person["new"] and (company or event or note):
        # A retry with new_person=true looks for a person it may have just made.
        ids = [m["id"] for m in find(g, person["name"], "Person", 20) if fold(m["name"]) == fold(person["name"])]
    rows = g.ro_query("MATCH (n:Note {key: $key})-[:ABOUT]->(p:Person) WHERE p.id IN $ids RETURN n.id, p.id, p.name LIMIT 1",
                      {"key": key, "ids": ids}).result_set
    return rows[0] if rows else None


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


def remember(g, name, company="", event="", note="", person_id="", new_person=False, on="", ended_jobs=None):
    """Save one meeting: who, where they work, where you met, what you talked about, and when."""
    name, company, event, note = (" ".join((x or "").split()) for x in (name, company, event, note))
    if not name and not person_id:
        raise ValueError("Give a name, or a person_id from find.")
    day = meeting_date(on)
    person, candidates = resolve(g, name, company, person_id, new_person)
    if person is None:
        return {"status": "needs_choice", "candidates": candidates,
                "message": f"Not sure who {name} is: these people have the same or a similar name. Ask the user, "
                           "then call remember again with person_id for one of them, or new_person=true for someone new."}

    text = note or " ".join(x for x in [f"Met {person['name']}", company and f"from {company}", event and f"at {event}"] if x)
    key = repeat_key(company, event, text, day)
    before = saved_before(g, key, person, company, event, note)
    if before:
        return {"status": "already_saved", "note_id": before[0], "person": {"id": before[1], "name": before[2], "new": False},
                "changes": [], "ms": 0.0, "undo": f"personal-brain forget note {before[0]}"}

    pid = person["id"]
    cid = (norm(company) or company) if company else ""    # an emoji has no words: use it as it is
    now = g.ro_query("MATCH (:Person {id: $pid})-[:WORKED_AT {current: true}]->(c:Company) RETURN c.id, c.name",
                     {"pid": pid}).result_set
    new_job = bool(company) and cid not in {c for c, _ in now}
    if new_job and ended_jobs is None and len(now) > 1:
        jobs = [cname for _, cname in now]
        return {"status": "needs_jobs", "person": person, "current_jobs": jobs,
                "message": f"{person['name']} has {len(jobs)} current jobs: {', '.join(jobs)}. Ask the user which ones ended, then call "
                           "remember again with ended_jobs (an empty list if none ended)."}
    ended = [c for c, _ in now] if ended_jobs is None else jobs_that_ended(now, ended_jobs, person["name"])

    nid = "note-" + uuid.uuid4().hex[:12]
    changes, ms = [], 0.0

    def q(cypher, **params):
        nonlocal ms
        r = g.query(cypher, {"nid": nid, "pid": pid, "day": day, **params})
        ms += r.run_time_ms
        return r.result_set

    q("MATCH (me:Me) CREATE (me)-[:WROTE]->(:Note {id: $nid, text: $text, on: $day, source: 'agent', key: $key})", text=text, key=key)
    if person["new"]:
        # The note's text stays on the Note, so forgetting the note removes it. The job step below sets the company.
        q("CREATE (:Person {id: $pid, name: $name, search: $search, company: '', headline: '',"
          " source: 'agent', note: $nid, messages: 0, enriched: false, event_contact: $ec})",
          name=person["name"], search=fold(person["name"]), ec=bool(EVENT_ROLE_RE.search(note)))
        changes.append(f"new person: {person['name']}")
    q("MATCH (me:Me), (n:Note {id: $nid}), (p:Person {id: $pid})"
      " CREATE (n)-[:ABOUT]->(p), (me)-[:MET {on: $day, note: $nid}]->(p)")

    if company:
        q("MERGE (c:Company {id: $cid})"
          " ON CREATE SET c.name = $company, c.search = $search, c.source = 'agent', c.note = $nid",
          cid=cid, company=company, search=fold(company))
        if new_job:
            # A new job: end the old ones instead of deleting them, so the history stays.
            q("MATCH (p:Person {id: $pid}), (n:Note {id: $nid}) SET n.prev_company = coalesce(p.company, '')")
            q("MATCH (p:Person {id: $pid})-[w:WORKED_AT {current: true}]->(c:Company) WHERE c.id IN $ended"
              " SET w.current = false, w.ended_on = $day, w.ended_by = $nid", ended=ended)
            q("MATCH (p:Person {id: $pid}), (c:Company {id: $cid})"
              " CREATE (p)-[:WORKED_AT {current: true, since: $day, source: 'agent', note: $nid}]->(c)"
              " SET p.company = $company, p.headline = CASE WHEN p.source = 'agent' THEN $company ELSE p.headline END",
              cid=cid, company=company)
            names = ", ".join(n for c, n in now if c in ended)
            changes.append(f"works at {company} now" + (f" (ended: {names})" if names else ""))

    if event:
        same = [m for m in find(g, event, "Event", 5) if fold(m["name"]) == fold(event)]
        eid = same[0]["id"] if same else "note-" + (norm(event) or event).replace(" ", "-")
        q("MERGE (e:Event {id: $eid})"
          " ON CREATE SET e.name = $event, e.search = $search, e.date = $day, e.confirmed = true,"
          " e.source = 'agent', e.note = $nid"
          " WITH e MATCH (me:Me), (p:Person {id: $pid})"
          " MERGE (me)-[a:ATTENDED]->(e) ON CREATE SET a.note = $nid"
          " CREATE (p)-[:MET_AT {on: $day, inferred: false, note: $nid}]->(e)",
          eid=eid, event=event, search=fold(event))
        changes.append(f"met at {same[0]['name'] if same else event}")

    for topic in [t for t, rx in TOPIC_RE.items() if rx.search(note)]:
        q("MATCH (p:Person {id: $pid}), (t:Topic {id: $t}) MERGE (p)-[i:INTO]->(t) ON CREATE SET i.note = $nid", t=topic)
        changes.append(f"into {topic}")

    return {"status": "saved", "note_id": nid, "person": person, "changes": changes, "ms": round(ms, 2),
            "undo": f"personal-brain forget note {nid}"}


# ---------- ask ----------

def plain(v):
    """FalkorDB values as JSON."""
    if isinstance(v, Node):
        return {"label": (v.labels or [""])[0], **{k: x for k, x in v.properties.items() if k not in ("search", "key")}}
    if isinstance(v, Edge):
        return {"type": v.relation, **v.properties}
    if isinstance(v, GraphPath):
        return {"nodes": [plain(n) for n in v.nodes()], "edges": [plain(e) for e in v.edges()]}
    if isinstance(v, list):
        return [plain(x) for x in v]
    if isinstance(v, dict):
        return {k: plain(x) for k, x in v.items()}
    return v


def ask(g, cypher, params=None):
    """A read-only Cypher query (GRAPH.RO_QUERY) with a time limit. Writes are refused by FalkorDB itself."""
    r = g.ro_query(cypher, params or {}, timeout=TIMEOUT_MS)
    return {"columns": [h[1] for h in r.header], "rows": [[plain(v) for v in row] for row in r.result_set[:MAX_ROWS]],
            "row_count": len(r.result_set), "truncated": len(r.result_set) > MAX_ROWS, "ms": round(r.run_time_ms, 2)}


# ---------- forget ----------

def forget_note(g, nid):
    """Undo one note: every relationship and node it made, and any job it ended. What other notes say stays."""
    counts = {"jobs_reopened": 0}
    params = {"nid": nid}
    if not g.ro_query("MATCH (n:Note {id: $nid}) RETURN n", params).result_set:
        raise ValueError(f"No note with id {nid}.")
    people = [p for (p,) in g.ro_query("MATCH (:Note {id: $nid})-[:ABOUT]->(p:Person) RETURN p.id", params).result_set]
    # Nodes this note made or touched. Each one that came from a note goes once nothing uses it.
    touched = {i for row in g.ro_query("MATCH (a)-[{note: $nid}]->(b) RETURN id(a), id(b)", params).result_set for i in row}
    touched |= {i for (i,) in g.ro_query("MATCH (x {note: $nid}) RETURN id(x)", params).result_set}
    touched |= {i for (i,) in g.ro_query("MATCH (:Note {id: $nid})-[:ABOUT]->(p) RETURN id(p)", params).result_set}

    started = g.ro_query("MATCH ()-[w:WORKED_AT {note: $nid}]->() RETURN w.ended_by", params).result_set
    if started and started[0][0]:
        # A later note ended the job this note started, so that later note now undoes what this note ended.
        later = {**params, "later": started[0][0]}
        g.query("MATCH (n:Note {id: $nid}), (m:Note {id: $later}) SET m.prev_company = coalesce(n.prev_company, '')", later)
        g.query("MATCH ()-[w:WORKED_AT {ended_by: $nid}]->() SET w.ended_by = $later", later)
    else:
        if started:
            g.query("MATCH (n:Note {id: $nid})-[:ABOUT]->(p:Person) SET p.company = coalesce(n.prev_company, '')", params)
        counts["jobs_reopened"] = g.query("MATCH ()-[w:WORKED_AT {ended_by: $nid}]->()"
                                          " SET w.current = true, w.ended_on = NULL, w.ended_by = NULL"
                                          " RETURN count(w)", params).result_set[0][0]
    # You still attended an event if someone else's note or the import says you met them there.
    g.query("MATCH (:Me)-[a:ATTENDED {note: $nid}]->(:Event)<-[m:MET_AT]-() WHERE coalesce(m.note, '') <> $nid"
            " WITH a, collect(m.note)[0] AS other SET a.note = other", params)

    counts["facts"] = int(g.query("MATCH ()-[r {note: $nid}]->() DELETE r", params).relationships_deleted)
    g.query("MATCH (n:Note {id: $nid}) DETACH DELETE n", params)
    for pid in people:
        rebuild_from_notes(g, pid)
    counts["nodes"] = int(g.query("MATCH (x) WHERE id(x) IN $ids AND x.source = 'agent' OPTIONAL MATCH (x)-[r]-()"
                                  " WITH x, count(r) AS used WHERE used = 0 DELETE x",
                                  {"ids": sorted(touched)}).nodes_deleted)
    return {"forgot": "note", "id": nid, **counts}


def rebuild_from_notes(g, pid):
    """After a note goes: what the person's other notes say stays true. Graphs from older versions kept note text in the headline."""
    notes = g.ro_query("MATCH (n:Note)-[:ABOUT]->(:Person {id: $pid}) RETURN n.id, n.text", {"pid": pid}).result_set
    for nid, text in notes:
        for topic in [t for t, rx in TOPIC_RE.items() if rx.search(text)]:
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
    for nid in notes:
        forget_note(g, nid)
    g.query("MATCH (p:Person {id: $id}) DETACH DELETE p", {"id": pid})
    forgotten = set(json.loads(FORGOTTEN.read_text())) if FORGOTTEN.exists() else set()
    FORGOTTEN.parent.mkdir(parents=True, exist_ok=True)
    FORGOTTEN.write_text(json.dumps(sorted(forgotten | {pid}), indent=1))
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
    for (nid,) in g.ro_query("MATCH (n:Note) RETURN n.id").result_set:
        deleted += forget_note(g, nid)["nodes"] + 1
    return deleted
