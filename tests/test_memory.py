"""End-to-end check of the memory tools on the sample network, in a throwaway graph.

Loads sample_data/ into graph "pb_memory_test", runs remember / find / ask /
forget against it, then deletes the graph. Never touches your "network" graph.

    .venv/bin/python tests/test_memory.py --cloud   # FALKORDB_URL from .env
    .venv/bin/python tests/test_memory.py           # local FalkorDB
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
TEST_GRAPH = "pb_memory_test"
REBUILD_HOME = tempfile.mkdtemp()   # keeps the real data folder out of rebuild tests
from personal_brain import memory  # noqa: E402


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        raise SystemExit(1)


def main():
    cloud = ["--cloud"] if "--cloud" in sys.argv else []
    subprocess.run([sys.executable, "-m", "personal_brain.load_graph", "--sample", "--graph", TEST_GRAPH,
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


def run_forget(g):
    """Forget leaves nothing behind, and does not take away what another note still says."""
    def one(cypher, **params):
        rows = g.ro_query(cypher, params).result_set
        return rows[0][0] if rows else None

    # A new person with two notes: the text of a forgotten note stays nowhere.
    a = memory.remember(g, "Forget Fiona", company="Fiona Co", note="private text one", new_person=True)
    pid = a["person"]["id"]
    b = memory.remember(g, "", person_id=pid, note="private text two")
    memory.forget(g, "note", a["note_id"])
    props = g.ro_query("MATCH (p:Person {id: $id}) RETURN p", {"id": pid}).result_set[0][0].properties
    check("private text one" not in str(props), "forgotten note's text stays nowhere on the person")
    check(not props.get("company"), "forgotten note's company stays nowhere on the person")
    memory.forget(g, "note", b["note_id"])
    check(one("MATCH (p:Person {id: $id}) RETURN count(p)", id=pid) == 0, "last note forgotten: new person gone")
    check(one("MATCH (c:Company {name: 'Fiona Co'}) RETURN count(c)") == 0, "last note forgotten: new company gone")

    # Three job changes: forget them in any order and the current job stays right.
    def jobs(person):
        rows = g.ro_query("MATCH (p:Person {id: $id}) OPTIONAL MATCH (p)-[:WORKED_AT {current: true}]->(c)"
                          " RETURN p.company, collect(c.name)", {"id": person}).result_set
        return rows[0] if rows else None
    j1 = memory.remember(g, "Job Hopper", company="Alpha Labs", new_person=True)
    pid = j1["person"]["id"]
    j2 = memory.remember(g, "", person_id=pid, company="Beta Labs")
    j3 = memory.remember(g, "", person_id=pid, company="Gamma Labs")
    memory.forget(g, "note", j2["note_id"])
    check(jobs(pid) == ["Gamma Labs", ["Gamma Labs"]], "forget a middle job: newest job stays the only current one")
    memory.forget(g, "note", j3["note_id"])
    check(jobs(pid) == ["Alpha Labs", ["Alpha Labs"]], "forget the newest job: the job before it comes back")
    j4 = memory.remember(g, "", person_id=pid, company="Delta Labs")
    memory.forget(g, "note", j1["note_id"])
    check(jobs(pid) == ["Delta Labs", ["Delta Labs"]], "forget the first job: newest job stays")
    memory.forget(g, "note", j4["note_id"])
    check(jobs(pid) is None, "all job notes forgotten: new person gone")

    # Someone imported with a company but no job history gets that company back.
    g.query("CREATE (:Person {id: 'imported-1', name: 'Imported Ines', search: 'imported ines', company: 'Old Co', headline: 'CEO'})")
    n = memory.remember(g, "", person_id="imported-1", company="New Co", note="private text three")
    memory.forget(g, "note", n["note_id"])
    check(jobs("imported-1") == ["Old Co", []], "imported person: company comes back")
    check(one("MATCH (p:Person {id: 'imported-1'}) RETURN p.headline") == "CEO", "imported person: headline stays")

    # Two people met at one new event: it stays until the last note about it goes.
    e1 = memory.remember(g, "Meetup Mia", event="Shared Meetup", new_person=True)
    e2 = memory.remember(g, "Meetup Max", event="Shared Meetup", new_person=True)
    attended = "MATCH (:Me)-[a:ATTENDED]->(:Event {name: 'Shared Meetup'}) RETURN count(a)"
    memory.forget(g, "note", e1["note_id"])
    check(one(attended) == 1, "first note forgotten: you still attended the shared event")
    memory.forget(g, "note", e2["note_id"])
    check(one("MATCH (e:Event {name: 'Shared Meetup'}) RETURN count(e)") == 0, "last note forgotten: shared event gone")

    # Two notes say the same topic: it stays until the last one goes.
    t1 = memory.remember(g, "Topic Tess", note="runs developer events, into agent memory", new_person=True)
    pid = t1["person"]["id"]
    t2 = memory.remember(g, "", person_id=pid, note="more about agent memory")
    topic = "MATCH (:Person {id: $id})-[i:INTO]->(:Topic {name: 'Agent Memory'}) RETURN count(i)"
    memory.forget(g, "note", t1["note_id"])
    check(one(topic, id=pid) == 1, "first note forgotten: topic from the second note stays")
    check(one("MATCH (p:Person {id: $id}) RETURN p.event_contact", id=pid) is False, "forgotten note no longer marks an event contact")
    memory.forget(g, "note", t2["note_id"])

    # A person from the import stays when every note about them goes.
    uid = g.ro_query("MATCH (:Me)-[:CONNECTED]->(p:Person) RETURN p.id LIMIT 1").result_set[0][0]
    n = memory.remember(g, "", person_id=uid, note="short note")
    memory.forget(g, "note", n["note_id"])
    check(one("MATCH (p:Person {id: $id}) RETURN count(p)", id=uid) == 1, "imported person stays after their notes go")


def refused(fn, *args, **kwargs):
    """The message when fn refuses with a ValueError, else ""."""
    try:
        fn(*args, **kwargs)
    except ValueError as e:
        return str(e)
    return ""


def run_dates(g):
    """A meeting keeps the day it happened, not the day you said so."""
    r = memory.remember(g, "Dated Dana", company="Date Co", event="Past Meetup", note="old chat", on="2026-01-15", new_person=True)
    pid, nid = r["person"]["id"], r["note_id"]
    row = g.ro_query("MATCH (me:Me)-[m:MET]->(p:Person {id: $id})-[a:MET_AT]->(e:Event), (n:Note {id: $nid})"
                     " RETURN n.on, m.on, a.on, e.date", {"id": pid, "nid": nid}).result_set[0]
    check(row == ["2026-01-15"] * 4, "meeting date reaches the note, the links and a new event")
    r = memory.remember(g, "", person_id=pid, company="Later Co", on="2026-02-01")
    row = g.ro_query("MATCH (:Person {id: $id})-[w:WORKED_AT]->(c:Company) RETURN c.name, w.since, w.ended_on ORDER BY c.name",
                     {"id": pid}).result_set
    check(row == [["Date Co", "2026-01-15", "2026-02-01"], ["Later Co", "2026-02-01", None]], "job dates use the meeting date")
    r = memory.remember(g, "", person_id=pid, note="no date given")
    on = g.ro_query("MATCH (n:Note {id: $id}) RETURN n.on", {"id": r["note_id"]}).result_set[0][0]
    check(on == memory.date.today().isoformat(), "no date: today")
    notes = g.ro_query("MATCH (n:Note) RETURN count(n)").result_set[0][0]
    check("last week" in refused(memory.remember, g, "", person_id=pid, note="x", on="last week"), "a date in words is refused")
    check("future" in refused(memory.remember, g, "", person_id=pid, note="x", on="2999-01-01"), "a future date is refused")
    check(g.ro_query("MATCH (n:Note) RETURN count(n)").result_set[0][0] == notes, "a refused date saves nothing")


class BreaksAfter:
    """The graph, with writes that start to fail after some number of them, as when the connection drops."""

    def __init__(self, graph, writes, also_cleanup=True):
        self.graph, self.left, self.also_cleanup, self.broken = graph, writes, also_cleanup, False

    def __getattr__(self, name):
        return getattr(self.graph, name)

    def query(self, *args, **kwargs):
        if self.broken and not self.also_cleanup:
            raise ConnectionError("connection dropped")
        if self.left == 0 and not self.broken:
            self.broken = True
            raise ConnectionError("connection dropped")
        self.left -= 1
        return self.graph.query(*args, **kwargs)


def run_half_saved(g):
    """A save that stops halfway leaves nothing behind, and a retry saves all of it."""
    def count(cypher, **params):
        return g.ro_query(cypher, params).result_set[0][0]

    call = dict(company="Half Co", event="Half Meetup", note="half saved words", on="2026-04-01")
    for writes in range(1, 8):
        name = f"Halfway Hal {'abcdefgh'[writes]}"
        try:
            memory.remember(BreaksAfter(g, writes), name, **call)
            check(False, f"the write after {writes} writes fails")
        except ConnectionError:
            pass
        check(count("MATCH (n:Note {text: 'half saved words'}) RETURN count(n)") == 0, f"stops after {writes} writes: no note stays")
        check(count("MATCH (p:Person {name: $n}) RETURN count(p)", n=name) == 0, f"stops after {writes} writes: no person stays")

    # The connection stays down, so cleaning up fails too. The retry must still save everything.
    try:
        memory.remember(BreaksAfter(g, 2, also_cleanup=False), "Stuck Stu", **call)
        check(False, "a save with a dead connection fails")
    except ConnectionError:
        pass
    r = memory.remember(g, "Stuck Stu", **call)
    check(r["status"] == "saved", "a retry after a dead connection saves, and does not say already_saved")
    check(count("MATCH (p:Person {name: 'Stuck Stu'}) RETURN count(p)") == 1, "the half-saved person does not stay as a second person")
    check(count("MATCH (n:Note {saving: true}) RETURN count(n)") == 0, "no half-saved note stays")
    row = g.ro_query("MATCH (p:Person {id: $id})-[:WORKED_AT {current: true}]->(c), (p)-[:MET_AT]->(e) RETURN c.name, e.name",
                     {"id": r["person"]["id"]}).result_set
    check(row == [["Half Co", "Half Meetup"]], "the retry saves the job and the event")
    check(memory.remember(g, "Stuck Stu", **call)["status"] == "already_saved", "after a full save, a repeat is found")
    g.query("MATCH (n:Note {text: 'half saved words'}) DETACH DELETE n")
    g.query("MATCH (p:Person {name: 'Stuck Stu'}) DETACH DELETE p")


def run_repeats(g):
    """An agent that retries a call must not save the meeting twice."""
    def count(cypher, **params):
        return g.ro_query(cypher, params).result_set[0][0]

    call = dict(company="Repeat Co", event="Repeat Meetup", note="same words", on="2026-03-01")
    first = memory.remember(g, "Repeat Rae", new_person=True, **call)
    pid = first["person"]["id"]
    again = memory.remember(g, "Repeat Rae", **call)
    check(again["status"] == "already_saved" and again["note_id"] == first["note_id"] and again["person"]["id"] == pid,
          "same call again: says it is saved, gives the same note")
    check("undo" in again and again["changes"] == [], "same call again: gives the undo command, changes nothing")
    retry = memory.remember(g, "Repeat Rae", new_person=True, **call)
    check(retry["status"] == "already_saved" and retry["person"]["id"] == pid, "same call with new_person: no second person")
    check(count("MATCH (:Person {id: $id})<-[:ABOUT]-(n:Note) RETURN count(n)", id=pid) == 1, "repeats: one note")
    check(count("MATCH (:Me)-[r:MET]->(:Person {id: $id}) RETURN count(r)", id=pid) == 1, "repeats: one MET link")
    check(count("MATCH (:Person {id: $id})-[r:MET_AT]->() RETURN count(r)", id=pid) == 1, "repeats: one MET_AT link")
    other_day = memory.remember(g, "Repeat Rae", **{**call, "on": "2026-03-02"})
    check(other_day["status"] == "saved", "same words on another day: a new meeting")
    other_words = memory.remember(g, "Repeat Rae", **{**call, "note": "new words"})
    check(other_words["status"] == "saved", "other words on the same day: a new note")
    memory.forget(g, "note", other_words["note_id"])
    check(memory.remember(g, "Repeat Rae", **{**call, "note": "new words"})["status"] == "saved", "a forgotten note can be saved again")
    a = memory.remember(g, "Blank Bo", new_person=True)
    b = memory.remember(g, "Blank Bo", new_person=True)
    check(a["person"]["id"] != b["person"]["id"], "new_person with nothing else: two people, as asked")
    note = g.ro_query("MATCH (n:Note {id: $id}) RETURN n", {"id": first["note_id"]}).result_set[0][0]
    check("key" not in memory.plain(note), "ask does not show the repeat key")


def run_jobs(g):
    """A person can hold several jobs. A new one ends only the ones you say ended."""
    g.query("CREATE (p:Person {id: 'two-jobs', name: 'Tess Twojobs', search: 'tess twojobs', company: 'Main Job', headline: 'x'})"
            " CREATE (p)-[:WORKED_AT {current: true}]->(:Company {id: 'main job', name: 'Main Job', search: 'main job'})"
            " CREATE (p)-[:WORKED_AT {current: true}]->(:Company {id: 'side job', name: 'Side Job', search: 'side job'})")

    def now():
        return sorted(c for (c,) in g.ro_query("MATCH (:Person {id: 'two-jobs'})-[:WORKED_AT {current: true}]->(c) RETURN c.name").result_set)

    notes = g.ro_query("MATCH (n:Note) RETURN count(n)").result_set[0][0]
    r = memory.remember(g, "", person_id="two-jobs", company="New Job", note="moved")
    check(r["status"] == "needs_jobs" and sorted(r["current_jobs"]) == ["Main Job", "Side Job"], "two current jobs: asks which ended")
    check(now() == ["Main Job", "Side Job"] and g.ro_query("MATCH (n:Note) RETURN count(n)").result_set[0][0] == notes,
          "two current jobs: asking saves nothing")
    message = refused(memory.remember, g, "", person_id="two-jobs", company="New Job", ended_jobs=["Nope"])
    check("Nope" in message and "Main Job" in message, "a job they do not hold is refused, and the error lists their jobs")
    r = memory.remember(g, "", person_id="two-jobs", company="New Job", note="left main", ended_jobs=["main job"])
    check(r["status"] == "saved" and now() == ["New Job", "Side Job"], "ended one job: the other stays current")
    check("works at New Job now (ended: Main Job)" in r["changes"], "the reply names only the job that ended")
    memory.forget(g, "note", r["note_id"])
    check(now() == ["Main Job", "Side Job"], "forget: the ended job comes back")
    r = memory.remember(g, "", person_id="two-jobs", company="Extra Job", note="added a job", ended_jobs=[])
    check(r["status"] == "saved" and now() == ["Extra Job", "Main Job", "Side Job"], "empty list: keeps every job")
    check("works at Extra Job now" in r["changes"], "the reply names no ended job when none ended")
    memory.forget(g, "note", r["note_id"])


def run_about(g):
    """Find people by what they do or what you talked about, not only by name."""
    def people(text):
        return {p["name"]: p for p in memory.about(g, text)["people"]}

    memory.remember(g, "Topical Tom", company="Tom Co", note="Talked about vector databases over email", new_person=True)
    g.query("CREATE (p:Person {id: 'hl', name: 'Headline Hana', search: 'headline hana', headline: 'Head of Quokka Robotics', company: ''})"
            " CREATE (p)-[:HAS_SKILL]->(:Skill {id: 'zigzag', name: 'Zigzag Welding'})"
            " CREATE (p)-[:INTO]->(:Topic {id: 'zebra studies', name: 'Zebra Studies'})")

    tom = people("vector databases")
    check("Topical Tom" in tom and tom["Topical Tom"]["why"][0]["from"] == "note", "about finds words in a note, and says so")
    check("vector databases" in tom["Topical Tom"]["why"][0]["text"], "about shows the words it matched")
    check(tom["Topical Tom"]["works_at"] == ["Tom Co"] and tom["Topical Tom"]["id"], "about gives the id and the job")
    check("Topical Tom" in people("DATABASE vector"), "about: any order, any case, and word starts match")
    check("Topical Tom" not in people("ai"), "about: 'ai' does not match inside 'email'")
    check("Topical Tom" not in people("vector robots"), "about: every word must match")
    check(people("quokka robotics")["Headline Hana"]["why"][0]["from"] == "headline", "about finds words in a headline")
    check(people("zigzag")["Headline Hana"]["why"][0] == {"from": "skill", "text": "Zigzag Welding"}, "about finds a skill")
    check(people("zebra")["Headline Hana"]["why"][0] == {"from": "topic", "text": "Zebra Studies"}, "about finds a topic")

    memory.remember(g, "Quokka Note Nia", note="Runs quokka robotics at a zoo", new_person=True)
    check(memory.about(g, "quokka robotics")["people"][0]["name"] == "Quokka Note Nia", "about: your own notes come first")

    memory.remember(g, "图 专家", note="讨论了图数据库", new_person=True)
    check("图 专家" in people("数据库"), "about finds words inside non-Latin text")

    for i in range(25):
        g.query("CREATE (:Person {id: $id, name: $name, search: $name, headline: 'Wombat Wrangler', company: ''})",
                {"id": f"w{i}", "name": f"wombat person {i}"})
    r = memory.about(g, "wombat")
    check(r["found"] == 25 and len(r["people"]) == 20, "about shows 20 people and says how many it found")
    check(memory.about(g, "no such words here") == {"found": 0, "people": []}, "about: no match")
    check(refused(memory.about, g, "   "), "about: empty words are refused")


def rebuild(*flags):
    """Run load on the test graph, the way you would after a new LinkedIn export."""
    r = subprocess.run([sys.executable, "-m", "personal_brain.load_graph", "--sample", "--graph", TEST_GRAPH, *flags],
                       capture_output=True, text=True, env={**os.environ, "PB_HOME": REBUILD_HOME})
    return r


def run_rebuild(g):
    """A new LinkedIn import keeps everything your notes added."""
    def one(cypher, **params):
        rows = g.ro_query(cypher, params).result_set
        return rows[0][0] if rows else None

    def counts():
        nodes = dict(g.ro_query("MATCH (n) RETURN labels(n)[0], count(n)").result_set)
        edges = dict(g.ro_query("MATCH ()-[r]->() RETURN type(r), count(r)").result_set)
        return nodes, edges

    check(rebuild("--force").returncode == 0, "start from a clean graph")
    memory.setup(g)
    plain_counts = counts()

    # A new person, with a new company, a new event and a topic.
    new = memory.remember(g, "Kept Kay", company="Kept Co", event="Kept Meetup", note="into agent memory", on="2026-01-15", new_person=True)
    # Someone from the import who moved jobs.
    pid = g.ro_query("MATCH (p:Person)-[:WORKED_AT {current: true}]->(c:Company) WITH p, collect(c.name) AS jobs WHERE size(jobs) = 1"
                     " AND NOT p.name STARTS WITH 'Kept' RETURN p.id LIMIT 1").result_set[0][0]
    old_job = one("MATCH (:Person {id: $id})-[:WORKED_AT {current: true}]->(c) RETURN c.name", id=pid)
    moved = memory.remember(g, "", person_id=pid, company="Moved Co", note="moved jobs")
    # Someone the next import no longer has.
    g.query("CREATE (:Person {id: 'gone-from-import', name: 'Gone Gia', search: 'gone gia', headline: 'x', company: 'Gia Co'})")
    gone = memory.remember(g, "", person_id="gone-from-import", note='left "my" network \\ 網絡')
    # A job and event already in the import: they must not double up.
    known = g.ro_query("MATCH (p:Person)-[:WORKED_AT {current: true}]->(c:Company) WHERE p.id <> $id RETURN p.id, c.name LIMIT 1", {"id": pid}).result_set[0]
    same_job = memory.remember(g, "", person_id=known[0], company=known[1], note="same job as before")
    before = counts()

    r = rebuild()
    check(r.returncode == 0, "rebuild with notes works" + (": " + r.stderr[-300:] if r.returncode else ""))
    check(f"Kept {before[0]['Note']} notes" in r.stdout, "rebuild says how many notes it kept")
    check(not list(Path(REBUILD_HOME).rglob("notes-backup.json")), "rebuild removes its backup when done")
    memory.setup(g)
    check(counts() == before, "rebuild: same nodes and links as before")

    check(memory.find(g, "Kept Kay", "Person")[0]["works_at"] == ["Kept Co"], "rebuild: new person and new company stay")
    row = g.ro_query("MATCH (:Me)-[:WROTE]->(n:Note {id: $id})-[:ABOUT]->(p:Person), (p)-[m:MET_AT]->(e:Event {name: 'Kept Meetup'}), (p)-[:INTO]->(:Topic {name: 'Agent Memory'})"
                     " RETURN n.text, n.on, e.date", {"id": new["note_id"]}).result_set
    check(row == [["into agent memory", "2026-01-15", "2026-01-15"]], "rebuild: note, date, event and topic stay")
    jobs = g.ro_query("MATCH (p:Person {id: $id})-[w:WORKED_AT]->(c) WHERE c.name IN [$old, 'Moved Co'] RETURN c.name, w.current, p.company ORDER BY c.name",
                      {"id": pid, "old": old_job}).result_set
    check(sorted(jobs) == sorted([["Moved Co", True, "Moved Co"], [old_job, False, "Moved Co"]]), "rebuild: a job change stays")
    check(one("MATCH (p:Person {id: 'gone-from-import'}) RETURN p.name") == "Gone Gia", "rebuild: a person with notes stays when the import drops them")
    check(one("MATCH (n:Note {id: $id}) RETURN n.text", id=gone["note_id"]) == 'left "my" network \\ 網絡', "rebuild: quotes and any script stay exact")

    again = memory.remember(g, "Kept Kay", company="Kept Co", event="Kept Meetup", note="into agent memory", on="2026-01-15")
    check(again["status"] == "already_saved", "rebuild: a repeat is still found")

    # The notes still undo cleanly after the rebuild.
    memory.forget(g, "note", moved["note_id"])
    check(one("MATCH (p:Person {id: $id}) RETURN p.company", id=pid) != "Moved Co" and
          one("MATCH (:Person {id: $id})-[:WORKED_AT {current: true}]->(c) RETURN c.name", id=pid) == old_job, "rebuild, then forget: the old job comes back")
    memory.forget(g, "note", gone["note_id"])
    check(one("MATCH (p:Person {id: 'gone-from-import'}) RETURN count(p)") == 0, "rebuild, then forget: a dropped person goes")
    memory.forget(g, "note", new["note_id"])
    check(one("MATCH (c:Company {name: 'Kept Co'}) RETURN count(c)") == 0, "rebuild, then forget: new company goes")
    memory.forget(g, "note", same_job["note_id"])

    # A rebuild that stopped halfway: the next one finds the notes in the backup.
    kept = memory.remember(g, "Crash Cam", note="survives a crash", new_person=True)
    backup_module = __import__("personal_brain.notes_backup", fromlist=["snapshot"])
    backup = Path(REBUILD_HOME) / "data" / "notes-backup.json"
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(json.dumps(backup_module.snapshot(g)))
    g.query("MATCH (n:Note) DETACH DELETE n")
    r = rebuild()
    memory.setup(g)
    check(r.returncode == 0 and one("MATCH (n:Note {id: $id}) RETURN n.text", id=kept["note_id"]) == "survives a crash",
          "rebuild after a crash: notes come back from the backup")

    # --force is how you start over.
    check(rebuild("--force").returncode == 0 and one("MATCH (n:Note) RETURN count(n)") == 0, "rebuild --force drops notes")
    memory.setup(g)
    check(counts() == plain_counts, "rebuild --force: back to a clean graph")


def run(g):
    run_rebuild(g)
    run_names(g)
    run_about(g)
    run_forget(g)
    run_dates(g)
    run_half_saved(g)
    run_repeats(g)
    run_jobs(g)
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

    # ask is read-only, and facts cannot be forgotten one at a time.
    a = memory.ask(g, "MATCH (:Me)-[r:CONNECTED]->(p:Person) RETURN r, p.name LIMIT 1")
    check(a["rows"] and a["rows"][0][0]["type"] == "CONNECTED", "ask returns rows")
    try:
        memory.ask(g, "MATCH (p:Person) DETACH DELETE p")
        check(False, "ask refuses writes")
    except Exception:
        check(True, "ask refuses writes")
    check("rel_id" not in a["rows"][0][0], "ask does not return rel_id")
    try:
        memory.forget(g, "fact", "1")
        check(False, "forget refuses a fact")
    except ValueError:
        check(True, "forget refuses a fact")

    total = g.ro_query("MATCH (n:Note) RETURN count(n)").result_set[0][0]
    check(memory.forget_all_notes(g) >= total and
          g.ro_query("MATCH (n:Note) RETURN count(n)").result_set[0][0] == 0, "reset removes every note")


if __name__ == "__main__":
    main()
