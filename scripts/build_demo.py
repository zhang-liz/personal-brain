"""Build the public demo site into data/demo_site/. Local FalkorDB only, never Cloud.

    docker run -d --name pb-demo -p 6379:6379 falkordb/falkordb:6.0.4
    .venv/bin/python scripts/build_demo.py               # full size
    .venv/bin/python scripts/build_demo.py --people 1000 # quick test
"""

import json
import math
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DATA, SITE = PROJECT / "data" / "demo", PROJECT / "data" / "demo_site"
BANNER = ('<style>#remember,#rememberHint{display:none}'
          '#pbDemo{position:fixed;left:16px;bottom:8px;z-index:50;font:12px system-ui;color:#fff;opacity:.65;pointer-events:none}</style>'
          '<div id="pbDemo">Demo: all connections are made up. Companies and events are only example names.</div>')


def run(*cmd, **env):
    subprocess.run([sys.executable, *cmd], cwd=PROJECT, check=True, env={**os.environ, **env})


def shape_galaxy():
    """Spread the layout into a spiral disc. Clusters stay together; only the look changes.

    With 18k people at a few hundred companies, the raw layout is one tight ball plus a grid of blobs."""
    raw, graph = DATA / "graph_raw.json", SITE / "graph.json"
    data = json.loads(raw.read_text())
    pos, rnd = data["pos2"], random.Random(7)
    n = len(pos) // 2
    me = next(i for i, node in enumerate(data["nodes"]) if data["labels"][node[0]] == "Me")
    order = sorted((i for i in range(n) if i != me), key=lambda i: math.hypot(pos[2 * i], pos[2 * i + 1]))
    for rank, i in enumerate(order):
        t = (rank + 1) / n
        radius = 1000 * t ** 0.72
        angle = math.atan2(pos[2 * i + 1], pos[2 * i]) + 2.6 * t ** 0.6
        radius *= 1 + rnd.gauss(0, 0.05)
        angle += rnd.gauss(0, 0.05)
        pos[2 * i], pos[2 * i + 1] = round(radius * math.cos(angle), 1), round(radius * math.sin(angle), 1)
    pos[2 * me], pos[2 * me + 1] = 0.0, 0.0
    graph.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def main():
    SITE.mkdir(parents=True, exist_ok=True)
    env = {"PB_SAMPLE_DIR": str(DATA), "PB_VIEWER_DIR": str(SITE), "PB_GRAPH": "demo"}
    run("scripts/make_demo_data.py", *sys.argv[1:])
    run("-m", "personal_brain.load_graph", "--sample", "--yes", "--graph", "demo", **env)
    run("-m", "personal_brain.export_graph", **env)
    run("-m", "personal_brain.layout_graph", **env)
    shutil.copyfile(SITE / "graph.json", DATA / "graph_raw.json")
    shape_galaxy()
    run("-m", "personal_brain.export_stories", **env)
    shutil.copytree(PROJECT / "src" / "personal_brain" / "viewer", SITE, dirs_exist_ok=True)
    index = SITE / "index.html"
    page = index.read_text()
    # The title stays "Personal Brain", not "<first name>'s Personal Brain".
    named_title = "if (firstName) document.title"
    if named_title not in page:
        sys.exit("viewer title code changed; update build_demo.py")
    index.write_text(page.replace(named_title, "if (false) document.title", 1).replace("</body>", BANNER + "</body>", 1))
    print(f"site ready: {SITE}")


if __name__ == "__main__":
    main()
