"""Export the whole FalkorDB graph to JSON for the WebGL viewer.

Reads every node and edge, in pages (FalkorDB Cloud caps results at 10k rows),
and writes data/viewer/graph.json (gitignored: it has real names).

    personal-brain export           # local Docker FalkorDB
    personal-brain export --cloud   # FALKORDB_URL from the environment or .env
"""

import json
import os
import sys
import time
from pathlib import Path

from falkordb import FalkorDB

from personal_brain.paths import HOME, NO_GRAPH, cloud_url, open_url
OUT = Path(os.environ.get("PB_VIEWER_DIR", HOME / "data" / "viewer")) / "graph.json"
PAGE = 500  # node ids per page
CAP = 10000  # FalkorDB Cloud result-set cap


def connect():
    if "--cloud" in sys.argv:
        return open_url(cloud_url()), "FalkorDB Cloud"
    return FalkorDB(host="localhost", port=6379), "local FalkorDB"


def main():
    db, where = connect()
    g = db.select_graph(os.environ.get("PB_GRAPH", "network"))
    t0 = time.time()
    max_id = g.ro_query("MATCH (n) RETURN max(id(n))").result_set[0][0]    # a read, so that it does not make an empty graph
    if max_id is None:
        sys.exit(NO_GRAPH)

    def edge_page(lo, hi):
        rows = g.query("MATCH (a)-[r]->(b) WHERE id(a) >= $lo AND id(a) < $hi"
                       " RETURN id(a), id(b), type(r) ORDER BY id(r) LIMIT $cap",
                       {"lo": lo, "hi": hi, "cap": CAP}).result_set
        if len(rows) < CAP:
            return rows
        if hi - lo == 1:
            # One node with more edges than a result can hold (you, with 10k+ connections).
            skip = 0
            while len(rows) % CAP == 0 and len(rows) == skip + CAP:
                skip += CAP
                rows += g.query("MATCH (a)-[r]->(b) WHERE id(a) = $lo RETURN id(a), id(b), type(r)"
                                " ORDER BY id(r) SKIP $skip LIMIT $cap", {"lo": lo, "skip": skip, "cap": CAP}).result_set
            return rows
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

    # In the viewer's typed arrays, a node's place is its id in FalkorDB. Forget leaves gaps, and they hold their place.
    ids = range(max_id + 1)
    for nid in ids:
        nodes.setdefault(nid, ["Gone", "", "", ""])
    index = {nid: nid for nid in ids}
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
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"exported from {where}: {len(ids)} nodes, {len(edges)} edges "
          f"in {time.time() - t0:.1f}s -> {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
