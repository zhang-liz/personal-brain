"""What must work on any system, with no database: Windows CI runs this, because its runners cannot run FalkorDB.

    .venv/bin/python tests/test_no_database.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from test_mcp_server import Server, check


def main():
    home = Path(tempfile.mkdtemp())
    env = {**os.environ, "PB_HOME": str(home), "FALKORDB_URL": "redis://localhost:6399"}
    brain = [sys.executable, "-m", "personal_brain.cli"]

    def run(*args):
        return subprocess.run([*brain, *args], cwd=home, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")

    check(run("--version").returncode == 0, "--version works")
    check("forget" in run("--help").stdout, "--help lists the commands")
    for command in (["notes"], ["forget", "note", "note-x", "--yes"]):
        r = run(*command)
        check(r.returncode != 0 and "FalkorDB" in r.stderr and "Traceback" not in r.stderr, f"{command[0]} with no database: a clear error")

    server = Server([*brain, "mcp"], home, env)
    tools = sorted(t["name"] for t in server.request("tools/list")["result"]["tools"])
    check(tools == ["ask", "find", "remember"], "the MCP server starts and lists its tools")
    check("FalkorDB" in server.call("find", name="Anyone")["error"], "a tool with no database says what to do")
    server.stop()

    code = ("from personal_brain import notes_backup as b; s = {'nodes': [{'label': 'Note', 'props': {'text': '図のデータベース'}}], 'about': [], 'links': []};"
            " b.save(s); assert b.pending() == s; b.clear(); assert b.pending() is None")
    r = subprocess.run([sys.executable, "-c", code], cwd=home, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    check(r.returncode == 0, "the notes backup keeps non-Latin text" + (": " + r.stderr[-200:] if r.returncode else ""))
    check(json.loads((Path(__file__).resolve().parent.parent / ".mcp.json").read_text())["mcpServers"], "the MCP config is valid JSON")


if __name__ == "__main__":
    main()
