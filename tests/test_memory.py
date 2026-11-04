"""End-to-end check of the memory tools on the sample network, in a throwaway graph.

Loads sample_data/ into graph "pb_memory_test", runs remember / find / ask /
forget against it, then deletes the graph. Never touches your "network" graph.

    .venv/bin/python tests/test_memory.py --cloud   # FALKORDB_URL from .env
    .venv/bin/python tests/test_memory.py           # local FalkorDB
"""

import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
TEST_GRAPH = "pb_memory_test"
sys.path.insert(0, str(PROJECT / "spike"))
import memory  # noqa: E402


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        raise SystemExit(1)


def main():
    cloud = ["--cloud"] if "--cloud" in sys.argv else []
    subprocess.run([sys.executable, str(PROJECT / "spike" / "load_graph.py"), "--sample", "--graph", TEST_GRAPH,
                    "--force", *cloud], check=True, capture_output=True)
    if not cloud:
        memory.os.environ["FALKORDB_URL"] = ""
    g, where = memory.connect(TEST_GRAPH)
    memory.FORGOTTEN = Path(tempfile.mkdtemp()) / "forgotten.json"   # keep the real list clean
    print(f"graph {TEST_GRAPH} on {where}")
    try:
        run(g)
    finally:
        g.delete()


def run_names(g):
    """Names in any script: found again, never merged with someone else's."""
    check(memory.fold("Tomás Ortega") == "tomas ortega" and memory.norm("Acme, Inc.") == "acme inc",
          "Latin names fold as before")
    for name in ["王伟", "Иван Петров", "김철수", "محمد علي", "प्रिया शर्मा", "山田 太郎"]:
        r = memory.remember(g, name, new_person=True)
        found = [m["id"] for m in memory.find(g, name, "Person")]
        check(r["person"]["id"] in found, f"find {name}")
        again = memory.remember(g, name, note="met again")
        check(again["status"] == "saved" and again["person"]["id"] == r["person"]["id"], f"remember {name} again: same person")
    check(memory.find(g, "Ивон Петров", "Person")[0]["name"] == "Иван Петров", "find a typo in Cyrillic")

    def job_of(name):
        rows = g.ro_query("MATCH (:Person {name: $n})-[:WORKED_AT {current: true}]->(c) RETURN c.name", {"n": name}).result_set
        return rows[0][0]
    memory.remember(g, "Li Na", company="腾讯", new_person=True)
    memory.remember(g, "Wang Lei", company="三星", new_person=True)
    check(job_of("Li Na") == "腾讯" and job_of("Wang Lei") == "三星", "two non-Latin companies stay two companies")
    memory.remember(g, "Rocket Fan", company="🚀", new_person=True)
    memory.remember(g, "Saucer Fan", company="🛸", new_person=True)
    check(job_of("Rocket Fan") == "🚀" and job_of("Saucer Fan") == "🛸", "two emoji companies stay two companies")

    def event_of(name):
        return g.ro_query("MATCH (:Person {name: $n})-[:MET_AT]->(e) RETURN e.name", {"n": name}).result_set[0][0]
    memory.remember(g, "Event Fan One", event="東京ミートアップ", new_person=True)
    memory.remember(g, "Event Fan Two", event="大阪イベント", new_person=True)
    memory.remember(g, "Event Fan Three", event="🎉", new_person=True)
    check(event_of("Event Fan One") == "東京ミートアップ" and event_of("Event Fan Two") == "大阪イベント"
          and event_of("Event Fan Three") == "🎉", "different events stay different events")

    # Graphs built before this fix hold an empty search value on non-Latin names.
    g.query("CREATE (:Person {id: 'old-1', name: '佐藤 花子', search: ''}), (:Person {id: 'old-2', name: '---', search: ''})")
    memory.setup(g)
    check([m["id"] for m in memory.find(g, "佐藤", "Person")] == ["old-1"], "setup fills in empty search values")


def run(g):
    run_names(g)
    memory.setup(g)
    memory.setup(g)
    check(True, "setup runs twice")

    # The sample has two people with the same name; find them with a typo and no accents.
    twins = g.ro_query("MATCH (p:Person) WITH p.name AS n, collect(p) AS ps WHERE size(ps) = 2"
                       " RETURN n, [x IN ps | x.id] LIMIT 1").result_set
    name, ids = twins[0]
    typo = name.split()[0][:-1] + " " + name.split()[1]
    found = [m for m in memory.find(g, typo, "Person") if m["name"] == name]
    check(len(found) == 2, f"find '{typo}' returns both people named {name}")

    r = memory.remember(g, name, note="talked about agent memory")
    check(r["status"] == "needs_choice" and len(r["candidates"]) == 2, "same name, no company: asks which one")

    # A typo of someone you know asks too, and saves nothing.
    uid, uname = g.ro_query("MATCH (p:Person) WITH p.name AS n, collect(p.id) AS ids WHERE size(ids) = 1"
                            " AND size(split(n, ' ')[0]) >= 5 RETURN ids[0], n LIMIT 1").result_set[0]
    notes = g.ro_query("MATCH (n:Note) RETURN count(n)").result_set[0][0]
    r = memory.remember(g, uname.split()[0][:-1] + " " + " ".join(uname.split()[1:]), note="typo test")
    check(r["status"] == "needs_choice" and uid in [c["id"] for c in r["candidates"]] and
          g.ro_query("MATCH (n:Note) RETURN count(n)").result_set[0][0] == notes, "typo of a known name: asks, saves nothing")

    company = g.ro_query("MATCH (p:Person {id: $id})-[:WORKED_AT {current: true}]->(c) RETURN c.name LIMIT 1",
                         {"id": ids[0]}).result_set[0][0]
    other = g.ro_query("MATCH (p:Person {id: $id})-[:WORKED_AT]->(c) RETURN collect(c.name)", {"id": ids[1]}).result_set[0][0]
    if company not in other:
        r = memory.remember(g, name, company=company, note="wants to co-host a meetup")
        check(r["status"] == "saved" and r["person"]["id"] == ids[0], "same name + their company: picks the right one")

    # Job change: the old job ends, history stays; forgetting the note brings it back.
    pid = ids[1]
    before = g.ro_query("MATCH (:Person {id: $id})-[:WORKED_AT {current: true}]->(c) RETURN c.name", {"id": pid}).result_set
    r = memory.remember(g, person_id=pid, name="", company="Wrenfield Robotics", event="Graph & RAG Meetup",
                        note="moved to Wrenfield Robotics, building agent memory")
    now = g.ro_query("MATCH (:Person {id: $id})-[w:WORKED_AT]->(c) RETURN c.name, w.current", {"id": pid}).result_set
    check(["Wrenfield Robotics", True] in now and all([c, False] in now for (c,) in before),
          "new company: old job ended, new job current")
    check(any(c.startswith("met at Graph & RAG Meetup") for c in r["changes"]), "matches an existing event by name")
    f = memory.forget(g, "note", r["note_id"])
    after = g.ro_query("MATCH (:Person {id: $id})-[:WORKED_AT {current: true}]->(c) RETURN c.name", {"id": pid}).result_set
    gone = g.ro_query("MATCH (c:Company {id: 'wrenfield robotics'}) RETURN count(c)").result_set[0][0]
    check(sorted(after) == sorted(before) and gone == 0 and f["jobs_reopened"] == len(before),
          "forget note: job reopened, new company gone")

    # A brand-new person, then forget them for good.
    r = memory.remember(g, "Ottilie Marchetti", company="Kestrel Graph", event="Rooftop Demo Night",
                        note="runs developer events, into knowledge graphs")
    check(r["status"] == "saved" and r["person"]["new"], "new name: new person")
    check(memory.find(g, "ottilie marcheti", "Person")[0]["id"] == r["person"]["id"], "find new person with a typo")
    f = memory.forget(g, "person", r["person"]["id"])
    left = g.ro_query("MATCH (n) WHERE n.note = $nid RETURN count(n)", {"nid": r["note_id"]}).result_set[0][0]
    check(f["notes"] == 1 and left == 0, "forget person: person, note and new event gone")
    check(r["person"]["id"] in memory.FORGOTTEN.read_text(), "forgotten person kept out of rebuilds")

    # ask is read-only, and a fact can be forgotten by its rel_id.
    a = memory.ask(g, "MATCH (:Me)-[r:CONNECTED]->(p:Person) RETURN r, p.name LIMIT 1")
    check(a["rows"] and a["rows"][0][0]["type"] == "CONNECTED", "ask returns rows")
    try:
        memory.ask(g, "MATCH (p:Person) DETACH DELETE p")
        check(False, "ask refuses writes")
    except Exception:
        check(True, "ask refuses writes")
    f = memory.forget(g, "fact", a["rows"][0][0]["rel_id"])
    check(f["fact"].endswith(a["rows"][0][1]), "forget fact by rel_id")

    total = g.ro_query("MATCH (n:Note) RETURN count(n)").result_set[0][0]
    check(memory.forget_all_notes(g) >= total and
          g.ro_query("MATCH (n:Note) RETURN count(n)").result_set[0][0] == 0, "reset removes every note")


if __name__ == "__main__":
    main()
