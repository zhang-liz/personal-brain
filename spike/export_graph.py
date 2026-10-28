"""THROWAWAY SPIKE: export the whole FalkorDB graph to JSON for the WebGL viewer.

Reads every node and edge, in pages (FalkorDB Cloud caps results at 10k rows),
and writes data/viewer/graph.json (gitignored: it has real names).

    .venv/bin/python spike/export_graph.py           # local Docker FalkorDB
    .venv/bin/python spike/export_graph.py --cloud   # FALKORDB_URL from .env
"""

import json
import sys
import time
from pathlib import Path

from falkordb import FalkorDB

PROJECT = Path(__file__).resolve().parent.parent
OUT = PROJECT / "data" / "viewer" / "graph.json"
PAGE = 500  # node ids per page
CAP = 10000  # FalkorDB Cloud result-set cap


def connect():
    if "--cloud" in sys.argv:
        env = (PROJECT / ".env").read_text().splitlines()
        url = next(l.split("=", 1)[1].strip() for l in env if l.startswith("FALKORDB_URL="))
        return FalkorDB.from_url(url), "FalkorDB Cloud"
    return FalkorDB(host="localhost", port=6379), "local FalkorDB"


def main():
    db, where = connect()
    g = db.select_graph("network")
    t0 = time.time()
    max_id = g.query("MATCH (n) RETURN max(id(n))").result_set[0][0]

    def edge_page(lo, hi):
        rows = g.query("MATCH (a)-[r]->(b) WHERE id(a) >= $lo AND id(a) < $hi"
                       " RETURN id(a), id(b), type(r)", {"lo": lo, "hi": hi}).result_set
        if len(rows) < CAP:
            return rows
        if hi - lo == 1:
            sys.exit(f"node {lo} has {CAP}+ outgoing edges; export cannot page it")
        mid = (lo + hi) // 2
        return edge_page(lo, mid) + edge_page(mid, hi)

    nodes, edges = {}, []
    for lo in range(0, max_id + 1, PAGE):
        params = {"lo": lo, "hi": lo + PAGE}
        for nid, label, name, sub, when in g.query(
                "MATCH (n) WHERE id(n) >= $lo AND id(n) < $hi"
                " RETURN id(n), labels(n)[0], n.name, coalesce(n.headline, n.title, n.funding, n.summary, ''),"
                " coalesce(n.connected_on, n.date, '')",
                params).result_set:
            nodes[nid] = [label, name or "", sub or "", when or ""]
        edges += [tuple(row) for row in edge_page(lo, lo + PAGE)]

    total = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    if total != len(edges):
        sys.exit(f"exported {len(edges)} edges but graph has {total}")

    # Re-index nodes 0..n-1 for the viewer's typed arrays.
    ids = sorted(nodes)
    index = {nid: i for i, nid in enumerate(ids)}
    labels = sorted({n[0] for n in nodes.values()})
    types = sorted({e[2] for e in edges})
    degree = [0] * len(ids)
    for a, b, _ in edges:
        degree[index[a]] += 1
        degree[index[b]] += 1

    data = {
        "source": where,
        "labels": labels,
        "types": types,
        # node: [labelIndex, name, subtitle, degree, when (connected_on / event date)]
        "nodes": [[labels.index(nodes[n][0]), nodes[n][1], nodes[n][2][:120], degree[index[n]],
                   nodes[n][3]] for n in ids],
        # edge: [sourceIndex, targetIndex, typeIndex]
        "edges": [[index[a], index[b], types.index(t)] for a, b, t in edges],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    print(f"exported from {where}: {len(ids)} nodes, {len(edges)} edges "
          f"in {time.time() - t0:.1f}s -> {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
