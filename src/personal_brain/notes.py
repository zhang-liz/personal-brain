"""personal-brain notes: see what your agent saved, newest first.

Text hidden in a LinkedIn headline could lead an agent to save a fake note. This
list lets you check, and each line has the id that `personal-brain forget note` takes.

    personal-brain notes
    personal-brain notes --limit 100
"""

import sys

import redis

from personal_brain import memory
from personal_brain.paths import cannot_reach, printable

DEFAULT_LIMIT = 20


def main():
    args = sys.argv[1:]
    limit = DEFAULT_LIMIT
    if args:
        if len(args) != 2 or args[0] != "--limit" or not args[1].isdigit() or int(args[1]) < 1:
            sys.exit("Use: personal-brain notes [--limit <number>]")
        limit = int(args[1])
    try:
        g, where = memory.connect()
        total = g.ro_query("MATCH (n:Note) RETURN count(n)").result_set[0][0]
        rows = g.ro_query("MATCH (n:Note) OPTIONAL MATCH (n)-[:ABOUT]->(p:Person)"
                          f" RETURN n.on, n.id, p.name, n.text ORDER BY n.on DESC, id(n) DESC LIMIT {limit}").result_set
    except redis.exceptions.ConnectionError as e:
        sys.exit(cannot_reach(e))
    if not total:
        print(f"No notes on {where}.")
        return
    print(f"Showing {len(rows)} of {total} notes on {where}. To delete one: personal-brain forget note <id>")
    for on, nid, person, text in rows:
        print(f"{on}  {nid}  {printable(person)}: {printable(text)}")


if __name__ == "__main__":
    main()
