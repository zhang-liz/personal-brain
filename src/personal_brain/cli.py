"""The personal-brain command."""

import importlib
import sys

from personal_brain import __version__
from personal_brain.paths import HOME

USAGE = f"""personal-brain {__version__}: your LinkedIn network as agent memory in FalkorDB.

Commands:
  mcp                      run the MCP server for your agent (stdio)
  load [--sample] [--cloud] [--force]
                           build the graph from your LinkedIn export, or from the made-up sample.
                           It keeps your agent's notes. --force drops them
  export [--cloud]         write the viewer's data from the graph
  view                     open the viewer's server on http://127.0.0.1:8765
  forget person <name or id> [--yes]
  forget note <id> [--yes]
  forget all-notes [--yes]
                           delete a person, a note or every note. Only you can do this, not your agent

Your data folder: {HOME}
"""


def run(module, *args):
    """Each module reads its options from sys.argv, some of them on import."""
    sys.argv = [f"personal-brain {module}", *args]
    importlib.import_module(f"personal_brain.{module}").main()


def main():
    command, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    if command == "--version":
        print(f"personal-brain {__version__}")
    elif command in ("mcp", "load", "export", "view", "forget"):
        HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
        if command == "mcp":
            run("mcp_server", *args)
        elif command == "load":
            run("load_graph", *args)
        elif command == "view":
            run("server", *args)
        elif command == "forget":
            run("forget", *args)
        else:
            run("export_graph", *args)
            run("layout_graph", "--dims=2")
            run("export_stories", *args)
    elif command in ("", "-h", "--help", "help"):
        print(USAGE)
    else:
        sys.exit(f"Unknown command: {command}\n\n{USAGE}")


if __name__ == "__main__":
    main()
