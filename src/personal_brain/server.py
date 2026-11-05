"""Local server for the Personal Brain viewer.

Serves data/viewer/ (the page + graph export) on http://127.0.0.1:8765 and
adds a tiny API that talks to FalkorDB (Cloud or local) live:

    GET  /api/delta?since=N   nodes with id >= N (added after the export) + their edges
    POST /api/remember        {"text": "Met Maya from Supabase at AI Engineer Summit, runs events"}

Only data/viewer/ is served, never your data folder (.env lives there). Only the
viewer's own page can use it: a request from another website is refused. Nothing
here deletes. To clear notes, run `personal-brain forget all-notes`.
"""

import errno
import json
import shutil
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from personal_brain.paths import HOME, NO_GRAPH, PACKAGE, graph_missing
from personal_brain import activity, memory
from personal_brain.load_graph import TOPIC_RE
from personal_brain.notes_backup import every_row

ROOT = HOME / "data" / "viewer"

# FALKORDB_URL in .env = FalkorDB Cloud; empty = local Docker FalkorDB on localhost:6379.
GRAPH, SOURCE = memory.connect()
memory.setup(GRAPH)

def q(cypher, params=None):
    return GRAPH.ro_query(cypher, params or {})


def remember(text):
    """The viewer's Remember box: the same write path as the MCP tool, from one typed sentence."""
    parts = memory.parse_met(text)
    if not parts:
        return 400, {"error": "Try: Met <name> from <company> at <event>, <what you talked about>"}
    r = memory.remember(GRAPH, parts["name"], parts["company"], parts["event"], parts["note"])
    if r["status"] == "needs_choice":
        who = ", ".join(f"{c['name']} ({', '.join(c.get('works_at', [])) or 'no company'})" for c in r["candidates"])
        return 409, {"error": f"Not sure which person: {who}. Ask your agent, it can pick one."}
    if r["status"] == "needs_jobs":
        return 409, {"error": f"{r['person']['name']} has {len(r['current_jobs'])} current jobs. Ask your agent, it can say which ended."}
    if r["status"] in ("needs_company", "needs_event"):
        near = r["current_jobs"][0] if r["status"] == "needs_company" else r["candidates"][0]["name"]
        return 409, {"error": f"That is one letter off {near}. Use the same spelling, or ask your agent if it is another one."}
    topics = [t for t, rx in TOPIC_RE.items() if rx.search(parts["note"])]
    pnode = q("MATCH (p:Person {id: $pid}) RETURN id(p)", {"pid": r["person"]["id"]}).result_set[0][0]
    return 200, {"parsed": parts, "topics": topics, "person": pnode, "ms": r["ms"], "source": SOURCE,
                 "changes": r["changes"], **neighborhood(pnode)}


def neighborhood(node_id):
    """The node, its neighbors, and all edges touching it (viewer adds what it lacks)."""
    rows = q("""MATCH (p) WHERE id(p) = $id
                OPTIONAL MATCH (p)-[r]-(x)
                RETURN id(p), labels(p)[0], p.name, coalesce(p.headline, p.title, p.funding, p.summary, ''),
                       coalesce(p.connected_on, p.date, ''),
                       id(x), labels(x)[0], x.name, coalesce(x.headline, x.title, x.funding, x.summary, ''),
                       coalesce(x.connected_on, x.date, ''),
                       id(startNode(r)), id(endNode(r)), type(r)""", {"id": node_id}).result_set
    nodes, edges = {}, []
    for row in rows:
        nodes[row[0]] = node_json(*row[0:5])
        if row[5] is not None:
            nodes[row[5]] = node_json(*row[5:10])
            edges.append({"s": row[10], "t": row[11], "type": row[12]})
    return {"nodes": list(nodes.values()), "edges": edges}


def node_json(nid, label, name, sub, when):
    return {"id": nid, "label": label, "name": name or "", "sub": sub or "", "when": when or ""}


def delta(since):
    nodes = [node_json(*row) for row in every_row(
        GRAPH, "MATCH (n) WITH n, id(n) AS at WHERE (at >= $s OR n.source = 'agent')",
        "id(n), labels(n)[0], n.name, coalesce(n.headline, n.title, n.funding, n.summary, ''), coalesce(n.connected_on, n.date, '')",
        {"s": since})]
    edges = [{"s": a, "t": b, "type": t} for a, b, t in every_row(
        GRAPH, "MATCH (a)-[r]->(b) WITH a, b, r, id(r) AS at WHERE (id(a) >= $s OR id(b) >= $s OR r.note IS NOT NULL)",
        "id(a), id(b), type(r)", {"s": since})]
    return {"nodes": nodes, "edges": edges}


def map_version():
    """Changes when an export writes the viewer's files: `forget` and `load` do that. An open page then reads them again."""
    return "/".join(str(f.stat().st_mtime_ns) for f in (ROOT / "graph.json", ROOT / "stories.json") if f.exists())


# A website you visit can send requests to this server. The browser says where they come from.
THIS_SERVER = ("127.0.0.1:8765", "localhost:8765")


MAX_BODY = 10_000
MAX_TEXT = 500


def problem(exc):
    return NO_GRAPH if graph_missing(exc) else str(exc)


class BadRequest(Exception):
    """A request the server can answer with an error the viewer can show."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Handler(SimpleHTTPRequestHandler):
    def json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise BadRequest(400, "Content-Length must be a number.") from None
        if length > MAX_BODY:
            raise BadRequest(413, f"The request is longer than {MAX_BODY} bytes.")
        try:
            body = json.loads(self.rfile.read(max(length, 0)) or b"{}")
        except ValueError:
            raise BadRequest(400, "The request is not valid JSON.") from None
        if not isinstance(body, dict):
            raise BadRequest(400, "The request must be a JSON object.")
        return body

    def refused(self):
        """True, after replying, unless the request names this server and comes from its own page or no website."""
        origin = self.headers.get("Origin")
        if self.headers.get("Host") in THIS_SERVER and (origin is None or urlparse(origin).netloc in THIS_SERVER):
            return False
        self.send_json(403, {"error": "This server only takes requests from its own page."})
        return True

    def send_json(self, status, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self):
        if not self.path.startswith("/api/"):
            # Re-check files on every load, so a reload never shows an old copy of the page.
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):
        if self.refused():
            return
        url = urlparse(self.path)
        if url.path == "/api/delta":
            try:
                since = int(parse_qs(url.query).get("since", ["0"])[0])
            except ValueError:
                return self.send_json(400, {"error": "since must be a number."})
            try:
                return self.send_json(200, delta(since))
            except Exception as exc:
                return self.send_json(500, {"error": problem(exc)})
        if url.path == "/api/activity":
            try:
                seq = int(parse_qs(url.query).get("since", ["-1"])[0])
            except ValueError:
                return self.send_json(400, {"error": "since must be a number."})
            return self.send_json(200, {**activity.since(seq), "map": map_version()})
        return super().do_GET()

    def do_POST(self):
        if self.refused():
            return
        url = urlparse(self.path)
        try:
            body = self.json_body()
        except BadRequest as e:
            return self.send_json(e.status, {"error": str(e)})
        t0 = time.time()
        try:
            if url.path == "/api/remember":
                text = str(body.get("text", ""))
                status, out = (remember(text) if len(text) <= MAX_TEXT else
                               (400, {"error": f"That is longer than {MAX_TEXT} characters. Make it shorter, or ask your agent: it can save a long note."}))
            else:
                status, out = 404, {"error": "not found"}
        except Exception as exc:  # surface FalkorDB errors to the page during the demo
            status, out = 500, {"error": problem(exc)}
        out["total_ms"] = round((time.time() - t0) * 1000)
        self.send_json(status, out)

    def log_message(self, fmt, *args):
        if "/api/" in str(args[0] if args else ""):
            super().log_message(fmt, *args)


def main():
    if not (ROOT / "graph.json").exists():
        raise SystemExit("The viewer has no data yet. Run personal-brain export first.")
    shutil.copytree(PACKAGE / "viewer", ROOT, dirs_exist_ok=True)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 8765), partial(Handler, directory=str(ROOT)))
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        raise SystemExit("Port 8765 is in use. The viewer may be running already: open http://127.0.0.1:8765.") from None
    print(f"serving http://127.0.0.1:8765 (live API on {SOURCE})")
    server.serve_forever()


if __name__ == "__main__":
    main()
