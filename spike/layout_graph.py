"""THROWAWAY SPIKE: precompute 2D and 3D layouts for the WebGL viewer.

Reads data/viewer/graph.json (from export_graph.py), runs igraph's DrL layout
in 2D and 3D, and writes the positions back into the same file.

    .venv/bin/python spike/layout_graph.py
"""

import json
import sys
import time
from pathlib import Path

import igraph as ig

PROJECT = Path(__file__).resolve().parent.parent
GRAPH_JSON = PROJECT / "data" / "viewer" / "graph.json"
RADIUS = 1000.0
# "Me" links to everyone, which would crush the map into one ball: leave those
# links out of the layout and put Me at the center afterwards. Skill links are
# many and weak, so they pull less than jobs, schools and "knows" links.
SKIP = {"CONNECTED", "ATTENDED"}
WEIGHTS = {"HAS_SKILL": 0.2, "LIVES_IN": 0.15, "HELD_IN": 0.15}


def normalize(coords, dims):
    n = len(coords)
    center = [sum(c[d] for c in coords) / n for d in range(dims)]
    shifted = [[c[d] - center[d] for d in range(dims)] for c in coords]
    radii = sorted(sum(v * v for v in c) ** 0.5 for c in shifted)
    scale = RADIUS / (radii[int(n * 0.98)] or 1.0)
    return [round(v * scale, 1) for c in shifted for v in c]


def main():
    data = json.loads(GRAPH_JSON.read_text())
    types = data["types"]
    n = len(data["nodes"])
    kept = [(a, b, t) for a, b, t in data["edges"] if a != b and types[t] not in SKIP]
    g = ig.Graph(n=n, edges=[(a, b) for a, b, _ in kept])
    weights = [WEIGHTS.get(types[t], 1.0) for _, _, t in kept]
    me = next(i for i, node in enumerate(data["nodes"]) if data["labels"][node[0]] == "Me")
    dims_list = [int(a.split("=")[1]) for a in sys.argv if a.startswith("--dims=")] or [2, 3]
    for dims in dims_list:
        t0 = time.time()
        coords = g.layout_drl(weights=weights, dim=dims).coords
        coords[me] = [0.0] * dims  # normalize() re-centers on the mean, so set it after
        flat = normalize(coords, dims)
        flat[me * dims:(me + 1) * dims] = [0.0] * dims
        data[f"pos{dims}"] = flat
        radii = sorted(sum(v * v for v in flat[i * dims:(i + 1) * dims]) ** 0.5 for i in range(n))
        print(f"{dims}D layout: {time.time() - t0:.1f}s, radius p10/p50/p90 = "
              f"{radii[n // 10]:.0f}/{radii[n // 2]:.0f}/{radii[n * 9 // 10]:.0f}")
    GRAPH_JSON.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {GRAPH_JSON} ({GRAPH_JSON.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
