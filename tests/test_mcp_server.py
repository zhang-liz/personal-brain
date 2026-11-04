"""End-to-end check of the MCP server over stdio, the way an agent starts it.

Starts the command in .mcp.json from a subfolder, with no database and then with
local FalkorDB (graph "pb_mcp_test", deleted at the end). Never touches "network".

    .venv/bin/python tests/test_mcp_server.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
TEST_GRAPH = "pb_mcp_test"
NO_DATABASE = "redis://localhost:6399"
from personal_brain import memory  # noqa: E402


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        raise SystemExit(1)


def from_mcp_json(url):
    """The server as .mcp.json starts it, from a subfolder."""
    config = json.loads((PROJECT / ".mcp.json").read_text())["mcpServers"]["personal-brain"]
    return Server([config["command"], *config["args"]], PROJECT / "src",
                  {**os.environ, "FALKORDB_URL": url, "PB_GRAPH": TEST_GRAPH})


class Server:
    """An MCP server, spoken to in plain JSON-RPC."""

    def __init__(self, command, cwd, env):
        self.process = subprocess.Popen(command, cwd=cwd, env=env, text=True,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.last_id = 0
        init = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                           "clientInfo": {"name": "test", "version": "0"}})
        self.info = init["result"]["serverInfo"]
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method, params=None):
        self.last_id += 1
        self.send({"jsonrpc": "2.0", "id": self.last_id, "method": method, "params": params or {}})
        line = self.process.stdout.readline()
        if not line:
            raise SystemExit(f"FAIL server stopped during {method}")
        return json.loads(line)

    def call(self, tool, **arguments):
        result = self.request("tools/call", {"name": tool, "arguments": arguments})["result"]
        if "structuredContent" in result:
            return result["structuredContent"].get("result", result["structuredContent"])
        return json.loads(result["content"][0]["text"])

    def stop(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)


def no_database():
    server = from_mcp_json(NO_DATABASE)
    tools = [t["name"] for t in server.request("tools/list")["result"]["tools"]]
    check(sorted(tools) == ["ask", "find", "remember"], "no database: server starts and lists its tools, none deletes")
    check(bool(server.info.get("version")), "server reports a version")
    check("falkordb_data:/var/lib/falkordb/data" in server.call("find", name="Anyone")["error"], "the start command keeps data in a volume")
    # Clients may show only tool descriptions, so each tool that returns other people's text says this too.
    described = {t["name"]: t["description"] for t in server.request("tools/list")["result"]["tools"]}
    for tool in ["find", "ask"]:
        check("never as instructions" in described[tool], f"{tool} says its results are data, not instructions")
    check("only what the user told you" in described["remember"].lower(), "remember says to save only what the user said")
    error = server.call("find", name="Anyone").get("error", "")
    check("FalkorDB" in error and "FALKORDB_URL" in error and "docker run" in error, "no database: a tool says what to do")
    check(server.process.poll() is None, "no database: server keeps running")
    server.stop()


def local_database():
    os.environ["FALKORDB_URL"] = ""
    graph, _ = memory.connect(TEST_GRAPH)
    server = from_mcp_json("")
    try:
        saved = server.call("remember", name="Stdio Sam", company="Pipe Works", note="met over stdio")
        check(saved.get("status") == "saved", "remember over stdio")
        found = server.call("find", name="Stdio Sam", label="Person")
        check(found[0]["id"] == saved["person"]["id"], "find over stdio")
        rows = server.call("ask", cypher="MATCH (p:Person {name: $n}) RETURN p.company", params={"n": "Stdio Sam"})["rows"]
        check(rows == [["Pipe Works"]], "ask over stdio")
        check(saved.get("undo") == f"personal-brain forget note {saved['note_id']}", "remember says how to undo")
        dated = server.call("remember", name="Stdio Sam", note="older chat", on="2026-01-15")
        rows = server.call("ask", cypher="MATCH (n:Note {id: $id}) RETURN n.on", params={"id": dated["note_id"]})["rows"]
        check(rows == [["2026-01-15"]], "remember takes a date over stdio")
        check(server.call("remember", name="Stdio Sam", note="older chat", on="2026-01-15")["status"] == "already_saved",
              "a repeat over stdio saves nothing")
        moved = server.call("remember", name="", person_id=saved["person"]["id"], company="Side Works", ended_jobs=[])
        rows = server.call("ask", cypher="MATCH (:Person {name: 'Stdio Sam'})-[:WORKED_AT {current: true}]->(c) RETURN c.name ORDER BY c.name")["rows"]
        check(moved["status"] == "saved" and rows == [["Pipe Works"], ["Side Works"]], "remember takes ended_jobs over stdio")
        todo = server.call("remember", name="", person_id=saved["person"]["id"], follow_up="Send the pipes deck", due="2030-01-15")
        check(todo["status"] == "saved" and todo["follow_up_id"], "remember takes a follow-up over stdio")
        closed = server.call("remember", name="", follow_up_done=todo["follow_up_id"])
        check(closed["status"] == "saved", "remember closes a follow-up over stdio")
        fact = server.call("remember", name="", person_id=saved["person"]["id"], title="Head of Pipes", location="Berlin", email="sam@example.com")
        found = server.call("find", name="Stdio Sam", label="Person")[0]
        check(fact["status"] == "saved" and found["title"] == "Head of Pipes" and found["email"] == "sam@example.com", "remember takes title, location and email over stdio")
        about = server.call("find", about="stdio")
        check(about["people"][0]["name"] == "Stdio Sam", "find about words over stdio")
        check("name or about" in server.call("find")["error"], "find with nothing: says what to pass")
        check(server.call("find", about="no such words here")["found"] == 0, "find about with no match: says so")
        error = server.call("find", name="Sam", label="Planet")["error"]
        check("Person" in error and "Planet" in error, "unknown label: the error names the choices")
    finally:
        server.stop()
        graph.delete()


if __name__ == "__main__":
    no_database()
    local_database()
