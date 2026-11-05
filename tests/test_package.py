"""Build the package and use it the way someone who installed it does: another folder, a new HOME, no repo.

Needs uv and local FalkorDB (graph "pb_package_test", deleted at the end).

    .venv/bin/python tests/test_package.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from personal_brain import __version__
from test_mcp_server import Server, check

PROJECT = Path(__file__).resolve().parent.parent
TEST_GRAPH = "pb_package_test"


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        subprocess.run(["uv", "build", "--wheel", "--out-dir", str(tmp / "dist"), str(PROJECT)], check=True, capture_output=True)
        wheel = next((tmp / "dist").glob("*.whl"))
        names = zipfile.ZipFile(wheel).namelist()
        check("personal_brain/viewer/index.html" in names and "personal_brain/viewer/vendor/three/build/three.module.js" in names,
              "wheel holds the viewer")
        check("personal_brain/sample_data/linkedin/Connections.csv" in names, "wheel holds the sample network")
        check(not [n for n in names if "shortlists" in n], "wheel holds no shortlists")
        private = [n for n in names if n.endswith(".env") or n.startswith("data/") or "LinkedInDataExport" in n
                   or n.endswith("my_brain.json")]
        check(not private, "wheel holds no private files")

        subprocess.run(["uv", "venv", "--python", sys.executable, str(tmp / "venv")], check=True, capture_output=True)
        subprocess.run(["uv", "pip", "install", "--python", str(tmp / "venv/bin/python"), str(wheel)], check=True, capture_output=True)
        brain = str(tmp / "venv/bin/personal-brain")
        user = tmp / "user"
        user.mkdir()
        env = {k: v for k, v in os.environ.items() if k not in ("FALKORDB_URL", "PB_HOME", "PB_GRAPH")}
        env.update(HOME=str(user), PB_GRAPH=TEST_GRAPH)
        home = user / ".personal-brain"

        def run(*args, answer="", **extra):
            return subprocess.run([brain, *args], cwd=user, env={**env, **extra}, text=True, capture_output=True, input=answer)

        readme = (PROJECT / "README.md").read_text(encoding="utf-8")
        check(readme.split("\n")[2].startswith("**An MCP server"), "the README says first that this is an MCP server")
        check('description = "MCP server' in (PROJECT / "pyproject.toml").read_text(encoding="utf-8"), "the package description says first that this is an MCP server")
        check(not [l for l in readme.splitlines() if "docker run" in l and "falkordb_data:/var/lib/falkordb/data" not in l], "every start command in the README keeps data in a volume")
        check(run("--version").stdout.strip().endswith(__version__), "--version works")
        r = run()
        check(r.returncode == 0 and all(c in r.stdout for c in ["mcp", "load", "export", "view"]), "no command: lists the commands")

        # .env in the data folder: the server reads it. Port 6399 has no database, so the error shows that it did.
        home.mkdir()
        (home / ".env").write_text("FALKORDB_URL=redis://localhost:6399\n")
        server = Server([brain, "mcp"], user, env)
        error = server.call("find", name="Anyone")["error"]
        check("6399" in error, "server reads .env from ~/.personal-brain")
        server.stop()
        (home / ".env").unlink()
        home.rmdir()

        try:
            r = run("load", "--sample", "--graph", TEST_GRAPH, "--force")
            check(r.returncode == 0, "load --sample works from another folder" + (": " + r.stderr[-300:] if r.returncode else ""))
            check(home.is_dir() and home.stat().st_mode & 0o077 == 0, "data folder is private to the user")

            server = Server([brain, "mcp"], user, env)
            found = server.call("find", name="Kestrel Graph", label="Company")
            check(found and found[0]["name"] == "Kestrel Graph", "mcp finds the sample network")
            saved = server.call("remember", name="Packaged Pat", note="installed from a wheel", new_person=True)

            def exists(name):
                return isinstance(server.call("find", name=name, label="Person"), list)

            check(run("forget").returncode != 0, "forget with nothing to forget: says how to use it")
            r = run("forget", "person", saved["person"]["id"])
            check(r.returncode != 0 and exists("Packaged Pat"), "forget person: no answer keeps them")
            r = run("forget", "person", saved["person"]["id"], answer="n\n")
            check(r.returncode != 0 and exists("Packaged Pat"), "forget person: answer n keeps them")
            r = run("forget", "person", saved["person"]["id"], answer="y\n")
            check(r.returncode == 0 and not exists("Packaged Pat") and (home / "data/forgotten.json").exists(),
                  "forget person: answer y deletes them and writes to the data folder" + (": " + r.stderr[-300:] if r.returncode else ""))

            server.call("remember", name="Named Nia", new_person=True)
            r = run("forget", "person", "Named Nia", "--yes")
            check(r.returncode == 0 and not exists("Named Nia"), "forget person by name")

            first = server.call("remember", name="Twin Tia", new_person=True)
            server.call("remember", name="Twin Tia", new_person=True)
            r = run("forget", "person", "Twin Tia", "--yes")
            check(r.returncode != 0 and first["person"]["id"] in r.stdout + r.stderr and exists("Twin Tia"),
                  "forget person: same name twice lists ids and deletes nobody")
            check(run("forget", "person", "Nobody Here", "--yes").returncode != 0, "forget person: unknown name fails")

            listed = server.call("remember", name="Listed Lee", company="Lee Co", note="words to review", new_person=True)
            r = run("notes")
            check(r.returncode == 0 and all(w in r.stdout for w in [listed["note_id"], "Listed Lee", "words to review"]),
                  "notes lists each note with its id, person and text")
            todo = server.call("remember", name="", person_id=listed["person"]["id"], follow_up="Send the deck", due="2030-01-15")
            later = server.call("remember", name="", person_id=listed["person"]["id"], follow_up="No date yet")
            r = run("followups")
            check(r.returncode == 0 and all(w in r.stdout for w in ["2030-01-15", todo["follow_up_id"], "Listed Lee", "Send the deck"]),
                  "followups lists what is open, with the date, id, person and text")
            check(r.stdout.index("Send the deck") < r.stdout.index("No date yet"), "followups puts dated ones first")
            server.call("remember", name="", follow_up_done=todo["follow_up_id"])
            check("Send the deck" not in run("followups").stdout, "followups hides what is done")
            run("forget", "note", later["note_id"], "--yes")
            older = server.call("remember", name="Older Olu", note="older words", on="2020-01-01", new_person=True)
            r = run("notes")
            check(r.stdout.index("Listed Lee") < r.stdout.index("Older Olu"), "notes puts the newest first")
            run("forget", "note", older["note_id"], "--yes")
            r = run("notes", "--limit", "1")
            check(r.returncode == 0 and "1 of" in r.stdout and r.stdout.count("note-") == 1, "notes --limit shows fewer and says so")
            check(run("forget", "note", listed["note_id"], "--yes").returncode == 0 and listed["note_id"] not in run("notes").stdout,
                  "a note from the list can be deleted")
            note = server.call("remember", name="", person_id=first["person"]["id"], note="undo this text")
            r = run(*note["undo"].split()[1:], "--yes")
            rows = server.call("ask", cypher="MATCH (n:Note {id: $id}) RETURN n.id", params={"id": note["note_id"]})["rows"]
            check(r.returncode == 0 and rows == [], "the undo command remember gives removes the note")
            check(run("forget", "note", "note-nope", "--yes").returncode != 0, "forget note: unknown id fails")

            # Windows does not default to UTF-8. Make this system act the same way.
            ascii_only = dict(PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", LC_ALL="C")
            non_latin = server.call("remember", name="山田 太郎", company="株式会社テスト", note="図のデータベース", new_person=True)
            r = run("notes", **ascii_only)
            check(r.returncode == 0 and non_latin["note_id"] in r.stdout, "notes works with non-Latin text when the system default is not UTF-8" + (": " + r.stderr[-200:] if r.returncode else ""))
            r = run("load", "--sample", "--graph", TEST_GRAPH, **ascii_only)
            check(r.returncode == 0 and "Kept" in r.stdout, "load keeps non-Latin notes when the system default is not UTF-8" + (": " + r.stderr[-200:] if r.returncode else ""))
            r = run("export", **ascii_only)
            check(r.returncode == 0, "export works when the system default is not UTF-8" + (": " + r.stderr[-200:] if r.returncode else ""))
            r = run("forget", "person", non_latin["person"]["id"], "--yes", **ascii_only)
            check(r.returncode == 0, "forget works with non-Latin text when the system default is not UTF-8" + (": " + r.stderr[-200:] if r.returncode else ""))
            older = server.call("remember", name="Gap Gale", note="made first", new_person=True)
            newer = server.call("remember", name="Stay Sato", note="made after", new_person=True)
            place = server.call("ask", cypher="MATCH (p:Person {id: $id}) RETURN id(p)", params={"id": newer["person"]["id"]})["rows"][0][0]
            r = run("forget", "person", older["person"]["id"], "--yes")
            check(r.returncode == 0, "forget an older person, then the viewer's files update" + (": " + r.stderr[-200:] if r.returncode else ""))
            r = run("export")
            viewer = json.loads((home / "data/viewer/graph.json").read_text(encoding="utf-8"))
            check(r.returncode == 0 and viewer["nodes"][place][1] == "Stay Sato", "after a forget, a viewer place still matches the id in the graph")
            server.stop()

            r = run("export")
            check(r.returncode == 0 and (home / "data/viewer/graph.json").exists(),
                  "export writes to the data folder" + (": " + r.stderr[-300:] if r.returncode else ""))
            check(not [f for f in (home / "data").rglob("*") if "shortlists" in f.name], "export writes no shortlists")

            # Forgetting someone also takes them out of the viewer's files, and it asks nothing else of you.
            viewer_files = lambda: "".join(f.read_text() for f in (home / "data/viewer").glob("*.json"))
            server = Server([brain, "mcp"], user, env)
            memory_note = server.call("remember", name="Viewer Vera", company="Vera Co", note="secret viewer words", new_person=True)
            server.stop()
            check(run("export").returncode == 0 and "Viewer Vera" in viewer_files(), "an exported person is in the viewer's files")
            r = run("forget", "person", "Viewer Vera", "--yes")
            check(r.returncode == 0 and "Viewer Vera" not in viewer_files() and "Vera Co" not in viewer_files(),
                  "forget person takes them out of the viewer's files" + (": " + r.stderr[-300:] if r.returncode else ""))
            server = Server([brain, "mcp"], user, env)
            memory_note = server.call("remember", name="Note Nell", note="more words", new_person=True)
            server.stop()
            run("export")
            check("Note Nell" in viewer_files(), "a person from a note is in the viewer's files")
            r = run("forget", "note", memory_note["note_id"], "--yes")
            check(r.returncode == 0 and "Note Nell" not in viewer_files(), "forget note takes what it added out of the viewer's files")
            # With no viewer files, forget does not make any.
            viewer = home / "data/viewer"
            viewer.rename(home / "data/viewer-kept")
            server = Server([brain, "mcp"], user, env)
            memory_note = server.call("remember", name="Nofile Ned", new_person=True)
            server.stop()
            r = run("forget", "note", memory_note["note_id"], "--yes")
            check(r.returncode == 0 and not viewer.exists(), "forget makes no viewer files when you have none")
            (home / "data/viewer-kept").rename(viewer)

            view = subprocess.Popen([brain, "view"], cwd=user, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(30):
                    try:
                        page = urllib.request.urlopen("http://127.0.0.1:8765/").read().decode()
                        break
                    except OSError:
                        time.sleep(0.5)
                graph = json.loads(urllib.request.urlopen("http://127.0.0.1:8765/graph.json").read())
                check("three" in page and graph["nodes"], "view serves the page and the graph")
                check('id="forget"' not in page and "/api/forget" not in page, "the page has no delete button")
                check("shortlists" not in page.lower(), "the page has no shortlists button")

                def post(path, text, **headers):
                    request = urllib.request.Request("http://127.0.0.1:8765" + path, json.dumps({"text": text}).encode(), headers)
                    try:
                        return urllib.request.urlopen(request).status
                    except urllib.error.HTTPError as e:
                        return e.code

                def get_with(path, **headers):
                    try:
                        return urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8765" + path, headers=headers)).status
                    except urllib.error.HTTPError as e:
                        return e.code

                def post_raw(body, length=None):
                    """The status and JSON reply for a body that may be broken. A dropped connection is a failure."""
                    import http.client
                    connection = http.client.HTTPConnection("127.0.0.1", 8765, timeout=10)
                    connection.putrequest("POST", "/api/remember")
                    connection.putheader("Content-Length", str(len(body) if length is None else length))
                    connection.endheaders()
                    connection.send(body)
                    reply = connection.getresponse()
                    return reply.status, json.loads(reply.read())

                for label, body in {"broken JSON": b"{not json", "JSON that is not an object": b"[1, 2]",
                                    "text that is not UTF-8": b'{"text": "\xff\xfe"}'}.items():
                    status, reply = post_raw(body)
                    check(status == 400 and reply.get("error"), f"the viewer API answers 400 to {label}")
                status, reply = post_raw(b'{"text": "' + b"x" * 20000 + b'"}')
                check(status == 413 and reply.get("error"), "the viewer API answers 413 to a body that is too big")
                status, reply = post_raw(b"{}", length="abc")
                check(status == 400 and reply.get("error"), "the viewer API answers 400 to a bad length")
                check(get_with("/api/delta?since=abc") == 400, "the viewer API answers 400 to a bad since")
                check(urllib.request.urlopen("http://127.0.0.1:8765/graph.json").status == 200, "the viewer keeps running after bad requests")
                check(post("/api/forget", "") == 404, "the viewer API cannot delete")
                check(post("/api/remember", "Met Evil Eve from Bad Co", Origin="https://evil.example") == 403,
                      "the viewer API refuses a request from another website")
                check(post("/api/remember", "Met Sandbox Sid from Bad Co", Origin="null") == 403,
                      "the viewer API refuses a request with no real origin")
                check(post("/api/remember", "Met Rebound Rae from Bad Co", Host="evil.example:8765") == 403,
                      "the viewer API refuses another host name")
                check(get_with("/api/delta?since=0", Host="evil.example:8765") == 403 and get_with("/graph.json", Host="evil.example:8765") == 403,
                      "the viewer does not show your data to another host name")
                check(post("/api/remember", "Met Viewer Vic from Good Co", Origin="http://127.0.0.1:8765") == 200,
                      "the viewer API takes a request from its own page")
                check(post("/api/remember", "Met Local Lou from Good Co", Origin="http://localhost:8765", Host="localhost:8765") == 200,
                      "the viewer API takes a request from localhost")
                check(post("/api/remember", "Met Viewer Vic from Good Co", Origin="http://127.0.0.1:8765") == 200,
                      "the viewer's Remember box handles a repeat")

                # The viewer shows what the agent does, from another process, as it happens.
                def activity(since):
                    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:8765/api/activity?since={since}").read())

                start = activity(-1)
                check(start["events"] == [], "the first activity request gets the position, not old events")
                check(get_with("/api/activity?since=abc") == 400, "the viewer API answers 400 to a bad activity position")
                check(get_with("/api/activity?since=0", Host="evil.example:8765") == 403 and get_with("/api/activity?since=0", Origin="https://evil.example") == 403,
                      "another website cannot read what your agent does")
                check("/api/activity" in page, "the page reads the activity")
                live = Server([brain, "mcp"], user, env)
                saved_live = live.call("remember", name="Live Lena", company="Live Co", note="the private words", new_person=True)
                live.call("find", name="Live Lena")
                live.call("ask", cypher="MATCH (p:Person {name: 'Live Lena'}) RETURN p.name")
                live.call("ask", cypher="MATCH (p:Person {name: 'Live Lena'}) RETURN p")
                live.call("ask", cypher="MATCH (p:Person {name: 'Live Lena'}) RETURN p.nope(")
                live.stop()
                seen = activity(start["seq"])
                check([e["tool"] for e in seen["events"]] == ["remember", "find", "ask", "ask"], "each tool call is one event, and an error is none: " + str(seen)[:300])
                check(all(e["names"] == ["Live Lena"] for e in seen["events"]), "each event names who it touched")
                check(seen["events"][0]["saved"] is True and seen["events"][1]["saved"] is False, "an event says if it saved something")
                check("private words" not in json.dumps(seen), "the activity holds names, not the text of notes")
                check(activity(seen["seq"])["events"] == [], "an event shows once")
                fresh = json.loads(urllib.request.urlopen("http://127.0.0.1:8765/api/delta?since=99999999").read())
                check("Live Lena" in [n["name"] for n in fresh["nodes"]], "new data reaches the viewer even when a forgotten node's id comes back")
                check(any(e["type"] == "MET" for e in fresh["edges"]), "new links between old nodes reach the viewer")
                r = run("forget", "note", saved_live["note_id"], "--yes")
                check(r.returncode == 0 and activity(-1)["seq"] == 0 and not (home / "data/activity.json").exists(), "forget also clears names from the activity")

                # Back at an old company with two current jobs: only an agent can say which jobs ended.
                server = Server([brain, "mcp"], user, env)
                pid = server.call("remember", name="Busy Bea", company="Old Co", new_person=True)["person"]["id"]
                server.call("remember", name="", person_id=pid, company="Job One")
                server.call("remember", name="", person_id=pid, company="Job Two", ended_jobs=[])
                server.stop()
                check(post("/api/remember", "Met Busy Bea from Old Co, talked again", Origin="http://127.0.0.1:8765") == 409,
                      "the viewer's Remember box handles a person with two jobs")
            finally:
                view.terminate()
                view.wait(timeout=10)

            server = Server([brain, "mcp"], user, env)
            refused = [n for n in ["Evil Eve", "Sandbox Sid", "Rebound Rae"] if isinstance(server.call("find", name=n, label="Person"), list)]
            check(not refused, f"refused requests saved nothing {refused}")
            check(isinstance(server.call("find", name="Viewer Vic", label="Person"), list), "the page's request was saved")
            notes = "MATCH (n:Note) RETURN count(n)"
            check(run("forget", "all-notes").returncode != 0 and server.call("ask", cypher=notes)["rows"][0][0] > 0,
                  "forget all-notes: no answer keeps them")
            r = run("forget", "all-notes", "--yes")
            check(r.returncode == 0 and server.call("ask", cypher=notes)["rows"] == [[0]], "forget all-notes clears notes for the next demo")
            check(run("forget", "all-notes", "extra").returncode != 0, "forget all-notes takes no target")
            server.stop()
        finally:
            sys.path.insert(0, str(PROJECT / "src"))
            from falkordb import FalkorDB
            FalkorDB(host="localhost", port=6379).select_graph(TEST_GRAPH).delete()


if __name__ == "__main__":
    main()
