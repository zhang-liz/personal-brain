"""Local server for the Personal Brain viewer.

Serves data/viewer/ (the page + graph export) on http://127.0.0.1:8765 and
adds a tiny API that talks to FalkorDB (Cloud or local) live:

    GET  /api/delta?since=N   nodes with id >= N (added after the export) + their edges
    POST /api/remember        {"text": "Met Maya from Supabase at AI Engineer Summit, runs events"}
    POST /api/forget          undo every remembered note (see memory.py)

Only data/viewer/ is served, never the project root (.env lives there).
"""

import json
import re
import sys
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT = Path(__file__).resolve().parent.parent
ROOT = PROJECT / "data" / "viewer"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory  # noqa: E402
from load_graph import TOPIC_RE  # noqa: E402

# FALKORDB_URL in .env = FalkorDB Cloud; empty = local Docker FalkorDB on localhost:6379.
GRAPH, SOURCE = memory.connect()
memory.setup(GRAPH)

# "met NAME [from COMPANY] [at EVENT][, note]"
MET_RE = re.compile(
    r"^\s*(?:i\s+)?(?:just\s+)?met\s+(?P<name>.+?)"
    r"(?:\s+(?:from|of)\s+(?P<company>.+?))?"
    r"(?:\s+(?:at|during)\s+(?P<event>.+?))?"
    r"\s*(?:(?:[.;:!]|,|\s[-–—]\s)\s*(?P<note>.*))?$", re.I)


def parse(text):
    m = MET_RE.match(text.strip())
    if not m:
        return None
    parts = {k: (v or "").strip(" .") for k, v in m.groupdict().items()}
    # "met Maya at Supabase" with no event keyword left: treat a lone "at X" as the event.
    return parts if parts["name"] else None


def q(cypher, params=None):
    return GRAPH.query(cypher, params or {})


def remember(text):
    """The viewer's Remember box: the same write path as the MCP tool, from one typed sentence."""
    parts = parse(text)
    if not parts:
        return 400, {"error": "Try: Met <name> from <company> at <event>, <what you talked about>"}
    r = memory.remember(GRAPH, parts["name"], parts["company"], parts["event"], parts["note"])
    if r["status"] == "needs_choice":
        who = ", ".join(f"{c['name']} ({', '.join(c.get('works_at', [])) or 'no company'})" for c in r["candidates"])
        return 409, {"error": f"Not sure which person: {who}. Ask your agent, it can pick one."}
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
    nodes = [node_json(*row) for row in q(
        "MATCH (n) WHERE id(n) >= $s RETURN id(n), labels(n)[0], n.name,"
        " coalesce(n.headline, n.title, n.funding, n.summary, ''), coalesce(n.connected_on, n.date, '')",
        {"s": since}).result_set]
    edges = [{"s": a, "t": b, "type": t} for a, b, t in q(
        "MATCH (a)-[r]->(b) WHERE id(a) >= $s OR id(b) >= $s RETURN id(a), id(b), type(r)",
        {"s": since}).result_set]
    return {"nodes": nodes, "edges": edges}


def forget():
    """The viewer's reset button: undo every note, and everything the notes added."""
    return {"deleted": memory.forget_all_notes(GRAPH)}


class Handler(SimpleHTTPRequestHandler):
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
        url = urlparse(self.path)
        if url.path == "/api/delta":
            since = int(parse_qs(url.query).get("since", ["0"])[0])
            return self.send_json(200, delta(since))
        return super().do_GET()

    def do_POST(self):
        url = urlparse(self.path)
        length = min(int(self.headers.get("Content-Length") or 0), 10_000)
        body = json.loads(self.rfile.read(length) or b"{}")
        t0 = time.time()
        try:
            if url.path == "/api/remember":
                status, out = remember(str(body.get("text", ""))[:500])
            elif url.path == "/api/forget":
                status, out = 200, forget()
            else:
                status, out = 404, {"error": "not found"}
        except Exception as exc:  # surface FalkorDB errors to the page during the demo
            status, out = 500, {"error": str(exc)}
        out["total_ms"] = round((time.time() - t0) * 1000)
        self.send_json(status, out)

    def log_message(self, fmt, *args):
        if "/api/" in str(args[0] if args else ""):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8765), partial(Handler, directory=str(ROOT)))
    print(f"serving http://127.0.0.1:8765 (live API on {SOURCE})")
    server.serve_forever()
