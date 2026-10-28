"""MCP server: your Personal Brain graph as your agent's memory.

Four tools on one FalkorDB graph: remember, find, ask, forget. Claude Code
starts it from .mcp.json in the project folder. Other MCP clients:

    command: .venv/bin/python   args: spike/mcp_server.py   (stdio)

FALKORDB_URL (environment or .env) = FalkorDB Cloud; empty = local FalkorDB on
localhost:6379. PB_GRAPH picks the graph (default "network"). The tools only
ever touch that one graph.
"""

import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory  # noqa: E402

GRAPH, SOURCE = memory.connect()
memory.setup(GRAPH)


def answer(fn, *args):
    """Errors go back to the agent as text it can act on (a Cypher typo, an unknown id), not a bare failure."""
    try:
        return fn(GRAPH, *args)
    except Exception as e:
        return {"error": str(e)}


server = MCPServer("personal-brain", instructions=(
    "The user's professional network and meeting notes, as a graph in FalkorDB. "
    "Use find to look up a person, company or event by name. Use ask for questions that follow links "
    "(who knows whom, who works where, who I met at which event). Use remember when the user tells you "
    "about someone they met. Use forget only when the user asks to forget something."))


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
def remember(name: str, company: str = "", event: str = "", note: str = "",
             person_id: str = "", new_person: bool = False) -> dict:
    """Save that the user met someone. Pull the fields out of what the user said.

    name: the person's full name. company: where they work now. event: where you met.
    note: what you talked about, in the user's words.
    It matches the name, typos included, to people already in the graph. If it is not sure which person it is, it saves
    nothing and returns candidates: ask the user, then call again with person_id (from the candidates)
    or new_person=true. A new company ends their old job and keeps it in their history."""
    return answer(memory.remember, name, company, event, note, person_id, new_person)


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def find(name: str, label: str = "") -> list | dict:
    """Find people, companies or events by name. Typos and missing accents are fine.

    label: "Person", "Company" or "Event" to search only one kind. Returns ids for ask, remember and forget,
    plus where each person works now, so you can tell two people with the same name apart."""
    # An empty list reaches the agent as an empty reply, so say it in words.
    return answer(memory.find, name, label or None) or {
        "found": 0, "message": "No match. Try part of the name, or use remember to add them as a new person."}


# The schema goes in the description: the agent writes better Cypher when it sees it.
@server.tool(annotations=ToolAnnotations(read_only_hint=True), description=(
    "Run a read-only OpenCypher query on the user's network. Writes are refused.\n"
    "Use $name parameters for values from the user. Returns at most 50 rows; use LIMIT and ORDER BY. "
    "Relationships come back with a rel_id, which forget(what=\"fact\") takes.\n\nSchema:\n" + memory.SCHEMA))
def ask(cypher: str, params: dict | None = None) -> dict:
    return answer(memory.ask, cypher, params)


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
def forget(what: str, id: str) -> dict:
    """Forget something, only when the user asks you to.

    what="note": one note (id from remember) and every fact it added; a job it ended comes back.
    what="fact": one relationship (rel_id from ask).
    what="person": a person (id from find), every note about them, and they stay out of future rebuilds."""
    return answer(memory.forget, what, id)


if __name__ == "__main__":
    server.run()
