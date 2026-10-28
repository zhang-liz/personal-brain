"""Agent memory on the Personal Brain graph: remember, find, ask and forget.

Shared by the MCP server (mcp_server.py) and the viewer's live API (server.py).
Every write hangs off a Note node, so each fact can be traced to the note that
made it, and forgotten with it. People you forget go in data/forgotten.json, so
a rebuild (load_graph.py) leaves them out.
"""

import json
import os
import uuid
from datetime import date
from pathlib import Path

from falkordb import FalkorDB, Edge, Node, Path as GraphPath

from load_graph import EVENT_ROLE_RE, FORGOTTEN, TOPIC_RE, create_search_indexes, fold, norm

PROJECT = Path(__file__).resolve().parent.parent
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
    env_file = PROJECT / ".env"
    env = env_file.read_text().splitlines() if env_file.exists() else []
    url = os.environ["FALKORDB_URL"] if "FALKORDB_URL" in os.environ else next(
        (l.split("=", 1)[1].strip() for l in env if l.startswith("FALKORDB_URL=")), "")
    db = FalkorDB.from_url(url) if url else FalkorDB(host="localhost", port=6379)
    return db.select_graph(graph or os.environ.get("PB_GRAPH", "network")), ("FalkorDB Cloud" if url else "local FalkorDB")


def setup(g):
    """You, and full-text search on names. Safe to run on every start: it only fills in what is missing."""
    g.query("MERGE (me:Me {id: 'me'}) ON CREATE SET me.name = 'Me'")   # an empty graph works too
    g.query("UNWIND $topics AS t MERGE (:Topic {id: t, name: t})", {"topics": list(TOPIC_RE)})
    for label in ["Person", "Company", "Event"]:
        # Graphs built before search existed: add the folded name, 5,000 nodes at a time.
        while rows := g.query(f"MATCH (n:{label}) WHERE n.search IS NULL RETURN id(n), n.name LIMIT 5000").result_set:
            g.query("UNWIND $rows AS r MATCH (n) WHERE id(n) = r[0] SET n.search = r[1]",
                    {"rows": [[i, fold(name)] for i, name in rows]})
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


def remember(g, name, company="", event="", note="", person_id="", new_person=False, today=None):
    """Save one meeting: who, where they work, where you met, what you talked about."""
    name, company, event, note = (" ".join((x or "").split()) for x in (name, company, event, note))
    if not name and not person_id:
        raise ValueError("Give a name, or a person_id from find.")
    person, candidates = resolve(g, name, company, person_id, new_person)
    if person is None:
        return {"status": "needs_choice", "candidates": candidates,
                "message": f"Not sure who {name} is: these people have the same or a similar name. Ask the user, "
                           "then call remember again with person_id for one of them, or new_person=true for someone new."}

    today = today or date.today().isoformat()
    nid = "note-" + uuid.uuid4().hex[:12]
    pid, changes, ms = person["id"], [], 0.0

    def q(cypher, **params):
        nonlocal ms
        r = g.query(cypher, {"nid": nid, "pid": pid, "today": today, **params})
        ms += r.run_time_ms
        return r.result_set

    text = note or " ".join(x for x in [f"Met {person['name']}", company and f"from {company}", event and f"at {event}"] if x)
    q("MATCH (me:Me) CREATE (me)-[:WROTE]->(:Note {id: $nid, text: $text, on: $today, source: 'agent'})", text=text)
    if person["new"]:
        q("CREATE (:Person {id: $pid, name: $name, search: $search, company: $company, headline: $headline,"
          " source: 'agent', note: $nid, messages: 0, enriched: false, event_contact: $ec})",
          name=person["name"], search=fold(person["name"]), company=company,
          headline=" · ".join(x for x in [company, note] if x)[:120], ec=bool(EVENT_ROLE_RE.search(note)))
        changes.append(f"new person: {person['name']}")
    q("MATCH (me:Me), (n:Note {id: $nid}), (p:Person {id: $pid})"
      " CREATE (n)-[:ABOUT]->(p), (me)-[:MET {on: $today, note: $nid}]->(p)")

    if company:
        cid = norm(company)
        q("MERGE (c:Company {id: $cid})"
          " ON CREATE SET c.name = $company, c.search = $search, c.source = 'agent', c.note = $nid",
          cid=cid, company=company, search=fold(company))
        now = q("MATCH (p:Person {id: $pid})-[w:WORKED_AT {current: true}]->(c:Company) RETURN c.id, c.name")
        if cid not in {c for c, _ in now}:
            # A new job: end the old one instead of deleting it, so the history stays.
            old = q("MATCH (p:Person {id: $pid})-[w:WORKED_AT {current: true}]->(:Company)"
                    " SET w.current = false, w.ended_on = $today, w.ended_by = $nid"
                    " WITH p, w MATCH (n:Note {id: $nid}) SET n.prev_company = p.company RETURN count(w)")
            q("MATCH (p:Person {id: $pid}), (c:Company {id: $cid})"
              " CREATE (p)-[:WORKED_AT {current: true, since: $today, source: 'agent', note: $nid}]->(c)"
              " SET p.company = $company", cid=cid, company=company)
            changes.append(f"works at {company} now" + (f" (ended: {', '.join(n for _, n in now)})" if old and old[0][0] else ""))

    if event:
        same = [m for m in find(g, event, "Event", 5) if fold(m["name"]) == fold(event)]
        eid = same[0]["id"] if same else "note-" + norm(event).replace(" ", "-")
        q("MERGE (e:Event {id: $eid})"
          " ON CREATE SET e.name = $event, e.search = $search, e.date = $today, e.confirmed = true,"
          " e.source = 'agent', e.note = $nid"
          " WITH e MATCH (me:Me), (p:Person {id: $pid})"
          " MERGE (me)-[a:ATTENDED]->(e) ON CREATE SET a.note = $nid"
          " CREATE (p)-[:MET_AT {on: $today, inferred: false, note: $nid}]->(e)",
          eid=eid, event=event, search=fold(event))
        changes.append(f"met at {same[0]['name'] if same else event}")

    for topic in [t for t, rx in TOPIC_RE.items() if rx.search(note)]:
        q("MATCH (p:Person {id: $pid}), (t:Topic {id: $t}) MERGE (p)-[i:INTO]->(t) ON CREATE SET i.note = $nid", t=topic)
        changes.append(f"into {topic}")

    return {"status": "saved", "note_id": nid, "person": person, "changes": changes, "ms": round(ms, 2)}


# ---------- ask ----------

def plain(v):
    """FalkorDB values as JSON. Relationships keep their rel_id, so forget(what='fact') can delete one."""
    if isinstance(v, Node):
        return {"label": (v.labels or [""])[0], **{k: x for k, x in v.properties.items() if k != "search"}}
    if isinstance(v, Edge):
        return {"type": v.relation, "rel_id": v.id, **v.properties}
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
    """Undo one note: every relationship and node it made, and any job it ended."""
    counts = {}
    params = {"nid": nid}
    if not g.ro_query("MATCH (n:Note {id: $nid}) RETURN n", params).result_set:
        raise ValueError(f"No note with id {nid}.")
    g.query("MATCH (n:Note {id: $nid})-[:ABOUT]->(p:Person) WHERE n.prev_company IS NOT NULL"
            " SET p.company = n.prev_company", params)
    counts["jobs_reopened"] = g.query("MATCH ()-[w:WORKED_AT {ended_by: $nid}]->()"
                                      " SET w.current = true, w.ended_on = NULL, w.ended_by = NULL"
                                      " RETURN count(w)", params).result_set[0][0]
    counts["facts"] = int(g.query("MATCH ()-[r {note: $nid}]->() DELETE r", params).relationships_deleted)
    g.query("MATCH (n:Note {id: $nid}) DETACH DELETE n", params)
    # Nodes this note created and nothing else uses now.
    counts["nodes"] = int(g.query("MATCH (x {note: $nid}) OPTIONAL MATCH (x)-[r]-() WITH x, count(r) AS used"
                              " WHERE used = 0 DELETE x", params).nodes_deleted)
    return {"forgot": "note", "id": nid, **counts}


def forget_fact(g, rel_id):
    """Delete one relationship, by the rel_id that ask() returns."""
    rows = g.query("MATCH (a)-[r]->(b) WHERE id(r) = $id WITH a, r, b, type(r) AS t, a.name AS an, b.name AS bn"
                   " DELETE r RETURN an, t, bn", {"id": int(rel_id)}).result_set
    if not rows:
        raise ValueError(f"No relationship with rel_id {rel_id}.")
    a, t, b = rows[0]
    return {"forgot": "fact", "fact": f"{a} {t} {b}"}


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
    FORGOTTEN.parent.mkdir(exist_ok=True)
    FORGOTTEN.write_text(json.dumps(sorted(forgotten | {pid}), indent=1))
    return {"forgot": "person", "name": rows[0][0], "notes": len(notes), "kept_out_of_rebuilds": True}


def forget(g, what, id):
    if what == "note":
        return forget_note(g, id)
    if what == "fact":
        return forget_fact(g, id)
    if what == "person":
        return forget_person(g, id)
    raise ValueError("what must be 'note', 'fact' or 'person'.")


def forget_all_notes(g):
    """Undo every note (the viewer's reset button). Returns how many nodes went."""
    deleted = 0
    for (nid,) in g.ro_query("MATCH (n:Note) RETURN n.id").result_set:
        deleted += forget_note(g, nid)["nodes"] + 1
    return deleted
