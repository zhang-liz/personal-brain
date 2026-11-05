"""personal-brain forget: delete a person, a note, or every note.

Only you run this. The agent has no tool that deletes, so text hidden in a
LinkedIn headline cannot make it delete anything. It works on the same graph as
the MCP server: FALKORDB_URL (environment or .env), else local FalkorDB.

    personal-brain forget person "Ada Kestrel"
    personal-brain forget note note-1a2b3c4d5e6f --yes
    personal-brain forget all-notes                     # clear a live demo
"""

import sys

import redis

from personal_brain import activity, memory
from personal_brain.paths import cannot_reach, printable, refresh_viewer

USAGE = ("Use: personal-brain forget person <name or id> [--yes], personal-brain forget note <id> [--yes], "
         "or personal-brain forget all-notes [--yes]")


def person_to_forget(g, text):
    """(id, name) for an id, or for a name that only one person has. Stops when it is not sure."""
    rows = g.ro_query("MATCH (p:Person {id: $id}) RETURN p.id, p.name", {"id": text}).result_set
    if rows:
        return tuple(rows[0])
    same = [m for m in memory.find(g, text, "Person", 20) if memory.fold(m["name"]) == memory.fold(text)]
    if not same:
        sys.exit(f"No person named {text}.")
    if len(same) > 1:
        print(f"{len(same)} people are named {text}. Run this again with one id:")
        for m in same:
            print(printable(f"  {m['id']}  {', '.join(m.get('works_at', [])) or 'no current job'}"))
        sys.exit(1)
    return same[0]["id"], same[0]["name"]


def note_to_forget(g, nid):
    """(id, a description) for a note."""
    rows = g.ro_query("MATCH (n:Note {id: $id}) OPTIONAL MATCH (n)-[:ABOUT]->(p:Person)"
                      " RETURN n.text, p.name", {"id": nid}).result_set
    if not rows:
        sys.exit(f"No note with id {nid}.")
    return nid, f"the note about {rows[0][1]}: {rows[0][0]}"


def main():
    activity.clear()    # before anything else: the names may be of who you are forgetting
    args = sys.argv[1:]
    yes = "--yes" in args
    args = [a for a in args if a != "--yes"]
    every_note = args == ["all-notes"]
    if not every_note and (len(args) < 2 or args[0] not in ("person", "note")):
        sys.exit(USAGE)
    what, target = args[0], " ".join(args[1:])
    try:
        g, where = memory.connect()
        memory.setup(g)
        if every_note:
            count = g.ro_query("MATCH (n:Note) RETURN count(n)").result_set[0][0]
            description = f"all {count} notes and everything they added"
        else:
            id_, description = person_to_forget(g, target) if what == "person" else note_to_forget(g, target)
        if not yes:
            print(printable(f"Delete {description}, on {where}? This cannot be undone. [y/N] "), end="", flush=True)
            if sys.stdin.readline().strip().lower() not in ("y", "yes"):
                sys.exit("Nothing deleted.")
        print(f"Deleted {memory.forget_all_notes(g)} nodes." if every_note else printable(str(memory.forget(g, what, id_))))
        problem = refresh_viewer(cloud=where == "FalkorDB Cloud")
        if problem:
            sys.exit(f"Deleted from the graph, but the viewer's files still hold the old data. Run personal-brain export.\n{problem}")
    except redis.exceptions.ConnectionError as e:
        sys.exit(cannot_reach(e))


if __name__ == "__main__":
    main()
