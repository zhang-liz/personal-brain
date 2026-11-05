"""personal-brain followups: see what you still have to do, soonest first.

    personal-brain followups
"""

import redis

from personal_brain import memory
from personal_brain.notes import printable
from personal_brain.paths import cannot_reach


def main():
    try:
        g, where = memory.connect()
        rows = g.ro_query("MATCH (f:FollowUp {open: true})-[:FOR]->(p:Person)"
                          " RETURN f.due, f.id, p.name, f.text ORDER BY coalesce(f.due, '9999'), f.on").result_set
    except redis.exceptions.ConnectionError as e:
        raise SystemExit(cannot_reach(e)) from None
    if not rows:
        print(f"No open follow-ups on {where}.")
        return
    print(f"{len(rows)} open follow-ups on {where}. Tell your agent when one is done.")
    for due, fid, person, text in rows:
        print(f"{due or 'no date   '}  {fid}  {printable(person)}: {printable(text)}")


if __name__ == "__main__":
    main()
