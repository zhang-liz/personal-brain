"""End-to-end check of the memory tools on the sample network, in a throwaway graph.

Loads sample_data/ into graph "pb_memory_test", runs remember / find / ask /
forget against it, then deletes the graph. Never touches your "network" graph.

    .venv/bin/python tests/test_memory.py --cloud   # FALKORDB_URL from .env
    .venv/bin/python tests/test_memory.py           # local FalkorDB
"""

import json
import os
import shutil
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
    check(memory.fold("Tomás Ortega") == "tomas ortega" and memory.norm("Kestrel, Inc.") == "kestrel inc",
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


def rebuild(*flags, sample=None):
    """Run load on the test graph, the way you would after a new LinkedIn export."""
    extra = {"PB_SAMPLE_DIR": str(sample)} if sample else {}
    r = subprocess.run([sys.executable, "-m", "personal_brain.load_graph", "--sample", "--graph", TEST_GRAPH, *flags],
                       capture_output=True, text=True, env={**os.environ, "PB_HOME": REBUILD_HOME, **extra})
    return r


def sample_with(*connections):
    """A copy of the sample network with more rows in Connections.csv, as in a newer LinkedIn export."""
    sample = Path(tempfile.mkdtemp()) / "sample"
    shutil.copytree(PROJECT / "src/personal_brain/sample_data", sample)
    file = sample / "linkedin/Connections.csv"
    file.write_text(file.read_text(encoding="utf-8").rstrip("\n") + "\n" + "\n".join(connections) + "\n", encoding="utf-8")
    return sample


def run_merge_on_import(g):
    """Someone you saved first, then a new import has them: one person, with your notes."""
    def noras(name):
        return g.ro_query("MATCH (p:Person {name: $n}) RETURN p.id", {"n": name}).result_set

    check(rebuild("--force").returncode == 0, "start from a clean graph")
    memory.setup(g)
    saved = memory.remember(g, "Nora Newcomer", company="Kestrel Graph Inc.", event="Merge Meetup", note="saved first", title="CTO", new_person=True)
    plain = memory.remember(g, "Solo Sol", note="no company given", new_person=True)
    other = memory.remember(g, "Other Olga", company="Another Company", note="a different Olga", new_person=True)
    sample = sample_with("Nora,Newcomer,https://www.linkedin.com/in/nora-newcomer,,Kestrel Graph,Engineer,01 Jan 2026",
                         "Solo,Sol,https://www.linkedin.com/in/solo-sol,,Sol Works,Engineer,01 Jan 2026",
                         "Other,Olga,https://www.linkedin.com/in/other-olga,,Kestrel Graph,Engineer,01 Jan 2026")
    r = rebuild(sample=sample)
    check(r.returncode == 0 and "Merged 2" in r.stdout, "rebuild says how many people it merged" + (": " + (r.stdout + r.stderr)[-300:] if r.returncode or "Merged 2" not in r.stdout else ""))
    memory.setup(g)
    check(noras("Nora Newcomer") == [["nora-newcomer"]], "same name and company: one person, with the import's id")
    check(noras("Solo Sol") == [["solo-sol"]], "same name and no company saved: one person")
    check(len(noras("Other Olga")) == 2, "same name and another company: stays two people")
    row = g.ro_query("MATCH (:Me)-[:MET]->(p:Person {id: 'nora-newcomer'})<-[:ABOUT]-(n:Note {id: $id}), (p)-[:MET_AT]->(e:Event {name: 'Merge Meetup'}), (:Me)-[:CONNECTED]->(p)"
                     " RETURN n.text, p.title", {"id": saved["note_id"]}).result_set
    check(row == [["saved first", "CTO"]], "the note, the meeting, the event and the title move to the imported person")
    check(memory.remember(g, "Nora Newcomer", note="after the import")["status"] == "saved", "the next remember does not ask which one")
    jobs = g.ro_query("MATCH (:Person {id: 'nora-newcomer'})-[:WORKED_AT {current: true}]->(c) RETURN c.name").result_set
    check(jobs == [["Kestrel Graph"]], "no second job from the note")
    memory.forget(g, "note", saved["note_id"])
    check(noras("Nora Newcomer") == [["nora-newcomer"]], "forget the note: the imported person stays")
    memory.forget(g, "note", plain["note_id"])
    memory.forget(g, "note", other["note_id"])


def run_open_client(g):
    """A rebuild renumbers field names. A client that was already open, like a running MCP server, must still read them right."""
    saved = memory.remember(g, "Open Client Olu", title="CTO", location="Oslo", note="open client", new_person=True)
    g.ro_query("MATCH (n:Note {id: $id}) RETURN n", {"id": saved["note_id"]})    # the client now holds the field names
    rebuild()
    note = memory.ask(g, "MATCH (n:Note {id: $id}) RETURN n", {"id": saved["note_id"]})["rows"][0][0]
    check(note.get("text") == "open client" and note.get("on"), "an open client reads the right fields after a rebuild")
    memory.forget(g, "note", saved["note_id"])
    left = g.ro_query("MATCH (p:Person {id: $id}) RETURN p.location", {"id": saved["person"]["id"]}).result_set
    check(not left or left[0][0] is None, "an open client can forget after a rebuild")


def run_company_names(g):
    """The same company said another way is the same job."""
    def jobs(pid):
        return sorted(g.ro_query("MATCH (:Person {id: $id})-[w:WORKED_AT]->(c) RETURN c.name, w.current", {"id": pid}).result_set)

    def companies(name):
        return g.ro_query("MATCH (c:Company) WHERE toLower(c.name) CONTAINS $n RETURN count(c)", {"n": name}).result_set[0][0]

    g.query("MERGE (c:Company {id: 'lanternfish data'}) ON CREATE SET c.name = 'Lanternfish Data', c.search = 'lanternfish data'"
            " CREATE (:Person {id: 'lf', name: 'Lena Fisher', search: 'lena fisher', company: 'Lanternfish Data', headline: 'x'})"
            "-[:WORKED_AT {current: true}]->(c)")
    for said in ["Lanternfish Data Inc.", "Lanternfish", "lanternfish data", "Lanternfsh Data", "LANTERNFISH DATA LLC"]:
        r = memory.remember(g, "", person_id="lf", company=said, note=f"said {said}")
        check(r["status"] == "saved" and jobs("lf") == [["Lanternfish Data", True]] and not any("now" in c for c in r["changes"]),
              f"{said}: the same job, with no job change")
    check(companies("lanternfish") == 1, "no second company")
    r = memory.remember(g, "Lena Fisher", company="Lanternfish Data Inc.", note="by name")
    check(r["status"] == "saved" and r["person"]["id"] == "lf", "by name with another spelling: the same person, no question")
    r = memory.remember(g, "Fresh Fran", company="Lanternfish Data, Inc.", note="new person", new_person=True)
    check(companies("lanternfish") == 1, "a new person joins the existing company")
    check(memory.find(g, "Fresh Fran", "Person")[0]["works_at"] == ["Lanternfish Data"], "a new person shows the existing company's name")

    r = memory.remember(g, "", person_id="lf", company="Applesauce Labs", note="a real move")
    check(jobs("lf") == [["Applesauce Labs", True], ["Lanternfish Data", False]], "another company is still a job change")
    g.query("CREATE (p:Person {id: 'ap', name: 'Abe Pell', search: 'abe pell', company: 'Apple', headline: 'x'})"
            "-[:WORKED_AT {current: true}]->(:Company {id: 'apple', name: 'Apple', search: 'apple'})")
    memory.remember(g, "", person_id="ap", company="Apply", note="short names are not typos")
    check(jobs("ap") == [["Apple", False], ["Apply", True]], "a short name one letter off is another company")


def run_event_names(g):
    """The same event said another way is the same event."""
    g.query("CREATE (:Event {id: 'gr', name: 'Graph & RAG Meetup', search: 'graph rag meetup', date: '2026-01-01'}), (:Event {id: 'as', name: 'AI Summit 2025', search: 'ai summit 2025', date: '2025-01-01'})")

    def event_of(said, note):
        r = memory.remember(g, "Event Eve", event=said, note=note)
        return next(c for c in r["changes"] if c.startswith("met at"))

    memory.remember(g, "Event Eve", new_person=True)
    for i, said in enumerate(["Graph and RAG Meetup", "Graph RAG Meetup", "graph & rag meetup", "Graph & RAG Meetp"]):
        check(event_of(said, f"note {i}") == "met at Graph & RAG Meetup", f"{said}: the same event")
    check(g.ro_query("MATCH (e:Event) WHERE e.name IN ['Graph and RAG Meetup', 'Graph & RAG Meetp'] RETURN count(e)").result_set[0][0] == 0, "no second event")
    check(event_of("AI Summit 2026", "next year") == "met at AI Summit 2026", "another year is another event")
    check(event_of("Rooftop Party", "new") == "met at Rooftop Party", "another name is another event")


def run_coworkers(g):
    """Someone new is linked to the people you know at their company."""
    g.query("CREATE (c:Company {id: 'wren works', name: 'Wren Works', search: 'wren works'})"
            " CREATE (:Person {id: 'w1', name: 'Wren One', search: 'wren one', company: 'Wren Works', headline: 'x'})-[:WORKED_AT {current: true}]->(c)"
            " CREATE (:Person {id: 'w2', name: 'Wren Two', search: 'wren two', company: 'Wren Works', headline: 'x'})-[:WORKED_AT {current: false}]->(c)")
    r = memory.remember(g, "Wren Three", company="Wren Works", note="joined", new_person=True)
    links = g.ro_query("MATCH (:Person {id: $id})-[k:KNOWS]-(o) RETURN o.id, k.via, k.place", {"id": r["person"]["id"]}).result_set
    check(links == [["w1", "coworkers", "Wren Works"]], "a new person knows current coworkers only")
    check("knows 1 coworker" in " ".join(r["changes"]), "the reply says so")
    check("MET" in memory.SCHEMA and "not CONNECTED" in memory.SCHEMA, "the schema says people you met may not be CONNECTED")
    memory.forget(g, "note", r["note_id"])
    check(g.ro_query("MATCH (:Person {id: 'w1'})-[k:KNOWS]-() RETURN count(k)").result_set[0][0] == 0, "forget the note: the link goes")


def run_facts(g):
    """Title, city and email are fields, so they can be asked about and corrected."""
    def person(pid):
        return g.ro_query("MATCH (p:Person {id: $id}) RETURN p.title, p.location, p.email", {"id": pid}).result_set[0]

    def cities(pid):
        return sorted(c for (c,) in g.ro_query("MATCH (:Person {id: $id})-[:LIVES_IN]->(c) RETURN c.name", {"id": pid}).result_set)

    first = memory.remember(g, "Fact Fay", company="Fact Co", title="CTO", location="Berlin", email="fay@example.com", note="met at lunch", new_person=True)
    pid = first["person"]["id"]
    check(person(pid) == ["CTO", "Berlin", "fay@example.com"] and cities(pid) == ["Berlin"], "title, city and email are saved as fields")
    found = memory.find(g, "Fact Fay", "Person")[0]
    check(found["title"] == "CTO" and found["location"] == "Berlin" and found["email"] == "fay@example.com", "find shows them")
    job = "MATCH (:Person {id: $id})-[w:WORKED_AT {current: true}]->() RETURN w.title"
    check(g.ro_query(job, {"id": pid}).result_set == [["CTO"]], "the job has the title")
    met = "MATCH (:Me)-[r:MET]->(:Person {id: $id}) RETURN count(r)"

    fix = memory.remember(g, "", person_id=pid, title="VP of Engineering")
    check(fix["status"] == "saved" and person(pid)[0] == "VP of Engineering", "a correction replaces the title")
    check(g.ro_query(job, {"id": pid}).result_set == [["VP of Engineering"]], "a correction reaches the job")
    check(g.ro_query(met, {"id": pid}).result_set[0][0] == 1, "a correction is not a meeting")
    text = g.ro_query("MATCH (n:Note {id: $id}) RETURN n.text", {"id": fix["note_id"]}).result_set[0][0]
    check("VP of Engineering" in text and not text.startswith("Met"), "a correction's note says what changed")
    check(memory.remember(g, "", person_id=pid, title="VP of Engineering")["status"] == "already_saved", "the same correction twice saves once")

    move = memory.remember(g, "", person_id=pid, location="Paris, France")
    check(person(pid)[1] == "Paris, France" and cities(pid) == ["Paris"], "a new city replaces the old one")
    memory.forget(g, "note", move["note_id"])
    check(person(pid)[1] == "Berlin" and cities(pid) == ["Berlin"], "forget: the old city comes back")

    third = memory.remember(g, "", person_id=pid, title="CEO")
    memory.forget(g, "note", fix["note_id"])
    check(person(pid)[0] == "CEO", "forget a middle correction: the newest value stays")
    memory.forget(g, "note", third["note_id"])
    check(person(pid)[0] == "CTO" and g.ro_query(job, {"id": pid}).result_set == [["CTO"]], "forget the newest: the value from before comes back")

    g.query("CREATE (p:Person {id: 'imp', name: 'Imp Ida', search: 'imp ida', title: 'Old Title', location: 'Oslo', company: 'Imp Co', headline: 'x'})"
            " CREATE (p)-[:LIVES_IN]->(:City {id: 'oslo', name: 'Oslo'})"
            " CREATE (p)-[:WORKED_AT {current: true, title: 'Job Title'}]->(:Company {id: 'imp co', name: 'Imp Co', search: 'imp co'})")
    n = memory.remember(g, "", person_id="imp", title="New Title", location="Rome", email="ida@example.com")
    check(person("imp") == ["New Title", "Rome", "ida@example.com"] and cities("imp") == ["Rome"], "an imported person: fields change")
    memory.forget(g, "note", n["note_id"])
    check(person("imp") == ["Old Title", "Oslo", None] and cities("imp") == ["Oslo"], "an imported person: forget brings the import's values back")
    check(g.ro_query(job, {"id": "imp"}).result_set == [["Job Title"]], "an imported person: forget brings the job's title back")
    node = g.ro_query("MATCH (p:Person {id: $id}) RETURN p", {"id": pid}).result_set[0][0]
    check(not [k for k in memory.plain(node) if k.endswith("_note")], "ask does not show the bookkeeping fields")
    memory.forget(g, "note", first["note_id"])
    check(g.ro_query("MATCH (p:Person {id: $id}) RETURN count(p)", {"id": pid}).result_set[0][0] == 0, "forget the first note: the person goes")


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
    fact = memory.remember(g, "", person_id=pid, title="Kept Title", location="Lisbon", email="kept@example.com")
    r = rebuild()
    memory.setup(g)
    row = g.ro_query("MATCH (p:Person {id: $id})-[:LIVES_IN]->(c:City) RETURN p.title, p.email, c.name", {"id": pid}).result_set
    check(row == [["Kept Title", "kept@example.com", "Lisbon"]], "rebuild: corrected fields stay, and the import's old city does not come back")
    memory.forget(g, "note", fact["note_id"])
    check(rebuild("--force").returncode == 0 and one("MATCH (n:Note) RETURN count(n)") == 0, "rebuild --force drops notes")
    memory.setup(g)
    check(counts() == plain_counts, "rebuild --force: back to a clean graph")


def run_job_ends(g):
    """A current job has no end date. Older graphs held today's date there as a stand-in."""
    stand_ins = "MATCH ()-[w:WORKED_AT {current: true}]->() WHERE w.end IS NOT NULL RETURN count(w)"
    check(g.ro_query(stand_ins).result_set[0][0] == 0, "a new graph: current jobs have no end date")
    ended = g.ro_query("MATCH ()-[w:WORKED_AT {current: false}]->() WHERE w.end IS NOT NULL RETURN count(w)").result_set[0][0]
    check(ended > 0, "a new graph: past jobs keep their end date")
    g.query("CREATE (p:Person {id: 'old-end', name: 'Old End', search: 'old end'})"
            "-[:WORKED_AT {current: true, start: 2020.0, end: 2026.83}]->(:Company {id: 'old end co', name: 'Old End Co', search: 'old end co'})")
    memory.setup(g)
    check(g.ro_query(stand_ins).result_set[0][0] == 0, "setup clears the stand-in on an older graph")
    check(g.ro_query("MATCH (:Person {id: 'old-end'})-[w:WORKED_AT]->() RETURN w.start, w.current").result_set == [[2020.0, True]],
          "setup keeps the rest of that job")
    check(g.ro_query("MATCH ()-[w:WORKED_AT {current: false}]->() WHERE w.end IS NOT NULL RETURN count(w)").result_set[0][0] == ended,
          "setup keeps real end dates")


def run(g):
    run_open_client(g)    # first: only the first rebuild of a new graph renumbers the fields
    run_merge_on_import(g)
    run_rebuild(g)
    run_company_names(g)
    run_event_names(g)
    run_coworkers(g)
    run_facts(g)
    run_job_ends(g)
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
