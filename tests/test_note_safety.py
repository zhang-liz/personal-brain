"""Notes must stay true when sessions overlap, when a rebuild stops or changes the facts, and when other notes go.

Each test builds its own throwaway graph from the sample network.
"""

import hashlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from personal_brain import job_changes, load_graph, memory, notes_backup
from personal_brain.companies import same_company
from personal_brain.load_graph import fold

PROJECT = Path(__file__).resolve().parent.parent
GRAPH = "pb_note_safety_test"
PLACE = f"local/{GRAPH}"


def row(first, last, company, title="Engineer"):
    return f"{first},{last},https://www.linkedin.com/in/{first.lower()}-{last.lower()},,{company},{title},01 Jan 2026"


def profile(slug, jobs, city=None):
    positions = []
    for company, title, start, end in jobs:
        period = {"startDate": {"year": start, "month": 1}}
        if end:
            period["endDate"] = {"year": end, "month": 1}
        positions.append({"company": {"name": company}, "title": title, "timePeriod": period})
    return {"inputUrl": f"https://www.linkedin.com/in/{slug}", "headline": "h", "positions": positions,
            **({"geoLocationName": city} if city else {})}


class Box:
    """One throwaway graph, with a data folder of its own."""

    def __init__(self, monkeypatch):
        self.home = Path(tempfile.mkdtemp())
        (self.home / "data").mkdir()
        monkeypatch.setattr(memory, "FORGOTTEN", self.home / "data" / "forgotten.json")
        monkeypatch.setattr(notes_backup, "BACKUP", self.home / "data" / "notes-backup.json")
        self.g = None

    def load(self, connections=(), profiles=(), *flags, check=True):
        sample = self.home / "sample"
        shutil.rmtree(sample, ignore_errors=True)
        shutil.copytree(PROJECT / "src/personal_brain/sample_data", sample)
        file = sample / "linkedin/Connections.csv"
        file.write_text(file.read_text(encoding="utf-8").rstrip("\n") + "\n" + "\n".join(connections) + "\n", encoding="utf-8")
        if profiles:
            (sample / "enriched/batch_zz.json").write_text(json.dumps({"items": list(profiles)}), encoding="utf-8")
        r = subprocess.run([sys.executable, "-m", "personal_brain.load_graph", "--sample", "--graph", GRAPH, *flags],
                           capture_output=True, text=True,
                           env={**os.environ, "PB_HOME": str(self.home), "PB_SAMPLE_DIR": str(sample), "FALKORDB_URL": ""})
        if check:
            assert r.returncode == 0, r.stderr[-800:]
        if self.g is None:
            self.g = memory.connect(GRAPH)[0]
        memory.setup(self.g)
        return r

    def rows(self, cypher, **params):
        self.g.schema.clear()
        return self.g.ro_query(cypher, params).result_set

    def jobs(self, pid):
        return sorted(self.rows("MATCH (:Person {id: $id})-[w:WORKED_AT]->(c) RETURN c.name, w.title, w.current", id=pid), key=str)

    def current(self, pid):
        return sorted(name for name, _, now in self.jobs(pid) if now)

    def person(self, pid, *fields):
        return self.rows(f"MATCH (p:Person {{id: $id}}) RETURN {', '.join('p.' + f for f in fields)}", id=pid)[0]

    def city(self, pid):
        return sorted(c for (c,) in self.rows("MATCH (:Person {id: $id})-[:LIVES_IN]->(c) RETURN c.name", id=pid))

    def notes(self):
        return sorted(t for (t,) in self.rows("MATCH (n:Note) RETURN n.text"))


@pytest.fixture
def box(monkeypatch):
    b = Box(monkeypatch)
    yield b
    if b.g is not None:
        b.g.delete()


# ---------- overlapping sessions ----------

def test_overlapping_saves_all_stay(box):
    box.load([row("Nia", "Race", "Race Co")], (), "--force")
    saved, errors = [], []

    def session(k):
        g = memory.connect(GRAPH)[0]
        for i in range(10):
            try:
                saved.append(memory.remember(g, "", person_id="nia-race", note=f"session {k} note {i}", event=f"Kestrel Meet {k} {i}"))
            except Exception as e:
                errors.append(repr(e))

    threads = [threading.Thread(target=session, args=(k,)) for k in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert len(box.notes()) == 40


def test_a_save_in_progress_stays_but_an_old_one_goes(box):
    box.load((), (), "--force")
    for nid, since in [("note-now", time.time()), ("note-old", time.time() - 3600), ("note-oldest", True)]:
        box.g.query("MATCH (me:Me) CREATE (me)-[:WROTE]->(:Note {id: $id, text: $id, on: '2026-01-01', source: 'agent', saving: $since})",
                    {"id": nid, "since": since})
    memory.remember(box.g, "Jonas Lind", note="met", new_person=True)
    assert box.notes() == ["met", "note-now"]


# ---------- a rebuild that stopped ----------

def test_rebuild_that_never_deleted_the_graph_uses_the_live_graph(box):
    connections = [row("Oda", "Keep", "Keep Co")]
    box.load(connections, (), "--force")
    memory.remember(box.g, "", person_id="oda-keep", note="old note")
    gone = memory.remember(box.g, "", person_id="oda-keep", note="note forgotten later")
    notes_backup.save({**notes_backup.snapshot(box.g), "graph_deleted": False, "place": PLACE})
    memory.remember(box.g, "", person_id="oda-keep", note="newer note")
    memory.forget_note(box.g, gone["note_id"])
    box.load(connections)
    assert box.notes() == ["newer note", "old note"]


def test_rebuild_that_stopped_halfway_keeps_notes_from_both(box):
    connections = [row("Oda", "Keep", "Keep Co")]
    box.load(connections, (), "--force")
    memory.remember(box.g, "", person_id="oda-keep", note="old note")
    rex = memory.remember(box.g, "Rex Secret", note="private thing", new_person=True)
    notes_backup.save(notes_backup.snapshot(box.g))    # as 0.3.1 wrote it: no word on whether the graph went
    memory.remember(box.g, "", person_id="oda-keep", note="newer note")
    memory.forget_person(box.g, rex["person"]["id"])
    box.load(connections)
    assert box.notes() == ["newer note", "old note"]
    assert box.rows("MATCH (p:Person {name: 'Rex Secret'}) RETURN count(p)") == [[0]]


def test_backup_of_another_graph_stops_the_rebuild(box):
    box.load((), (), "--force")
    memory.remember(box.g, "Jonas Lind", note="in this graph", new_person=True)
    notes_backup.save({"nodes": [{"label": "Note", "props": {"id": "note-x", "text": "other graph"}}], "about": [], "links": [],
                       "graph_deleted": True, "place": "local/other"})
    r = box.load((), (), check=False)
    assert r.returncode != 0 and "local/other" in r.stderr and "Traceback" not in r.stderr
    assert box.notes() == ["in this graph"]


# ---------- forget after a rebuild ----------

def test_forget_after_merge_brings_back_what_the_import_says(box):
    box.load((), (), "--force")
    n = memory.remember(box.g, "Jonas Lind", company="Kestrel Labs Inc", note="met", title="CTO", location="Rome", email="j@x.test", new_person=True)
    connections = [row("Jonas", "Lind", "Kestrel Labs")]
    box.load(connections, [profile("jonas-lind", [("Kestrel Labs", "Engineer", 2020, None)], "Oslo, Norway")])
    assert box.current("jonas-lind") == ["Kestrel Labs"]
    memory.forget_note(box.g, n["note_id"])
    assert box.person("jonas-lind", "title", "company") == ["Engineer", "Kestrel Labs"]
    assert box.city("jonas-lind") == ["Oslo"]
    assert box.rows("MATCH (c:Company {name: 'Kestrel Labs Inc'}) RETURN count(c)") == [[0]]


def test_forget_after_rebuild_brings_back_the_new_import_not_the_old_one(box):
    box.load([row("Gus", "Stale", "Stale Co")], [profile("gus-stale", [("Stale Co", "Engineer", 2019, None)], "Oslo, Norway")], "--force")
    n = memory.remember(box.g, "", person_id="gus-stale", title="Typo Title", location="Rome")
    box.load([row("Gus", "Stale", "Stale Co", "Director")], [profile("gus-stale", [("Stale Co", "Director", 2019, None)], "Lisbon, Portugal")])
    assert box.person("gus-stale", "title") == ["Typo Title"]
    assert box.city("gus-stale") == ["Rome"]
    memory.forget_note(box.g, n["note_id"])
    assert box.person("gus-stale", "title", "location") == ["Director", "Lisbon, Portugal"]
    assert box.city("gus-stale") == ["Lisbon"]
    assert box.jobs("gus-stale") == [["Stale Co", "Director", True]]


PROMOTED = ([row("Eva", "Promo", "Kestrel Labs", "Senior Engineer")],
            [profile("eva-promo", [("Kestrel Labs", "Senior Engineer", 2022, None), ("Kestrel Labs", "Engineer", 2019, 2022)])])


def test_rebuild_leaves_a_past_role_alone(box):
    box.load(*PROMOTED, "--force")
    n = memory.remember(box.g, "", person_id="eva-promo", title="Staff Engineer")
    box.load(*PROMOTED)
    assert box.jobs("eva-promo") == [["Kestrel Labs", "Engineer", False], ["Kestrel Labs", "Staff Engineer", True]]
    memory.forget_note(box.g, n["note_id"])
    assert box.jobs("eva-promo") == [["Kestrel Labs", "Engineer", False], ["Kestrel Labs", "Senior Engineer", True]]


def test_forget_after_rebuild_does_not_reopen_a_past_role(box):
    box.load(*PROMOTED, "--force")
    n = memory.remember(box.g, "", person_id="eva-promo", company="Nova Graph", note="moved")
    box.load(*PROMOTED)
    assert box.current("eva-promo") == ["Nova Graph"]
    memory.forget_note(box.g, n["note_id"])
    assert box.jobs("eva-promo") == [["Kestrel Labs", "Engineer", False], ["Kestrel Labs", "Senior Engineer", True]]


def test_forget_does_not_reopen_a_job_the_import_ended(box):
    box.load([row("Finn", "Mover", "Old Co")], [profile("finn-mover", [("Old Co", "Eng", 2019, None)])], "--force")
    n = memory.remember(box.g, "", person_id="finn-mover", company="New Co", note="moved")
    box.load([row("Finn", "Mover", "New Co")], [profile("finn-mover", [("New Co", "Eng", 2025, None), ("Old Co", "Eng", 2019, 2025)])])
    assert memory.forget_note(box.g, n["note_id"])["jobs_reopened"] == 0
    assert box.current("finn-mover") == ["New Co"]
    assert box.person("finn-mover", "company") == ["New Co"]


def test_ended_job_stays_ended_when_the_export_spells_it_another_way(box):
    box.load([row("Hal", "Spell", "Spell Works")], (), "--force")
    memory.remember(box.g, "", person_id="hal-spell", company="Later Co", note="moved")
    box.load([row("Hal", "Spell", "Spell Works Inc")])
    assert box.current("hal-spell") == ["Later Co"]


def test_new_job_with_another_spelling_is_one_job(box):
    box.load([row("Ivo", "Spell", "Ivo Old")], (), "--force")
    memory.remember(box.g, "", person_id="ivo-spell", company="Zeta Graph Inc", note="moved")
    box.load([row("Ivo", "Spell", "Zeta Graph")])
    assert box.current("ivo-spell") == ["Zeta Graph"]


def test_return_to_an_old_job_survives_a_rebuild(box):
    connections = [row("Kai", "Back", "Kestrel Works")]
    box.load(connections, (), "--force")
    memory.remember(box.g, "", person_id="kai-back", company="Nova Graph", note="moved")
    memory.remember(box.g, "", person_id="kai-back", company="Kestrel Works", note="back again")
    box.load(connections)
    assert box.current("kai-back") == ["Kestrel Works"]


def test_job_a_later_note_ended_stays_ended_after_rebuild(box):
    box.load([row("Dee", "End", "Dee Old")], (), "--force")
    memory.remember(box.g, "", person_id="dee-end", company="Nova One", note="moved")
    memory.remember(box.g, "", person_id="dee-end", company="Nova Two", note="moved again")
    box.load([row("Dee", "End", "Nova One")])
    assert box.current("dee-end") == ["Nova Two"]


def test_forgotten_person_from_a_note_stays_out_of_the_import(box):
    box.load((), (), "--force")
    n = memory.remember(box.g, "Tia Gone", company="Kestrel Forge", note="x", new_person=True)
    memory.forget_person(box.g, n["person"]["id"])
    box.load([row("Tia", "Gone", "Kestrel Forge")])
    assert box.rows("MATCH (p:Person {name: 'Tia Gone'}) RETURN count(p)") == [[0]]


# ---------- company and event names ----------

@pytest.mark.parametrize("a, b, same", [
    ("kestrel labs", "kestrel", True),
    ("kestrel inc", "kestrel", True),
    ("kestrel data systems", "kestrel", True),
    ("kestrel capital", "kestrel", False),
    ("kestrel motion", "kestrel notion", False),
    ("kestrel motion", "kestrel mation", True),
    ("the home inc", "the home depot", False),
    ("a", "a cloud", True),
    ("a", "a cloud guru", False),
])
def test_same_company(a, b, same):
    assert same_company(a, b) is same


def test_move_to_a_company_with_a_similar_name_is_a_move(box):
    box.load([row("Uma", "Bank", "Kestrel"), row("Xan", "Typo", "Kestrel Motion")], (), "--force")
    r = memory.remember(box.g, "", person_id="uma-bank", company="Kestrel Capital", note="now at the fund")
    assert box.current("uma-bank") == ["Kestrel Capital"] and "works at Kestrel Capital now (ended: Kestrel)" in r["changes"]
    memory.remember(box.g, "", person_id="xan-typo", company="Kestrel Notion", note="moved")
    assert box.current("xan-typo") == ["Kestrel Notion"]


def test_same_job_under_another_name_is_reported(box):
    box.load([row("Lea", "Long", "Kestrel Labs")], (), "--force")
    r = memory.remember(box.g, "", person_id="lea-long", company="Kestrel", note="same place")
    assert box.current("lea-long") == ["Kestrel Labs"] and "same job as Kestrel Labs" in r["changes"]


def test_events_in_two_cities_are_two_events(box):
    box.load((), (), "--force")
    memory.remember(box.g, "Zed One", event="Kestrel Night Cork", new_person=True)
    memory.remember(box.g, "Zed Two", event="Kestrel Night York", new_person=True)
    assert sorted(n for (n,) in box.rows("MATCH (e:Event) WHERE e.name STARTS WITH 'Kestrel' RETURN e.name")) == ["Kestrel Night Cork", "Kestrel Night York"]


# ---------- forget with several notes ----------

def test_forget_older_side_job_keeps_the_newer_company(box):
    box.load([row("Ada", "Kest", "Old Co")], (), "--force")
    one = memory.remember(box.g, "", person_id="ada-kest", company="Kestrel One", ended_jobs=[], note="also advises")
    two = memory.remember(box.g, "", person_id="ada-kest", company="Nova Two", ended_jobs=[], note="also advises more")
    memory.forget_note(box.g, one["note_id"])
    assert box.person("ada-kest", "company") == ["Nova Two"]
    memory.forget_note(box.g, two["note_id"])
    assert box.person("ada-kest", "company") == ["Old Co"]
    assert box.current("ada-kest") == ["Old Co"]


def test_titles_on_two_jobs_stay_apart(box):
    box.load([row("Bo", "Twojob", "Alpha Works")], [profile("bo-twojob", [("Alpha Works", "A-title", 2020, None), ("Bravo Works", "B-title", 2021, None)])], "--force")
    a = memory.remember(box.g, "", person_id="bo-twojob", company="Alpha Works", title="A-new")
    b = memory.remember(box.g, "", person_id="bo-twojob", company="Bravo Works", title="B-new")
    memory.forget_note(box.g, b["note_id"])
    assert box.jobs("bo-twojob") == [["Alpha Works", "A-new", True], ["Bravo Works", "B-title", True]]
    memory.forget_note(box.g, a["note_id"])
    assert box.jobs("bo-twojob") == [["Alpha Works", "A-title", True], ["Bravo Works", "B-title", True]]


def test_two_titles_on_one_job_undo_in_any_order(box):
    box.load([row("Cleo", "Chain", "Alpha Works", "First")], [profile("cleo-chain", [("Alpha Works", "First", 2020, None)])], "--force")
    a = memory.remember(box.g, "", person_id="cleo-chain", title="Second")
    memory.remember(box.g, "", person_id="cleo-chain", title="Third")
    memory.forget_note(box.g, a["note_id"])
    assert box.jobs("cleo-chain") == [["Alpha Works", "Third", True]]


def test_job_in_two_notes_stays_when_one_goes(box):
    box.load((), (), "--force")
    first = memory.remember(box.g, "Jonas Newman", company="Kestrel Forge", note="first", new_person=True)
    pid = first["person"]["id"]
    memory.remember(box.g, "", person_id=pid, company="Kestrel Forge", note="second meeting")
    memory.forget_note(box.g, first["note_id"])
    assert box.current(pid) == ["Kestrel Forge"]
    assert box.person(pid, "company") == ["Kestrel Forge"]


def test_forget_adds_no_new_topics(box):
    box.load([row("Cy", "Topicless", "Calm Co")], (), "--force")
    memory.remember(box.g, "", person_id="cy-topicless", title="Founder")
    memory.remember(box.g, "", person_id="cy-topicless", event="Kestrel Hackathon")
    z = memory.remember(box.g, "", person_id="cy-topicless", note="nice lunch")
    memory.forget_note(box.g, z["note_id"])
    assert box.rows("MATCH (:Person {id: 'cy-topicless'})-[:INTO]->(t) RETURN t.name") == []


def test_topic_two_notes_share_stays_when_one_goes(box):
    box.load([row("Cy", "Topicless", "Calm Co")], (), "--force")
    a = memory.remember(box.g, "", person_id="cy-topicless", note="a founder")
    memory.remember(box.g, "", person_id="cy-topicless", note="founder again")
    before = box.rows("MATCH (:Person {id: 'cy-topicless'})-[:INTO]->(t) RETURN t.name")
    assert before
    memory.forget_note(box.g, a["note_id"])
    assert box.rows("MATCH (:Person {id: 'cy-topicless'})-[:INTO]->(t) RETURN t.name") == before


def test_person_from_a_note_goes_with_their_last_note(box):
    box.load((), (), "--force")
    a = memory.remember(box.g, "Hana Ghost", company="Zeta Forge", note="a", new_person=True)
    memory.remember(box.g, "Ian Other", company="Zeta Forge", note="b", new_person=True)
    memory.forget_note(box.g, a["note_id"])
    assert box.rows("MATCH (p:Person {name: 'Hana Ghost'}) RETURN count(p)") == [[0]]


# ---------- repeats ----------

def test_same_day_return_to_an_earlier_value_saves(box):
    box.load([row("Cy", "Topicless", "Calm Co")], (), "--force")
    memory.remember(box.g, "", person_id="cy-topicless", email="a@x.test")
    memory.remember(box.g, "", person_id="cy-topicless", email="b@x.test")
    r = memory.remember(box.g, "", person_id="cy-topicless", email="a@x.test")
    assert r["status"] == "saved" and box.person("cy-topicless", "email") == ["a@x.test"]
    assert memory.remember(box.g, "", person_id="cy-topicless", email="a@x.test")["status"] == "already_saved"


def test_same_follow_up_after_done_saves(box):
    box.load([row("Cy", "Topicless", "Calm Co")], (), "--force")
    f = memory.remember(box.g, "", person_id="cy-topicless", follow_up="send deck")
    memory.remember(box.g, "", follow_up_done=f["follow_up_id"])
    r = memory.remember(box.g, "", person_id="cy-topicless", follow_up="send deck")
    assert r["status"] == "saved"
    assert box.rows("MATCH (f:FollowUp {open: true})-[:FOR]->(:Person {id: 'cy-topicless'}) RETURN f.text") == [["send deck"]]


def test_same_day_return_to_an_earlier_job_saves(box):
    box.load([row("Cy", "Topicless", "Calm Co")], (), "--force")
    memory.remember(box.g, "", person_id="cy-topicless", company="Nova Graph")
    memory.remember(box.g, "", person_id="cy-topicless", company="Zeta Forge")
    r = memory.remember(box.g, "", person_id="cy-topicless", company="Nova Graph")
    assert r["status"] == "saved" and box.current("cy-topicless") == ["Nova Graph"]


# ---------- cities ----------

def test_failed_location_save_keeps_the_city(box):
    box.load([row("Lea", "Fail", "Fail Co")], [profile("lea-fail", [("Fail Co", "Eng", 2019, None)], "Oslo, Norway")], "--force")
    query = box.g.query
    down = {"once": True, "for_good": False}

    def breaks(cypher, params=None, **kwargs):
        if down["for_good"] and "ON CREATE SET c.name = city.name" in cypher:
            raise ConnectionError("connection is still down")
        if "c.source = 'agent', c.note = $nid" in cypher and down["once"]:
            down["once"] = False
            raise ConnectionError("connection dropped")
        return query(cypher, params, **kwargs)

    box.g.query = breaks
    with pytest.raises(ConnectionError):
        memory.remember(box.g, "", person_id="lea-fail", location="Rome")
    assert box.city("lea-fail") == ["Oslo"]
    assert box.person("lea-fail", "location") == ["Oslo, Norway"]
    assert box.notes() == []

    # The connection stays down, so the undo fails too. The next save puts the city back.
    down.update(once=True, for_good=True)
    with pytest.raises(ConnectionError):
        memory.remember(box.g, "", person_id="lea-fail", location="Rome")
    box.g.query = query
    assert box.city("lea-fail") == []
    memory.remember(box.g, "", person_id="lea-fail", note="the next save")
    assert box.city("lea-fail") == ["Oslo"]
    assert box.notes() == ["the next save"]


def test_city_comes_back_when_another_note_removed_it(box):
    box.load([row("Ana", "City", "City Co"), row("Ben", "City", "City Co")], (), "--force")
    ana = memory.remember(box.g, "", person_id="ana-city", location="Tromso")
    memory.remember(box.g, "", person_id="ben-city", location="Tromso")
    bergen = memory.remember(box.g, "", person_id="ben-city", location="Bergen")
    memory.forget_note(box.g, ana["note_id"])
    memory.forget_note(box.g, bergen["note_id"])
    assert box.city("ben-city") == ["Tromso"]


# ---------- the job report ----------

def test_report_counts_people_not_jobs(box, capsys):
    box.load([row("Cal", "Two", "Kestrel Old")], [profile("cal-two", [("Kestrel Old", "E", 2019, None)])], "--force")
    old = job_changes.read_jobs(box.g)
    box.load([row("Cal", "Two", "Nova One")], [profile("cal-two", [("Nova One", "E", 2025, None), ("Nova Two", "E", 2025, None)])], "--force")
    job_changes.report(job_changes.mark(box.g, old))
    out = capsys.readouterr().out
    assert "1 person changed jobs" in out and "Cal Two: Kestrel Old -> Nova One, Nova Two" in out


# ---------- the viewer's text box ----------

@pytest.mark.parametrize("text, name, company, event, note", [
    ("Met Dr. Jonas Lind from Kestrel at Graph Night, talked", "Dr. Jonas Lind", "Kestrel", "Graph Night", "talked"),
    ("Met Jonas from Node.js at Graph Night", "Jonas", "Node.js", "Graph Night", ""),
    ("Met Jonas Lind from Kestrel at Graph Night - talked at length", "Jonas Lind", "Kestrel", "Graph Night", "talked at length"),
])
def test_parse_met(text, name, company, event, note):
    assert memory.parse_met(text) == {"name": name, "company": company, "event": event, "note": note}


def test_forget_a_return_to_an_old_job_goes_back_one_step(box):
    box.load([row("Kai", "Back", "Kestrel Works")], (), "--force")
    memory.remember(box.g, "", person_id="kai-back", company="Nova Graph", note="moved")
    back = memory.remember(box.g, "", person_id="kai-back", company="Kestrel Works", note="back again")
    memory.forget_note(box.g, back["note_id"])
    assert box.person("kai-back", "company") == ["Nova Graph"]
    assert box.current("kai-back") == ["Nova Graph"]


def test_company_chain_skips_older_notes_with_the_same_company(box):
    box.load([row("Kai", "Back", "Kestrel Works")], (), "--force")
    memory.remember(box.g, "", person_id="kai-back", company="Nova Graph", note="moved")
    back = memory.remember(box.g, "", person_id="kai-back", company="Kestrel Works", note="back again")
    side = memory.remember(box.g, "", person_id="kai-back", company="Zeta Forge", ended_jobs=[], note="also advises")
    memory.forget_note(box.g, back["note_id"])
    memory.forget_note(box.g, side["note_id"])
    assert box.person("kai-back", "company") == ["Nova Graph"]


# ---------- the commands ----------

def test_view_before_export_says_what_to_do(box):
    box.load((), (), "--force")
    r = subprocess.run([sys.executable, "-m", "personal_brain.cli", "view"], capture_output=True, text=True, timeout=30,
                       env={**os.environ, "PB_HOME": str(box.home), "PB_GRAPH": GRAPH, "FALKORDB_URL": ""})
    assert r.returncode != 0 and "personal-brain export" in r.stderr and "Traceback" not in r.stderr


def test_help_says_this_is_an_mcp_server():
    r = subprocess.run([sys.executable, "-m", "personal_brain.cli", "--help"], capture_output=True, text=True)
    assert "MCP server" in r.stdout.splitlines()[0]


# ---------- found by the second review ----------

def test_forget_a_return_undoes_it_when_an_older_note_named_that_job(box):
    box.load([row("Kai", "Back", "Kestrel Works")], (), "--force")
    memory.remember(box.g, "", person_id="kai-back", company="Kestrel Works", note="still there")
    memory.remember(box.g, "", person_id="kai-back", company="Nova Graph", note="moved")
    back = memory.remember(box.g, "", person_id="kai-back", company="Kestrel Works", note="back again")
    memory.forget_note(box.g, back["note_id"])
    assert box.current("kai-back") == ["Nova Graph"]
    assert box.person("kai-back", "company") == ["Nova Graph"]


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_three_moves_undo_in_any_order(box, order):
    box.load([row("Kai", "Back", "Old Co")], (), "--force")
    notes = [memory.remember(box.g, "", person_id="kai-back", company=company, note=f"step {i}")["note_id"]
             for i, company in enumerate(["Kestrel Works", "Nova Graph", "Kestrel Works"])]
    for i in order:
        memory.forget_note(box.g, notes[i])
        now = box.current("kai-back")
        assert len(now) == 1 and box.person("kai-back", "company") == now
    assert box.current("kai-back") == ["Old Co"]


def test_forgetting_a_note_person_keeps_someone_else_with_that_name(box):
    connections = [row("Jonas", "Lind", "Kestrel Works")]
    box.load(connections, (), "--force")
    other = memory.remember(box.g, "Jonas Lind", note="a different Jonas", new_person=True)
    memory.forget_person(box.g, other["person"]["id"])
    box.load(connections)
    assert box.rows("MATCH (p:Person {id: 'jonas-lind'}) RETURN count(p)") == [[1]]


def test_load_says_who_it_left_out_by_name(box):
    box.load((), (), "--force")
    n = memory.remember(box.g, "Tia Gone", note="x", new_person=True)
    memory.forget_person(box.g, n["person"]["id"])
    r = box.load([row("Tia", "Gone", "Kestrel Forge")])
    assert "Tia Gone" in r.stdout and "forgotten.json" in r.stdout


def test_force_keeps_the_backup_of_another_graph(box):
    box.load((), (), "--force")
    other = {"nodes": [{"label": "Note", "props": {"id": "note-x", "text": "other graph"}}], "about": [], "links": [],
             "graph_deleted": True, "place": "local/other"}
    notes_backup.save(other)
    box.load((), (), "--force")
    assert notes_backup.pending() == other


def test_titles_from_an_older_version_stay_in_step(box):
    box.load([row("Cleo", "Chain", "Alpha Works", "First")], [profile("cleo-chain", [("Alpha Works", "First", 2020, None)])], "--force")
    memory.remember(box.g, "", person_id="cleo-chain", title="Second")
    box.g.query("MATCH ()-[w:WORKED_AT]->() SET w.title_log = NULL")    # 0.3.1 kept no list
    third = memory.remember(box.g, "", person_id="cleo-chain", title="Third")
    memory.forget_note(box.g, third["note_id"])
    assert box.person("cleo-chain", "title") == ["Second"] and box.jobs("cleo-chain") == [["Alpha Works", "Second", True]]

    fourth = memory.remember(box.g, "", person_id="cleo-chain", title="Fourth")
    box.g.query("MATCH ()-[w:WORKED_AT]->() SET w.title_log = NULL")
    memory.forget_note(box.g, fourth["note_id"])
    assert box.person("cleo-chain", "title") == ["Second"] and box.jobs("cleo-chain") == [["Alpha Works", "Second", True]]


def test_rebuild_keeps_a_noted_job_the_import_has_as_a_past_role(box):
    imported = ([row("Eva", "Back", "Old Co")], [profile("eva-back", [("Old Co", "Engineer", 2022, None), ("Kestrel Labs", "Engineer", 2019, 2022)])])
    box.load(*imported, "--force")
    rejoin = memory.remember(box.g, "", person_id="eva-back", company="Kestrel Labs", note="rejoined")
    moved = memory.remember(box.g, "", person_id="eva-back", company="Nova Graph", note="moved")
    box.load(*imported)
    assert box.current("eva-back") == ["Nova Graph"]
    memory.forget_note(box.g, moved["note_id"])
    assert box.current("eva-back") == ["Kestrel Labs"] and box.person("eva-back", "company") == ["Kestrel Labs"]
    memory.forget_note(box.g, rejoin["note_id"])
    assert box.current("eva-back") == ["Old Co"] and box.person("eva-back", "company") == ["Old Co"]


@pytest.mark.parametrize("writes", range(1, 8))
def test_a_save_that_died_and_a_retry_leave_one_current_job(box, writes):
    box.load([row("Kai", "Back", "Old Co")], (), "--force")
    query, left = box.g.query, [writes]

    def dies(cypher, params=None, **kwargs):
        if left[0] == 0:
            raise ConnectionError("this process died")
        left[0] -= 1
        return query(cypher, params, **kwargs)

    box.g.query = dies
    with pytest.raises(ConnectionError):
        memory.remember(box.g, "", person_id="kai-back", company="Nova Graph", note="moved")
    box.g.query = query
    memory.LOST_SAVES.clear()    # a new process does not know about the lost save
    assert memory.remember(box.g, "", person_id="kai-back", company="Nova Graph", note="moved")["status"] == "saved"
    box.g.query("MATCH (n:Note) WHERE n.saving IS NOT NULL SET n.saving = 1")    # later: the half-saved note is old now
    memory.remember(box.g, "", person_id="kai-back", note="the next save")
    assert box.rows("MATCH (n:Note) WHERE n.saving IS NOT NULL RETURN count(n)") == [[0]]
    assert box.current("kai-back") == ["Nova Graph"]


# ---------- a result holds at most 10,000 rows by default ----------

def test_backup_reads_past_the_row_limit(box):
    box.load([row("Rae", f"Cap{i}", "Cap Works") for i in range(30)], (), "--force")
    for i in range(6):
        memory.remember(box.g, f"Nell Hire{i}", new_person=True, company="Cap Works", note=f"hire {i}")
    whole = notes_backup.snapshot(box.g)
    assert len(whole["links"]) > 100
    db = memory.FalkorDB().connection
    size = db.execute_command("GRAPH.CONFIG", "GET", "RESULTSET_SIZE")[1]
    db.execute_command("GRAPH.CONFIG", "SET", "RESULTSET_SIZE", 10)
    try:
        capped = notes_backup.snapshot(box.g)
    finally:
        db.execute_command("GRAPH.CONFIG", "SET", "RESULTSET_SIZE", size)
    for part in ("nodes", "about", "links"):
        assert sorted(json.dumps(x, sort_keys=True) for x in capped[part]) == sorted(json.dumps(x, sort_keys=True) for x in whole[part])


# ---------- the sample must not take the place of a real network ----------

def run_load(home, *flags, export=None):
    env = {**os.environ, "PB_HOME": str(home), "FALKORDB_URL": ""}
    if export:
        env["LINKEDIN_EXPORT"] = str(export)
    return subprocess.run([sys.executable, "-m", "personal_brain.load_graph", "--graph", GRAPH, *flags],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env)


def test_sample_asks_before_it_replaces_a_real_network(box):
    export = box.home / "real"
    export.mkdir()
    (export / "Connections.csv").write_text("First Name,Last Name,URL,Email Address,Company,Position,Connected On\n" + row("Rory", "Real", "Real Co") + "\n", encoding="utf-8")
    assert run_load(box.home, export=export).returncode == 0
    box.g = memory.connect(GRAPH)[0]

    refused = run_load(box.home, "--sample")
    assert refused.returncode != 0 and "rory-real" == box.rows("MATCH (p:Person) RETURN p.id")[0][0]
    assert "Nothing changed" in refused.stderr and "Traceback" not in refused.stderr

    assert run_load(box.home, "--sample", "--yes").returncode == 0
    assert box.rows("MATCH (p:Person {id: 'rory-real'}) RETURN count(p)")[0][0] == 0
    assert run_load(box.home, "--sample").returncode == 0, "a sample can replace a sample with no question"


def test_sample_does_not_ask_when_only_notes_are_there(box):
    box.g = memory.connect(GRAPH)[0]
    memory.setup(box.g)
    memory.remember(box.g, "Una Note", new_person=True, note="kept")
    assert run_load(box.home, "--sample").returncode == 0
    assert box.notes() == ["kept"]


# ---------- the newest export wins ----------

@pytest.mark.parametrize("folders, newest", [
    (["Complete_LinkedInDataExport_12-01-2024", "Complete_LinkedInDataExport_03-04-2026"], "Complete_LinkedInDataExport_03-04-2026"),
    (["Complete_LinkedInDataExport_01-02-2025", "Basic_LinkedInDataExport_06-07-2026"], "Basic_LinkedInDataExport_06-07-2026"),
    (["Basic_LinkedInDataExport_06-07-2026", "Complete_LinkedInDataExport_06-07-2026"], "Complete_LinkedInDataExport_06-07-2026"),
    (["Basic_LinkedInDataExport_12-31-2025 (1)", "Basic_LinkedInDataExport_01-01-2025"], "Basic_LinkedInDataExport_12-31-2025 (1)"),
])
def test_newest_export_wins(tmp_path, monkeypatch, folders, newest):
    for name in folders:
        (tmp_path / name).mkdir()
        (tmp_path / name / "Connections.csv").write_text("x", encoding="utf-8")
    monkeypatch.setattr(load_graph, "HOME", tmp_path)
    monkeypatch.delenv("LINKEDIN_EXPORT", raising=False)
    assert load_graph.find_export().name == newest


def test_export_with_no_date_uses_its_file_date(tmp_path, monkeypatch):
    for name, year in (("Old_LinkedInDataExport", 2020), ("New_LinkedInDataExport", 2025)):
        (tmp_path / name).mkdir()
        file = tmp_path / name / "Connections.csv"
        file.write_text("x", encoding="utf-8")
        stamp = time.mktime((year, 6, 1, 0, 0, 0, 0, 0, -1))
        os.utime(file, (stamp, stamp))
    (tmp_path / "Zzz_LinkedInDataExport_01-01-2019").mkdir()
    (tmp_path / "Zzz_LinkedInDataExport_01-01-2019" / "Connections.csv").write_text("x", encoding="utf-8")
    monkeypatch.setattr(load_graph, "HOME", tmp_path)
    monkeypatch.delenv("LINKEDIN_EXPORT", raising=False)
    assert load_graph.find_export().name == "New_LinkedInDataExport"


# ---------- two notes that differ only in punctuation are two notes ----------

@pytest.mark.parametrize("first, second", [("Loves C++", "Loves C#"), ("+1 to the plan", "-1 to the plan"), ("🙂", "🙁")])
def test_punctuation_can_change_what_a_note_says(box, first, second):
    box.load([row("Pat", "Punct", "Punct Co")], (), "--force")
    one = memory.remember(box.g, "", person_id="pat-punct", note=first)
    two = memory.remember(box.g, "", person_id="pat-punct", note=second)
    assert (one["status"], two["status"]) == ("saved", "saved")
    assert box.notes() == sorted([first, second])


@pytest.mark.parametrize("retry", ["talked about graphs", "Talked  about GRAPHS", "talked about graphs.", "tálked about graphs"])
def test_a_retry_in_other_case_or_spacing_is_a_repeat(box, retry):
    box.load([row("Rey", "Retry", "Retry Co")], (), "--force")
    memory.remember(box.g, "", person_id="rey-retry", note="talked about graphs")
    assert memory.remember(box.g, "", person_id="rey-retry", note=retry)["status"] == "already_saved"


# ---------- ended_jobs must not say saved when it ended nothing ----------

def test_one_job_in_both_company_and_ended_jobs_saves_nothing(box):
    box.load([row("Eli", "Ended", "Stay Co")], (), "--force")
    with pytest.raises(ValueError, match="ended_jobs"):
        memory.remember(box.g, "", person_id="eli-ended", company="Stay Co", ended_jobs=["Stay Co"])
    assert box.current("eli-ended") == ["Stay Co"]
    assert box.notes() == []


# ---------- forget person writes the keep-out list first ----------

def test_forget_person_stops_before_it_deletes_if_the_list_is_broken(box):
    box.load([row("Fay", "Forget", "Forget Co")], (), "--force")
    memory.remember(box.g, "", person_id="fay-forget", note="a note")
    memory.FORGOTTEN.write_text("{broken", encoding="utf-8")
    with pytest.raises(SystemExit, match="forgotten.json"):
        memory.forget_person(box.g, "fay-forget")
    assert box.person("fay-forget", "name") == ["Fay Forget"]
    assert box.notes() == ["a note"]


def test_forget_person_is_on_the_list_even_if_the_delete_stops(box, monkeypatch):
    box.load([row("Gil", "Halfway", "Forget Co")], (), "--force")
    memory.remember(box.g, "", person_id="gil-halfway", note="a note")

    def stops(*_):
        raise RuntimeError("connection lost")

    monkeypatch.setattr(memory, "forget_note", stops)
    with pytest.raises(RuntimeError):
        memory.forget_person(box.g, "gil-halfway")
    assert "gil-halfway" in json.loads(memory.FORGOTTEN.read_text(encoding="utf-8"))


# ---------- someone left a job, with no new one ----------

def company_of(box, pid):
    return box.person(pid, "company")[0]


def test_ended_jobs_alone_ends_the_job_and_is_not_a_meeting(box):
    box.load([row("Lea", "Left", "Stay Co")], (), "--force")
    r = memory.remember(box.g, "", person_id="lea-left", ended_jobs=["Stay Co"])
    assert r["status"] == "saved" and r["changes"] == ["ended: Stay Co"]
    assert box.current("lea-left") == [] and company_of(box, "lea-left") == ""
    assert box.rows("MATCH (:Me)-[r:MET]->(:Person {id: 'lea-left'}) RETURN count(r)")[0][0] == 0
    assert memory.remember(box.g, "", person_id="lea-left", ended_jobs=["Stay Co"])["status"] == "already_saved"


def test_forgetting_it_brings_the_job_back(box):
    box.load([row("Lon", "Back", "Stay Co")], (), "--force")
    r = memory.remember(box.g, "", person_id="lon-back", ended_jobs=["Stay Co"])
    assert memory.forget_note(box.g, r["note_id"])["jobs_reopened"] == 1
    assert box.current("lon-back") == ["Stay Co"] and company_of(box, "lon-back") == "Stay Co"


def test_ended_jobs_keeps_the_other_job(box):
    box.load([row("Ann", "Two", "One Co")], (), "--force")
    memory.remember(box.g, "", person_id="ann-two", company="Two Co", ended_jobs=[], note="also advises")
    memory.remember(box.g, "", person_id="ann-two", ended_jobs=["Two Co"])
    assert box.current("ann-two") == ["One Co"] and company_of(box, "ann-two") == "One Co"
    memory.remember(box.g, "", person_id="ann-two", ended_jobs=["One Co"])
    assert box.current("ann-two") == [] and company_of(box, "ann-two") == ""


def test_a_same_job_note_can_end_the_other_job(box):
    box.load([row("Sam", "Same", "One Co")], (), "--force")
    memory.remember(box.g, "", person_id="sam-same", company="Two Co", ended_jobs=[], note="also advises")
    r = memory.remember(box.g, "", person_id="sam-same", company="One Co", ended_jobs=["Two Co"], note="only this one now")
    assert r["status"] == "saved" and box.current("sam-same") == ["One Co"]
    memory.forget_note(box.g, r["note_id"])
    assert box.current("sam-same") == ["One Co", "Two Co"]


def test_it_can_end_only_a_current_job(box):
    box.load([row("Kim", "Wrong", "One Co")], (), "--force")
    with pytest.raises(ValueError, match="not a current job"):
        memory.remember(box.g, "", person_id="kim-wrong", ended_jobs=["Other Co"])
    assert box.notes() == []


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_left_then_new_job_forgotten_in_either_order(box, order):
    box.load([row("Ned", "Next", "Old Co")], (), "--force")
    notes = [memory.remember(box.g, "", person_id="ned-next", ended_jobs=["Old Co"])["note_id"],
             memory.remember(box.g, "", person_id="ned-next", company="New Co", note="started there")["note_id"]]
    assert box.current("ned-next") == ["New Co"] and company_of(box, "ned-next") == "New Co"
    for i in order:
        memory.forget_note(box.g, notes[i])
    assert box.current("ned-next") == ["Old Co"] and company_of(box, "ned-next") == "Old Co"


def test_a_job_can_end_and_start_again_with_the_same_words(box):
    box.load([row("Ray", "Return", "Old Co")], (), "--force")
    memory.remember(box.g, "", person_id="ray-return", ended_jobs=["Old Co"])
    memory.remember(box.g, "", person_id="ray-return", company="Old Co", note="came back")
    r = memory.remember(box.g, "", person_id="ray-return", ended_jobs=["Old Co"])
    assert r["status"] == "saved" and box.current("ray-return") == []


def test_a_rebuild_keeps_the_job_ended(box):
    people = [row("Tia", "Gone", "Old Co")]
    box.load(people, (), "--force")
    r = memory.remember(box.g, "", person_id="tia-gone", ended_jobs=["Old Co"])
    box.load(people)
    assert box.current("tia-gone") == [] and company_of(box, "tia-gone") == ""
    memory.forget_note(box.g, r["note_id"])
    assert box.current("tia-gone") == ["Old Co"] and company_of(box, "tia-gone") == "Old Co"


# ---------- every search reads past the row limit ----------

def low_row_limit(rows=30):
    db = memory.FalkorDB().connection
    size = db.execute_command("GRAPH.CONFIG", "GET", "RESULTSET_SIZE")[1]
    db.execute_command("GRAPH.CONFIG", "SET", "RESULTSET_SIZE", rows)
    return lambda: db.execute_command("GRAPH.CONFIG", "SET", "RESULTSET_SIZE", size)


def test_about_reads_past_the_row_limit(box):
    box.load([row("Wes", f"Word{i}", "Word Co", "Zebra keeper") for i in range(40)], (), "--force")
    memory.remember(box.g, "", person_id="wes-word0", note="zebra chat")
    whole = memory.about(box.g, "zebra")
    restore = low_row_limit()
    try:
        capped = memory.about(box.g, "zebra")
    finally:
        restore()
    assert capped == whole and capped["found"] == 40


def test_forget_all_notes_goes_past_the_row_limit(box):
    box.load([row("Nan", "Many", "Many Co")], (), "--force")
    for i in range(130):
        memory.remember(box.g, "", person_id="nan-many", note=f"note {i}")
    restore = low_row_limit(100)    # not lower: the client reads the graph's field names with a query too
    try:
        memory.forget_all_notes(box.g)
    finally:
        restore()
    assert box.notes() == []


def test_a_person_with_a_skill_twice_shows_it_once(box):
    box.load([], (), "--force")
    people = memory.about(box.g, "python")["people"]
    assert any(w["from"] == "skill" for p in people for w in p["why"])
    for p in people:
        assert len(p["why"]) == len({(w["from"], w["text"]) for w in p["why"]})


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_company_follows_the_job_after_several_forgets(box, order):
    box.load([row("Cal", "Company", "Old Co")], (), "--force")
    notes = [memory.remember(box.g, "", person_id="cal-company", company="Third Co", note="moved")["note_id"],
             memory.remember(box.g, "", person_id="cal-company", company="Old Co", ended_jobs=[], note="back part time")["note_id"],
             memory.remember(box.g, "", person_id="cal-company", company="Third Co", ended_jobs=["Old Co"], note="only this one now")["note_id"]]
    for i in order:
        memory.forget_note(box.g, notes[i])
    assert box.current("cal-company") == ["Old Co"] and company_of(box, "cal-company") == "Old Co"


# ---------- a backup left by 0.3.1 says nothing about whether the graph went ----------

def old_backup(box):
    notes_backup.save(notes_backup.snapshot(box.g))


def test_old_backup_with_a_whole_graph_keeps_a_forgotten_note_out(box):
    connections = [row("Oda", "Keep", "Keep Co")]
    box.load(connections, (), "--force")
    memory.remember(box.g, "", person_id="oda-keep", note="stays", company="Keep Co")
    gone = memory.remember(box.g, "", person_id="oda-keep", note="private thing")
    old_backup(box)
    memory.forget_note(box.g, gone["note_id"])
    box.load(connections)
    assert box.notes() == ["stays"]


def test_old_backup_with_a_whole_graph_keeps_a_done_follow_up_done(box):
    connections = [row("Oda", "Keep", "Keep Co")]
    box.load(connections, (), "--force")
    fid = memory.remember(box.g, "", person_id="oda-keep", follow_up="send the deck")["follow_up_id"]
    old_backup(box)
    memory.remember(box.g, "", follow_up_done=fid)
    box.load(connections)
    assert box.rows("MATCH (f:FollowUp) RETURN f.open") == [[False]]


def test_half_built_graph_keeps_a_follow_up_done_since_the_backup(box):
    connections = [row("Oda", "Keep", "Keep Co")]
    box.load(connections, (), "--force")
    fid = memory.remember(box.g, "", person_id="oda-keep", follow_up="send the deck")["follow_up_id"]
    notes_backup.save({**notes_backup.snapshot(box.g), "graph_deleted": True, "place": PLACE})
    memory.remember(box.g, "", follow_up_done=fid)
    box.load(connections)
    assert box.rows("MATCH (f:FollowUp) RETURN f.open") == [[False]]


def test_old_backup_from_a_restore_that_stopped_brings_back_every_note(box):
    connections = [row("Oda", "Keep", "Keep Co")]
    box.load(connections, (), "--force")
    memory.remember(box.g, "", person_id="oda-keep", note="first")
    second = memory.remember(box.g, "", person_id="oda-keep", note="second")
    old_backup(box)
    # As the restore left it: the first note is back with no links yet, and the second is not back yet.
    box.g.query("MATCH (n:Note {id: $id}) DETACH DELETE n", {"id": second["note_id"]})
    box.g.query("MATCH (n:Note)-[r:ABOUT]->() DELETE r")
    box.load(connections)
    assert box.notes() == ["first", "second"]
    assert box.rows("MATCH (:Note)-[r:ABOUT]->(:Person {id: 'oda-keep'}) RETURN count(r)") == [[2]]


def test_old_backup_with_none_of_its_notes_left_keeps_them_and_says_so(box):
    connections = [row("Oda", "Keep", "Keep Co")]
    box.load(connections, (), "--force")
    only = memory.remember(box.g, "", person_id="oda-keep", note="only note")
    old_backup(box)
    memory.forget_note(box.g, only["note_id"])
    r = box.load(connections)
    assert box.notes() == ["only note"]    # the graph may be half-built: never lose a note on a guess
    assert "forget" in r.stdout


# ---------- a job named in two notes from 0.3.1 ----------

def as_031(box, nid, company="", event=""):
    """The note as 0.3.1 wrote it: no job_note and no saved_at, and a key with the words it was given."""
    text, day = box.rows("MATCH (n:Note {id: $id}) RETURN n.text, n.on", id=nid)[0]
    fold_key(box, nid, company, event, text, day, {})


def fold_key(box, nid, company, event, text, day, fields):
    key = hashlib.sha256(json.dumps([fold(company), fold(event), fold(text), day, sorted(fields.items())]).encode()).hexdigest()[:16]
    box.g.query("MATCH (n:Note {id: $id}) SET n.key = $key, n.saved_at = NULL, n.job_note = NULL", {"id": nid, "key": key})


def test_job_named_in_two_old_notes_stays_with_the_later_note(box):
    box.load([row("Oda", "Keep", "Keep Co")], (), "--force")
    first = memory.remember(box.g, "", person_id="oda-keep", company="Tern Works", note="started there")
    second = memory.remember(box.g, "", person_id="oda-keep", company="Tern Works", note="still there")
    as_031(box, first["note_id"], "Tern Works")
    as_031(box, second["note_id"], "Tern Works")
    memory.forget_note(box.g, first["note_id"])
    assert box.current("oda-keep") == ["Tern Works"]
    assert box.person("oda-keep", "company") == ["Tern Works"]
    memory.forget_note(box.g, second["note_id"])
    assert box.current("oda-keep") == ["Keep Co"]


def test_old_note_that_named_no_job_does_not_keep_it(box):
    box.load([row("Oda", "Keep", "Keep Co")], (), "--force")
    first = memory.remember(box.g, "", person_id="oda-keep", company="Tern Works", note="started there")
    other = memory.remember(box.g, "", person_id="oda-keep", note="talked about Tern Works")
    as_031(box, first["note_id"], "Tern Works")
    as_031(box, other["note_id"])
    memory.forget_note(box.g, first["note_id"])
    assert box.current("oda-keep") == ["Keep Co"]


def test_job_in_an_old_note_with_an_event_a_title_and_a_follow_up_stays(box):
    box.load([row("Oda", "Keep", "Keep Co")], (), "--force")
    first = memory.remember(box.g, "", person_id="oda-keep", company="Tern Works", note="started there")
    second = memory.remember(box.g, "", person_id="oda-keep", company="Tern Works", event="Graph Night", note="more",
                             title="Boss", follow_up="send it", due="2099-01-01")
    as_031(box, first["note_id"], "Tern Works")
    text, day = box.rows("MATCH (n:Note {id: $id}) RETURN n.text, n.on", id=second["note_id"])[0]
    fold_key(box, second["note_id"], "Tern Works", "Graph Night", text, day, {"title": "Boss", "follow_up": "send it", "due": "2099-01-01"})
    memory.forget_note(box.g, first["note_id"])
    assert box.current("oda-keep") == ["Tern Works"]


# ---------- the job report after a rebuild that stopped ----------

def load_that_stops(box, connections, *flags):
    """A real load that stops after it built the new graph, before the job report."""
    sample = box.home / "sample"
    shutil.rmtree(sample, ignore_errors=True)
    shutil.copytree(PROJECT / "src/personal_brain/sample_data", sample)
    file = sample / "linkedin/Connections.csv"
    file.write_text(file.read_text(encoding="utf-8").rstrip("\n") + "\n" + "\n".join(connections) + "\n", encoding="utf-8")
    code = ("import sys; sys.argv = ['load', '--sample', '--graph', %r] + %r\n"
            "from personal_brain import job_changes, load_graph\n"
            "job_changes.mark = lambda *a: sys.exit(3)\n"
            "load_graph.main()" % (GRAPH, list(flags)))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env={**os.environ, "PB_HOME": str(box.home), "PB_SAMPLE_DIR": str(sample), "FALKORDB_URL": ""})
    assert r.returncode == 3, r.stderr[-800:]


@pytest.mark.parametrize("flags", [(), ("--force",)])
def test_job_report_survives_a_rebuild_that_stopped(box, flags):
    box.load([row("Uma", "Bank", "Old Co")], (), "--force")
    load_that_stops(box, [row("Uma", "Bank", "New Co")], *flags)
    r = box.load([row("Uma", "Bank", "New Co")], (), *flags)
    assert "Uma Bank: Old Co -> New Co" in r.stdout


def test_job_report_survives_a_rebuild_that_stopped_with_notes(box):
    box.load([row("Uma", "Bank", "Old Co")], (), "--force")
    memory.remember(box.g, "", person_id="uma-bank", note="a note")
    load_that_stops(box, [row("Uma", "Bank", "New Co")])
    r = box.load([row("Uma", "Bank", "New Co")])
    assert "Uma Bank: Old Co -> New Co" in r.stdout and box.notes() == ["a note"]

