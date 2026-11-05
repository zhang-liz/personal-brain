"""Run the demo "story" queries on FalkorDB and save results for the viewer.

Each story is a Cypher query. We store the query text, FalkorDB's run time, and
the node ids it touched, so the viewer can light them up.

    personal-brain export --cloud
"""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from falkordb import FalkorDB

from personal_brain.paths import HOME, cloud_url, open_url, write_whole
OUT = Path(os.environ.get("PB_VIEWER_DIR", HOME / "data" / "viewer")) / "stories.json"
PAGE = 5000

STORIES = [
    {
        "id": "founders",
        "title": "Which founders did I meet at events in the last year?",
        "focus": "MATCH (m:Me) RETURN id(m)",
        "cypher": f"""MATCH (me:Me)-[:ATTENDED]->(e:Event)<-[:MET_AT]-(p:Person)
      -[:INTO]->(:Topic {{name:'Founder'}})
WHERE e.date >= '{date.today() - timedelta(days=365)}'
RETURN id(me), id(e), id(p)""",
        "pairs": [(0, 1), (1, 2)],
    },
    {
        "id": "bigtech",
        "title": "Who in my network works at big tech?",
        "focus": "MATCH (m:Me) RETURN id(m)",
        "cypher": """MATCH (me:Me)-[:CONNECTED]->(p:Person)
      -[:WORKED_AT {current:true}]->(c:Company)
WHERE c.name IN ['Google', 'Meta', 'Microsoft', 'Apple', 'NVIDIA',
  'Amazon', 'Amazon Web Services (AWS)', 'IBM', 'Salesforce', 'Oracle']
RETURN id(me), id(p), id(c)""",
        "pairs": [(0, 1), (1, 2)],
    },
    {
        "id": "funded-intro",
        "title": "Who can warm-intro me to people at well-funded startups?",
        "focus": "MATCH (m:Me) RETURN id(m)",
        "cypher": """MATCH (me:Me)-[:CONNECTED]->(friend:Person)-[:KNOWS]-(p:Person)
      -[:WORKED_AT {current:true}]->(c:Company {well_funded:true})
OPTIONAL MATCH (c)-[:FUNDED_BY]->(i:Investor)
RETURN id(me), id(friend), id(p), id(c), collect(id(i)) AS investors""",
        "pairs": [(0, 1), (1, 2), (2, 3), (3, 4)],
    },
    {
        "id": "cohosts",
        "title": "Which startups host AI events, and who do I know there?",
        "focus": "MATCH (m:Me) RETURN id(m)",
        "cypher": """MATCH (me:Me)-[:CONNECTED]->(p:Person)-[:WORKED_AT {current:true}]->(c:Company)
      <-[:HOSTED_BY]-(e:Event {upcoming:true})
WHERE NOT c.name IN ['Google', 'Meta', 'Microsoft', 'Apple', 'NVIDIA', 'Amazon',
  'Amazon Web Services (AWS)', 'IBM', 'Salesforce', 'Oracle', 'GitHub']
RETURN id(me), id(p), id(c), id(e)""",
        "pairs": [(0, 1), (1, 2), (2, 3)],
    },
    {
        "id": "podcast",
        "title": "Who should I invite to my podcast on agents and memory?",
        "focus": "MATCH (m:Me) RETURN id(m)",
        "cypher": """MATCH (me:Me)-[:CONNECTED]->(p:Person)-[:INTO]->(t:Topic)
WHERE t.name IN ['Agent Memory', 'Knowledge Graphs', 'AI Agents', 'RAG & Search']
  AND ANY(w IN ['founder', 'cto', 'ceo', 'chief', 'head of', 'vp', 'director',
                'principal', 'staff', 'research'] WHERE toLower(p.headline) CONTAINS w)
RETURN id(me), id(p), id(t)""",
        "pairs": [(0, 1), (1, 2)],
    },
    {
        "id": "speak",
        "title": "Which upcoming agent events can I speak at through people I know?",
        "focus": "MATCH (m:Me) RETURN id(m)",
        # Two ways in: you know the host, or you know someone at the host company.
        "cypher": """MATCH (me:Me)-[:CONNECTED]->(p:Person)<-[:HOSTED_BY]-(e:Event {upcoming:true})
      -[:ABOUT]->(t:Topic)
WHERE t.name IN ['AI Agents', 'Agent Memory', 'Knowledge Graphs', 'RAG & Search']
RETURN id(me) AS me, id(p) AS friend, id(p) AS host, id(e) AS event, id(t) AS topic
UNION
MATCH (me:Me)-[:CONNECTED]->(p:Person)-[:WORKED_AT {current:true}]->(c:Company)
      <-[:HOSTED_BY]-(e:Event {upcoming:true})-[:ABOUT]->(t:Topic)
WHERE t.name IN ['AI Agents', 'Agent Memory', 'Knowledge Graphs', 'RAG & Search']
RETURN id(me) AS me, id(p) AS friend, id(c) AS host, id(e) AS event, id(t) AS topic""",
        "pairs": [(0, 1), (1, 2), (2, 3), (3, 4)],
    },
    {
        "id": "investors",
        "title": "Who can warm-intro me to the investors behind OpenAI?",
        "focus": "MATCH (m:Me) RETURN id(m)",
        # The investor firm is a Company (its people work there) and an Investor (it funds companies), same id.
        "cypher": """MATCH (t:Company {name:'OpenAI'})-[:FUNDED_BY]->(i:Investor)
MATCH (firm:Company {id: i.id})<-[:WORKED_AT {current:true}]-(vc:Person)-[:INTO]->(:Topic {name:'Investing'})
MATCH (me:Me)-[:CONNECTED]->(friend:Person)-[:KNOWS]-(vc)
WHERE friend.messages > 0
RETURN id(me), id(friend), id(vc), id(firm), id(i), id(t)""",
        "pairs": [(0, 1), (1, 2), (2, 3), (4, 5)],
    },
    {
        "id": "executives",
        "title": "Which founders and CEOs can I reach through people I talk to?",
        "focus": "MATCH (m:Me) RETURN id(m)",
        "cypher": """MATCH (me:Me)-[:CONNECTED]->(friend:Person)-[:KNOWS]-(p:Person)
WHERE friend.messages > 0 AND p.messages = 0
  AND ANY(w IN ['founder', 'ceo', 'chief', 'cto', 'coo', 'cfo'] WHERE toLower(p.title) CONTAINS w)
RETURN id(me), id(friend), id(p)""",
        "pairs": [(0, 1), (1, 2)],
    },
]


def split_columns(text):
    """The parts of a RETURN, split at each comma that is not inside brackets."""
    columns, depth, part = [], 0, ""
    for c in text:
        depth += (c in "([") - (c in ")]")
        if c == "," and not depth:
            columns.append(part)
            part = ""
        else:
            part += c
    return [c.strip() for c in columns + [part]]


def every_row(g, cypher):
    """All rows of a story, and the time they took. A result holds at most 10,000 rows, and the rest would be lost with no error."""
    rows, seen, ms = [], set(), 0.0
    for query in cypher.split("\nUNION\n"):
        match, _, returned = query.rpartition("RETURN ")
        columns = [c.rsplit(" AS ", 1)[0] for c in split_columns(returned)]
        named = ", ".join(f"{c} AS c{i}" for i, c in enumerate(columns))
        order = ", ".join(f"c{i}" for i, c in enumerate(columns) if "collect(" not in c)
        skip = 0
        while True:
            result = g.ro_query(f"{match}RETURN {named} ORDER BY {order} SKIP {skip} LIMIT {PAGE}")
            ms += result.run_time_ms
            if not result.result_set:
                break
            skip += len(result.result_set)
            for row in result.result_set:
                key = json.dumps(row)    # UNION gives a row once
                if key not in seen:
                    seen.add(key)
                    rows.append(row)
    return rows, ms


def connect():
    if "--cloud" in sys.argv:
        return open_url(cloud_url()), "FalkorDB Cloud"
    return FalkorDB(host="localhost", port=6379), "local FalkorDB"


def main():
    db, where = connect()
    g = db.select_graph(os.environ.get("PB_GRAPH", "network"))
    out = []
    for story in STORIES:
        rows, ms = every_row(g, story["cypher"])
        # A column can be a list (collect); flatten it for the lit-node set.
        flat = lambda v: v if isinstance(v, list) else [v]
        ids = sorted({x for row in rows for v in row for x in flat(v) if x is not None})
        # Edges to light: only the path steps, not every edge between lit nodes.
        # Keep direction (me -> friend -> expert) so the viewer can send light pulses along it.
        seen, pairs = set(), []
        for row in rows:
            for a, b in story["pairs"]:
                for x in flat(row[a]):
                    for y in flat(row[b]):
                        if x is not None and y is not None and x != y and (x, y) not in seen:
                            seen.add((x, y))
                            pairs.append([x, y])
        focus = g.query(story["focus"]).result_set[0][0]
        out.append({**{k: story[k] for k in ("id", "title", "cypher")},
                    "focus": focus, "nodes": ids, "pairs": pairs, "rows": len(rows),
                    "ms": round(ms, 2), "source": where})
        print(f"{story['id']:9s} rows={len(rows):4d} nodes={len(ids):4d} pairs={len(pairs):4d} {ms:.2f} ms")
    write_whole(OUT, json.dumps(out, ensure_ascii=False, indent=1))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
