"""The viewer in a real browser. It needs Chrome and Playwright, so it skips when they are not here:

    uv pip install playwright
"""

import os
import subprocess
import sys
import time
import urllib.request

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from personal_brain import activity, memory

GRAPH = "pb_viewer_test"
BRAIN = [sys.executable, "-m", "personal_brain.cli"]
CHROME = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--no-first-run"]


class Map:
    def __init__(self, home, page):
        self.home, self.page = home, page
        self.env = {**os.environ, "PB_HOME": str(home), "PB_GRAPH": GRAPH, "FALKORDB_URL": ""}
        self.g = memory.connect(GRAPH)[0]

    def brain(self, *args):
        r = subprocess.run([*BRAIN, *args], env=self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=self.home)
        assert r.returncode == 0, r.stderr[-500:]
        return r

    def open(self):
        self.page.goto("http://127.0.0.1:8765/")
        self.page.wait_for_selector("#loading", state="detached", timeout=60000)
        self.page.wait_for_function("document.getElementById('rememberHint').textContent.includes('Writes to')", timeout=60000)

    def shows(self, name):
        self.page.fill("#q", name)
        self.page.wait_for_timeout(200)
        return name in self.page.inner_text("#results")

    def waits_until(self, name, shown=True, seconds=30):
        end = time.time() + seconds
        while time.time() < end:
            try:
                if self.shows(name) is shown:
                    return True
            except sync_api.Error:    # the page is reloading
                pass
            self.page.wait_for_timeout(500)
        return False

    def say(self, text):
        """Type in the viewer's text box."""
        self.page.fill("#rememberText", text)
        self.page.press("#rememberText", "Enter")
        self.page.wait_for_function("/Saved|Updating/.test(document.getElementById('rememberHint').textContent)", timeout=20000)

    def ids_of(self, name):
        """The ids a person's save took: the person, their notes and their company."""
        self.g.schema.clear()
        return {i for row in self.g.ro_query("MATCH (p:Person {name: $name})<-[:ABOUT]-(n:Note) OPTIONAL MATCH (p)-[:WORKED_AT]->(c)"
                                             " RETURN id(p), id(n), id(c)", {"name": name}).result_set for i in row}


@pytest.fixture
def map_(tmp_path):
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", args=CHROME)
        except sync_api.Error:
            pytest.skip("no Chrome here")
        page = browser.new_page(viewport={"width": 320, "height": 240})    # drawing is in software: a small page keeps it quick
        problems = []
        page.on("pageerror", lambda e: problems.append(str(e)))
        m = Map(tmp_path, page)
        m.brain("load", "--sample", "--yes")
        m.brain("export")
        server = subprocess.Popen([*BRAIN, "view"], env=m.env, cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen("http://127.0.0.1:8765/graph.json", timeout=1)
                    break
                except OSError:
                    time.sleep(0.2)
            m.open()
            yield m
        finally:
            server.terminate()
            server.wait()
            browser.close()
            m.g.delete()
        assert not problems


def test_someone_you_forget_leaves_an_open_map(map_):
    map_.say("Met Rex Secret from Tern Works")
    assert map_.waits_until("Rex Secret")
    map_.brain("forget", "person", "Rex Secret", "--yes")
    assert map_.waits_until("Rex Secret", shown=False)


def test_a_reused_id_shows_the_new_person(map_):
    map_.say("Met Rex Secret from Tern Works")
    assert map_.waits_until("Rex Secret")
    rex = map_.ids_of("Rex Secret")
    # What forget does, with no new files: the next save gets the same ids.
    map_.g.query("MATCH (n) WHERE n.source = 'agent' DETACH DELETE n")
    map_.say("Met Vera Newhire from Tern Works")
    assert rex & map_.ids_of("Vera Newhire")
    assert map_.waits_until("Vera Newhire")
    assert map_.waits_until("Rex Secret", shown=False)


def test_a_file_older_than_the_graph_shows_the_new_person(map_):
    file = map_.home / "data/viewer/graph.json"
    map_.say("Met Rex Secret from Tern Works")
    map_.brain("export")
    old = file.read_text(encoding="utf-8")
    rex = map_.ids_of("Rex Secret")
    map_.brain("forget", "person", "Rex Secret", "--yes")
    map_.open()
    map_.say("Met Vera Newhire from Tern Works")
    assert rex & map_.ids_of("Vera Newhire")
    file.write_text(old, encoding="utf-8")
    map_.open()
    assert map_.shows("Vera Newhire") and not map_.shows("Rex Secret")


def test_a_long_session_makes_room_with_no_help(map_):
    for i in range(300):
        memory.remember(map_.g, f"Nell Crowd{i:03d}", new_person=True, note=f"crowd {i}")
    assert map_.g.ro_query("MATCH (n) RETURN max(id(n))").result_set[0][0] > map_.page.evaluate("fetch('graph.json').then(r => r.json()).then(g => g.nodes.length)") + 500
    map_.say("Met Vera Newhire from Tern Works")
    assert map_.waits_until("Vera Newhire", seconds=60)
    assert map_.waits_until("Nell Crowd299")
    assert "Reload" not in map_.page.inner_text("#rememberHint")


def test_the_map_waits_for_text_you_are_typing(map_, monkeypatch):
    for i in range(300):
        memory.remember(map_.g, f"Nell Crowd{i:03d}", new_person=True, note=f"crowd {i}")
    map_.page.fill("#rememberText", "Met Half Written")
    monkeypatch.setattr(activity, "FILE", map_.home / "data" / "activity.json")
    activity.log("remember", {"status": "saved", "person": {"name": "Nell Crowd299"}})
    map_.page.wait_for_timeout(4000)
    assert map_.page.input_value("#rememberText") == "Met Half Written"
    map_.page.fill("#rememberText", "")
    assert map_.waits_until("Nell Crowd299", seconds=30)
