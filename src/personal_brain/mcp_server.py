"""MCP server: your Personal Brain graph as your agent's memory.

Three tools on one FalkorDB graph: remember, find, ask. None of them deletes:
only the user can, with `personal-brain forget`. Claude Code
and other MCP clients start it over stdio: `uvx personal-brain mcp`. In a clone,
Claude Code uses .mcp.json.

FALKORDB_URL (environment or .env) = FalkorDB Cloud; empty = local FalkorDB on
localhost:6379. PB_GRAPH picks the graph (default "network"). The tools only
ever touch that one graph. The server starts without a database: each tool
connects when it runs, and says what to do if it cannot.
"""

import redis
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from personal_brain import __version__, memory

GRAPH = None
NO_DATABASE = ("Cannot reach FalkorDB ({}). Tell the user to start it with "
               "`docker run -p 6379:6379 -p 3000:3000 -it --rm falkordb/falkordb:latest`, "
               "or to put their FalkorDB Cloud URL in FALKORDB_URL in the .env file. Then try again.")


def answer(fn, *args):
    """Errors go back to the agent as text it can act on (a Cypher typo, an unknown id), not a bare failure."""
    global GRAPH
    try:
        if GRAPH is None:
            graph, _ = memory.connect()
            memory.setup(graph)
            GRAPH = graph
        return fn(GRAPH, *args)
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
        GRAPH = None
        return {"error": NO_DATABASE.format(e)}
    except Exception as e:
        return {"error": str(e)}


server = MCPServer("personal-brain", version=__version__, instructions=(
    "The user's professional network and meeting notes, as a graph in FalkorDB. "
    "Use find to look up a person, company or event by name. Use ask for questions that follow links "
    "(who knows whom, who works where, who I met at which event). Use remember when the user tells you "
    "about someone they met. You cannot delete anything. If the user wants something forgotten, tell them to run "
    "`personal-brain forget person <name>` or the undo command that remember returned."))


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
def remember(name: str, company: str = "", event: str = "", note: str = "",
             person_id: str = "", new_person: bool = False) -> dict:
    """Save that the user met someone. Pull the fields out of what the user said.

    name: the person's full name. company: where they work now. event: where you met.
    note: what you talked about, in the user's words.
    It matches the name, typos included, to people already in the graph. If it is not sure which person it is, it saves
    nothing and returns candidates: ask the user, then call again with person_id (from the candidates)
    or new_person=true. A new company ends their old job and keeps it in their history.
    The reply has an undo command. Only the user can run it: you cannot delete anything."""
    return answer(memory.remember, name, company, event, note, person_id, new_person)


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def find(name: str, label: str = "") -> list | dict:
    """Find people, companies or events by name. Typos and missing accents are fine.

    label: "Person", "Company" or "Event" to search only one kind. Returns ids for ask and remember,
    plus where each person works now, so you can tell two people with the same name apart."""
    # An empty list reaches the agent as an empty reply, so say it in words.
    return answer(memory.find, name, label or None) or {
        "found": 0, "message": "No match. Try part of the name, or use remember to add them as a new person."}


# The schema goes in the description: the agent writes better Cypher when it sees it.
@server.tool(annotations=ToolAnnotations(read_only_hint=True), description=(
    "Run a read-only OpenCypher query on the user's network. Writes are refused.\n"
    "Use $name parameters for values from the user. Returns at most 50 rows; use LIMIT and ORDER BY."
    "\n\nSchema:\n" + memory.SCHEMA))
def ask(cypher: str, params: dict | None = None) -> dict:
    return answer(memory.ask, cypher, params)


def main():
    server.run()


if __name__ == "__main__":
    main()
