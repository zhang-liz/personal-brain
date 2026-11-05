"""Bad files, bad settings and a missing graph must end in a clear message, not a traceback. Each test has its own data folder."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import pytest

from personal_brain import export_stories, load_graph, memory, paths
from test_mcp_server import Server

PROJECT = Path(__file__).resolve().parent.parent
GRAPH = "pb_bad_input_test"
HEADER = "First Name,Last Name,URL,Email Address,Company,Position,Connected On"
ADA = "Ada,Lee,https://www.linkedin.com/in/ada,,Foo Co,Engineer,01 Jan 2026"
BRAIN = [sys.executable, "-m", "personal_brain.cli"]


class Folder:
    def __init__(self, path):
        self.path = path
        self.export = path / "x_LinkedInDataExport"
        self.export.mkdir()
        self.env = {**os.environ, "PB_HOME": str(path), "PB_GRAPH": GRAPH, "FALKORDB_URL": ""}

    def connections(self, *lines, start=HEADER):
        (self.export / "Connections.csv").write_text("\n".join([start, *lines]) + "\n", encoding="utf-8")

    def run(self, *args, **env):
        return subprocess.run([*BRAIN, *args], env={**self.env, **env}, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL, cwd=self.path)

    def people(self):
        g = memory.connect(GRAPH)[0]
        g.schema.clear()
        return sorted(g.ro_query("MATCH (p:Person) RETURN p.name, p.connected_on").result_set)


@pytest.fixture
def folder(tmp_path):
    yield Folder(tmp_path)
    graph = memory.connect(GRAPH)[0]
    try:
        graph.delete()
    except Exception:
        pass


def clean(result, *words):
    """It stopped, or it worked, with no traceback, and it said these words."""
    assert "Traceback" not in result.stderr, result.stderr[-600:]
    for word in words:
        assert word in result.stdout + result.stderr, result.stdout + result.stderr


# ---------- LinkedIn files ----------

@pytest.mark.parametrize("line", [
    "Ada,Lee,https://www.linkedin.com/in/ada,,Foo Co,Engineer Lead,01 Jan 2026",
    "Ada,Lee,https://www.linkedin.com/in/ada,,Foo Co,Engineer\x85Lead,01 Jan 2026",
    "Ada,Lee,https://www.linkedin.com/in/ada,,Foo Co,Engineer\x00Lead,01 Jan 2026",
    '"Ada","Lee","https://www.linkedin.com/in/ada","","Foo Co","Engineer\nLead","01 Jan 2026"',
])
def test_odd_characters_in_a_row(folder, line):
    folder.connections(line, "Bo,Ng,https://www.linkedin.com/in/bo,,Foo Co,Engineer,01 Jan 2026")
    clean(folder.run("load"))
    assert folder.people() == [["Ada Lee", "2026-01-01"], ["Bo Ng", "2026-01-01"]]


@pytest.mark.parametrize("line", ["Ada,Lee,https://www.linkedin.com/in/ada,,Foo Co,Engineer,", "Ada,Lee,https://www.linkedin.com/in/ada,,Foo Co,Engineer,not a date",
                                  "Ada,Lee,https://www.linkedin.com/in/ada"])
def test_a_row_with_no_date_keeps_the_person(folder, line):
    folder.connections(line, "Bo,Ng,https://www.linkedin.com/in/bo,,Foo Co,Engineer,01 Jan 2026")
    clean(folder.run("load"), "1 connection")
    assert folder.people() == [["Ada Lee", None], ["Bo Ng", "2026-01-01"]]
    clean(folder.run("export"))


def test_a_byte_order_mark_and_old_bytes(folder):
    (folder.export / "Connections.csv").write_bytes(b"\xef\xbb\xbf" + f"{HEADER}\n{ADA}\nJos\xe9,Ng,https://www.linkedin.com/in/jose,,Foo Co,Eng,01 Jan 2026\n".encode("latin-1"))
    (folder.export / "Profile.csv").write_bytes(b"\xef\xbb\xbfFirst Name,Last Name,Headline\nMe,Myself,Builder\n")
    clean(folder.run("load"))
    assert [name for name, _ in folder.people()][0] == "Ada Lee" and len(folder.people()) == 2
    assert memory.connect(GRAPH)[0].ro_query("MATCH (m:Me) RETURN m.name").result_set == [["Me Myself"]]


def test_the_notes_above_the_header_stay_out(folder):
    folder.connections(HEADER, ADA, start='Notes:\n"When exporting your connection data, you may notice that some of the"\n')
    clean(folder.run("load"))
    assert folder.people() == [["Ada Lee", "2026-01-01"]]


def test_a_file_that_is_not_an_export(folder):
    (folder.export / "Connections.csv").write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    clean(folder.run("load"), "First Name")


def test_empty_and_short_optional_files(folder):
    folder.connections(ADA)
    (folder.export / "Profile.csv").write_text("", encoding="utf-8")
    (folder.export / "messages.csv").write_text("CONVERSATION ID,SENDER PROFILE URL,RECIPIENT PROFILE URLS,DATE\nc1,https://www.linkedin.com/in/ada\n", encoding="utf-8")
    (folder.export / "Shares.csv").write_text("Date,ShareCommentary\n2026-01-01 10:00:00\nnot a date,words\n", encoding="utf-8")
    (folder.export / "Positions.csv").write_text("Company Name\n", encoding="utf-8")
    (folder.export / "Education.csv").write_text("Other\nx\n", encoding="utf-8")
    clean(folder.run("load"))
    assert folder.people() == [["Ada Lee", "2026-01-01"]]


@pytest.mark.parametrize("period, expected", [({"startDate": {"year": 2020, "month": None}}, 2020.0),
                                              ({"startDate": {"year": 2020}, "endDate": {"year": 2021, "month": None}}, 2020.0)])
def test_a_job_with_no_month(period, expected):
    assert load_graph.years(period)[0] == expected


# ---------- settings and urls ----------

def test_bad_settings_stop_only_load(folder):
    folder.connections(ADA)
    (folder.path / "my_brain.json").write_text("{bad", encoding="utf-8")
    clean(folder.run("load"), "my_brain.json")
    clean(folder.run("notes"))
    server = Server([*BRAIN, "mcp"], folder.path, folder.env)
    assert "error" not in server.call("remember", name="Bad Settings", new_person=True)
    server.stop()


def test_a_bad_event_date_in_settings(folder):
    folder.connections(ADA)
    (folder.path / "my_brain.json").write_text('{"events": {"soon": "Party"}}', encoding="utf-8")
    clean(folder.run("load"), "my_brain.json")


@pytest.mark.parametrize("line", ["FALKORDB_URL=redis://x:1", 'FALKORDB_URL="redis://x:1"', "FALKORDB_URL='redis://x:1'", "export FALKORDB_URL=redis://x:1",
                                  "  FALKORDB_URL = redis://x:1  ", "FALKORDB_URL=redis://x:1 # my cloud"])
def test_env_file_forms(tmp_path, monkeypatch, line):
    (tmp_path / ".env").write_text(f"# a comment\nOTHER=1\n{line}\n", encoding="utf-8")
    monkeypatch.setattr(paths, "HOME", tmp_path)
    assert paths.env_file_url() == "redis://x:1"


def test_a_commented_url_is_no_url(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("#FALKORDB_URL=redis://x:1\n", encoding="utf-8")
    monkeypatch.setattr(paths, "HOME", tmp_path)
    assert paths.env_file_url() == ""


@pytest.mark.parametrize("command", [["notes"], ["load", "--sample", "--cloud"], ["export", "--cloud"], ["view"]])
def test_a_bad_url_says_so_and_stays_secret(folder, command):
    r = folder.run(*command, FALKORDB_URL="http://user:secretword@host:1")
    assert r.returncode != 0
    clean(r, "FALKORDB_URL")
    assert "secretword" not in r.stdout + r.stderr


def test_a_bad_url_in_a_tool(folder):
    server = Server([*BRAIN, "mcp"], folder.path, {**folder.env, "FALKORDB_URL": "http://user:secretword@host:1"})
    error = server.call("find", name="Anyone")["error"]
    server.stop()
    assert "FALKORDB_URL" in error and "secretword" not in error


# ---------- no graph ----------

@pytest.mark.parametrize("command", [["notes"], ["followups"], ["export"]])
def test_no_graph_yet(folder, command):
    r = folder.run(*command)
    assert r.returncode != 0
    clean(r, "personal-brain load")
    assert GRAPH not in memory.FalkorDB().list_graphs(), "asking must not create the graph"


def test_a_tool_after_the_graph_was_wiped(folder):
    server = Server([*BRAIN, "mcp"], folder.path, folder.env)
    assert server.call("remember", name="First Note", new_person=True)["status"] == "saved"
    memory.connect(GRAPH)[0].delete()
    assert server.call("remember", name="Second Note", new_person=True)["status"] == "saved"
    memory.connect(GRAPH)[0].delete()
    assert server.call("find", name="Nobody")["found"] == 0
    server.stop()


# ---------- the viewer's server ----------

@pytest.fixture
def viewer(folder):
    folder.connections(ADA)
    clean(folder.run("load"))
    clean(folder.run("export"))
    process = subprocess.Popen([*BRAIN, "view"], env=folder.env, cwd=folder.path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               preexec_fn=lambda: signal.signal(signal.SIGINT, signal.default_int_handler))
    for _ in range(50):
        try:
            urllib.request.urlopen("http://127.0.0.1:8765/graph.json", timeout=1)
            break
        except OSError:
            time.sleep(0.2)
    yield process
    process.kill()
    process.communicate()


def post(text):
    request = urllib.request.Request("http://127.0.0.1:8765/api/remember", json.dumps({"text": text}).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_view_twice_and_ctrl_c(folder, viewer):
    r = folder.run("view")
    assert r.returncode != 0
    clean(r, "8765")
    viewer.send_signal(signal.SIGINT)
    _, err = viewer.communicate(timeout=10)
    assert "Traceback" not in err, err[-500:]


def test_long_text_is_refused_not_cut(folder, viewer):
    status, body = post("Met Long Note, " + "word " * 200)
    assert status == 400 and "500" in body["error"]
    assert not memory.connect(GRAPH)[0].ro_query("MATCH (p:Person {name: 'Long Note'}) RETURN p").result_set


def test_viewer_after_the_graph_was_wiped(folder, viewer):
    memory.connect(GRAPH)[0].delete()
    status, body = post("Met Some One")
    assert status == 500 and "personal-brain load" in body["error"]
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen("http://127.0.0.1:8765/api/delta?since=0", timeout=10)
    assert e.value.code == 500 and "personal-brain load" in json.load(e.value)["error"]


# ---------- terminal text ----------

def test_forget_shows_no_control_characters(folder):
    g = memory.connect(GRAPH)[0]
    memory.setup(g)
    saved = memory.remember(g, "Esc\x1b[2J Person", new_person=True, note="hidden\x1b[2Jtext")
    r = folder.run("forget", "note", saved["note_id"])
    assert "\x1b" not in r.stdout and "hidden" in r.stdout
    r = folder.run("forget", "person", saved["person"]["id"], "--yes")
    assert "\x1b" not in r.stdout


def test_graph_flag_with_no_name(folder):
    r = folder.run("load", "--sample", "--graph")
    assert r.returncode != 0
    clean(r, "--graph")


# ---------- sample that does not go out of date ----------

def test_sample_events_stay_upcoming(folder):
    sample = folder.path / "sample"
    shutil.copytree(PROJECT / "src/personal_brain/sample_data", sample)
    events = sample / "enriched/events_sample.json"
    data = json.loads(events.read_text(encoding="utf-8"))
    for e in data["items"]:
        e["startAt"] = "2020" + e["startAt"][4:]
    events.write_text(json.dumps(data), encoding="utf-8")
    clean(folder.run("load", "--sample", PB_SAMPLE_DIR=str(sample)))
    days = [d for (d,) in memory.connect(GRAPH)[0].ro_query("MATCH (e:Event {upcoming: true}) RETURN e.date").result_set]
    assert len(days) == len(data["items"]) and min(days) >= date.today().isoformat()


def test_founders_story_looks_back_one_year():
    cypher = next(s for s in export_stories.STORIES if s["id"] == "founders")["cypher"]
    assert f"{date.today().year}-01-01" not in cypher


def test_sample_loads_with_no_time_zone_data(folder):
    clean(folder.run("load", "--sample", PYTHONTZPATH=""))
    assert len(folder.people()) > 200


# ---------- the viewer's text box ----------

@pytest.mark.parametrize("text, name, company, event, note", [
    ("Met J. Lind", "J. Lind", "", "", ""),
    ("Met J. R. Lind from Foo", "J. R. Lind", "Foo", "", ""),
    ("Met St. John from Foo", "St. John", "Foo", "", ""),
    ("Met Mary J. Blige at Bar, great chat", "Mary J. Blige", "", "Bar", "great chat"),
])
def test_initials_and_st_stay_in_the_name(text, name, company, event, note):
    assert memory.parse_met(text) == {"name": name, "company": company, "event": event, "note": note}


def test_live_updates_read_past_the_row_limit(folder, viewer):
    g = memory.connect(GRAPH)[0]
    for i in range(8):
        memory.remember(g, f"Live Person{i}", new_person=True, note=f"note {i}")
    read = lambda: json.load(urllib.request.urlopen("http://127.0.0.1:8765/api/delta?since=0", timeout=10))
    whole = read()
    db = memory.FalkorDB().connection
    size = db.execute_command("GRAPH.CONFIG", "GET", "RESULTSET_SIZE")[1]
    db.execute_command("GRAPH.CONFIG", "SET", "RESULTSET_SIZE", 3)
    try:
        capped = read()
    finally:
        db.execute_command("GRAPH.CONFIG", "SET", "RESULTSET_SIZE", size)
    assert len(capped["nodes"]) == len(whole["nodes"]) > 8 and len(capped["edges"]) == len(whole["edges"]) > 8


# ---------- the viewer's files stay true ----------

def stories_from(folder):
    r = subprocess.run([sys.executable, "-m", "personal_brain.export_stories"], env=folder.env, capture_output=True, text=True, cwd=folder.path)
    assert r.returncode == 0, r.stderr[-500:]
    return {s["id"]: s for s in json.loads((folder.path / "data/viewer/stories.json").read_text(encoding="utf-8"))}


def test_stories_read_past_the_row_limit(folder):
    clean(folder.run("load", "--sample"))
    whole = stories_from(folder)
    assert max(s["rows"] for s in whole.values()) > 20
    db = memory.FalkorDB().connection
    size = db.execute_command("GRAPH.CONFIG", "GET", "RESULTSET_SIZE")[1]
    db.execute_command("GRAPH.CONFIG", "SET", "RESULTSET_SIZE", 20)
    try:
        capped = stories_from(folder)
    finally:
        db.execute_command("GRAPH.CONFIG", "SET", "RESULTSET_SIZE", size)
    for name, story in whole.items():
        assert capped[name]["rows"] == story["rows"], name
        assert capped[name]["nodes"] == story["nodes"], name
        assert sorted(capped[name]["pairs"]) == sorted(story["pairs"]), name


def test_a_story_that_has_two_parts_counts_a_row_once(folder):
    clean(folder.run("load", "--sample"))
    g = memory.connect(GRAPH)[0]
    rows = g.ro_query(next(s for s in export_stories.STORIES if s["id"] == "speak")["cypher"]).result_set
    assert stories_from(folder)["speak"]["rows"] == len(rows)


def test_load_writes_the_viewer_files_again(folder):
    clean(folder.run("load", "--sample"))
    clean(folder.run("export"))
    file = folder.path / "data/viewer/graph.json"
    file.write_text('{"nodes": []}', encoding="utf-8")
    clean(folder.run("load", "--sample", "--yes"))
    assert len(json.loads(file.read_text(encoding="utf-8"))["nodes"]) > 200


def test_load_of_another_graph_leaves_the_viewer_files(folder):
    clean(folder.run("load", "--sample"))
    clean(folder.run("export"))
    file = folder.path / "data/viewer/graph.json"
    file.write_text('{"nodes": []}', encoding="utf-8")
    other = GRAPH + "_other"
    try:
        clean(folder.run("load", "--sample", "--yes", "--graph", other))
    finally:
        memory.connect(other)[0].delete()
    assert json.loads(file.read_text(encoding="utf-8")) == {"nodes": []}


def test_activity_says_when_the_viewer_files_change(folder, viewer):
    read = lambda: json.load(urllib.request.urlopen("http://127.0.0.1:8765/api/activity?since=-1", timeout=10))["map"]
    before = read()
    assert read() == before
    clean(folder.run("export"))
    assert read() != before


# ---------- what the tools say back ----------

@pytest.fixture
def tools(folder):
    server = Server([*BRAIN, "mcp"], folder.path, folder.env)
    yield server
    server.stop()


def raw(server, tool, **arguments):
    return server.request("tools/call", {"name": tool, "arguments": arguments})["result"]


@pytest.mark.parametrize("cypher", [
    "LOAD CSV FROM 'https://127.0.0.1:1/x.csv' AS row RETURN row",
    "load   csv from 'https://127.0.0.1:1/x.csv' AS row RETURN row",
    "LOAD /* x */ CSV FROM 'https://127.0.0.1:1/x.csv' AS row RETURN row",
    "LOAD\nCSV FROM $url AS row RETURN row",
    "MATCH (p:Person) WITH p LOAD CSV FROM 'https://127.0.0.1:1/?' + p.name AS row RETURN row",
])
def test_ask_cannot_send_data_out(tools, cypher):
    """LOAD CSV works in a read-only query, and its URL can hold anything from the graph."""
    assert "internet" in tools.call("ask", cypher=cypher, params={"url": "https://127.0.0.1:1/x.csv"})["error"]


def test_an_error_is_marked_as_an_error(tools):
    assert raw(tools, "find")["isError"] is True
    assert raw(tools, "ask", cypher="MATCH (p:Person RETURN p")["isError"] is True
    assert not raw(tools, "find", name="Nobody").get("isError")


def test_the_viewer_still_lights_up_what_find_shows(tools, folder):
    tools.call("remember", name="Jonas Weber", new_person=True)
    tools.call("find", name="Jonas Weber", label="Person")
    last = json.loads((folder.path / "data" / "activity.json").read_text(encoding="utf-8"))["events"][-1]
    assert last["tool"] == "find" and last["names"] == ["Jonas Weber"]


def test_a_reply_is_one_compact_block(tools):
    tools.call("remember", name="Jonas Weber", new_person=True)
    tools.call("remember", name="Jonas Fischer", new_person=True)
    content = raw(tools, "find", name="Jonas")["content"]
    assert len(content) == 1 and "\n" not in content[0]["text"] and json.loads(content[0]["text"])["found"] == 2


def test_find_shows_each_person_their_own_company(tools):
    """FalkorDB 6 mixed up people when it grouped what a full-text search returned."""
    for number in range(6):
        tools.call("remember", name=f"Jonas Number{number}", new_person=True, company=f"Kestrel Works{number}", follow_up=f"Call {number}")
    for match in tools.call("find", name="Jonas", label="Person")["matches"]:
        number = match["name"][-1]
        assert match["works_at"] == [f"Kestrel Works{number}"] and match["follow_ups"][0]["text"] == f"Call {number}"
    assert tools.call("find", name="Kestrel Works3", label="Company")["matches"][0]["people_you_know_there"] == 1


def test_find_says_when_it_shows_only_some(tools):
    for number in range(12):
        tools.call("remember", name=f"Jonas Number{number}", new_person=True)
    some = tools.call("find", name="Jonas", label="Person")
    assert len(some["matches"]) == 10 and some["more"] is True
    assert "more" not in tools.call("find", name="Jonas Number7", label="Person")


def test_ask_gives_no_wrong_total_when_it_cuts_rows(tools):
    tools.call("remember", name="Jonas Weber", new_person=True)
    whole = tools.call("ask", cypher="UNWIND range(1, 3) AS x RETURN x")
    assert whole["row_count"] == 3 and whole["truncated"] is False
    cut = tools.call("ask", cypher="UNWIND range(1, 20000) AS x RETURN x")
    assert len(cut["rows"]) == 50 and cut["truncated"] is True
    assert "row_count" not in cut and "count()" in cut["message"]


def test_ask_keeps_a_reply_small_enough_to_read(tools):
    tools.call("remember", name="Jonas Weber", new_person=True)
    many = raw(tools, "ask", cypher="UNWIND range(1, 50) AS x RETURN x, $big", params={"big": "x" * 5000})
    assert 5000 < len(many["content"][0]["text"]) <= 45000
    assert json.loads(many["content"][0]["text"])["truncated"] is True
    one = tools.call("ask", cypher="RETURN $big", params={"big": "x" * 100000})
    assert one["rows"] == [] and one["truncated"] is True and "fewer fields" in one["message"]


def test_find_about_keeps_a_long_note_short(tools):
    tools.call("remember", name="Jonas Weber", new_person=True, note="pipes " * 5000)
    reply = tools.call("find", about="pipes")
    assert reply["people"][0]["name"] == "Jonas Weber" and len(reply["people"][0]["why"][0]["text"]) <= 300


def test_a_slow_query_over_a_url_is_not_a_dead_database(folder):
    server = Server([*BRAIN, "mcp"], folder.path, {**folder.env, "FALKORDB_URL": "redis://localhost:6379"})
    server.call("remember", name="Jonas Weber", new_person=True)
    error = server.call("ask", cypher="UNWIND range(1, 100000) AS a UNWIND range(1, 100000) AS b WITH a, b WHERE a + b < 0 RETURN count(a)")["error"]
    server.stop()
    assert "LIMIT" in error and "Cannot reach" not in error


def test_a_silent_database_cannot_hold_a_tool_forever():
    assert memory.connect(GRAPH)[0].client.connection.connection_pool.connection_kwargs["socket_timeout"]


def test_a_misspelled_field_saves_nothing(tools):
    reply = raw(tools, "remember", name="Jonas Weber", new_person=True, notes="This note would be lost")
    assert reply["isError"] is True and "notes" in reply["content"][0]["text"]
    assert tools.call("find", name="Jonas Weber")["found"] == 0


def test_remember_needs_no_name_to_close_a_follow_up(tools):
    saved = tools.call("remember", name="Jonas Weber", new_person=True, follow_up="Send the deck")
    assert tools.call("remember", follow_up_done=saved["follow_up_id"])["status"] == "saved"
    assert "error" in tools.call("remember")


def test_tools_say_how_they_behave(tools):
    listed = {t["name"]: t for t in tools.request("tools/list")["result"]["tools"]}
    assert all(t["annotations"]["openWorldHint"] is False and t.get("title") for t in listed.values())
    assert listed["remember"]["annotations"]["idempotentHint"] is True
    assert listed["find"]["annotations"]["readOnlyHint"] is True and listed["ask"]["annotations"]["readOnlyHint"] is True


def test_the_sample_has_no_messages_from_the_future():
    """The agent called a last message after today a data error, on the first question a new user asked."""
    sample = PROJECT / "src" / "personal_brain" / "sample_data" / "linkedin" / "messages.csv"
    days = [line.split(",")[-1][:10] for line in sample.read_text(encoding="utf-8").splitlines()[1:]]
    assert days and max(days) <= "2026-11-04"


def test_the_schema_shows_a_my_network_query_that_runs(tools):
    """Agents wrote EXISTS((:Me)-[:MET]->(p)) for "my network", which FalkorDB cannot run."""
    example = "MATCH (:Me)-[:CONNECTED|MET]->(p:Person)"
    assert example in memory.SCHEMA
    tools.call("remember", name="Jonas Weber", new_person=True)
    assert tools.call("ask", cypher=example + " RETURN count(DISTINCT p)")["rows"] == [[1]]


def test_one_follow_up_is_one(folder):
    folder.connections(ADA)
    clean(folder.run("load"))
    server = Server([*BRAIN, "mcp"], folder.path, folder.env)
    server.call("remember", name="Ada Lee", follow_up="Send the deck")
    server.stop()
    assert "1 open follow-up on" in folder.run("followups").stdout


def test_the_schema_shows_how_to_get_past_the_falkordb_6_collect_bug(tools):
    """FalkorDB 6 finds nothing for a filtered OPTIONAL MATCH after a WITH that collects. The schema shows a way around."""
    way_around = "CALL { WITH p OPTIONAL MATCH (f:FollowUp {open: true})-[:FOR]->(p) RETURN collect(f.text) AS todo }"
    assert way_around in memory.SCHEMA
    pid = tools.call("remember", name="Jonas Weber", company="Kestrel", follow_up="Send the deck", new_person=True)["person"]["id"]
    rows = tools.call("ask", cypher="MATCH (p:Person {id: $id}) OPTIONAL MATCH (p)-[:WORKED_AT {current: true}]->(c:Company) "
                      "WITH p, collect(c.name) AS jobs " + way_around + " RETURN jobs, todo", params={"id": pid})["rows"]
    assert rows == [[["Kestrel"], ["Send the deck"]]]
