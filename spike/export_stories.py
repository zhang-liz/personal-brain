"""THROWAWAY SPIKE: run the demo "story" queries on FalkorDB and save results for the viewer.

Each story is a Cypher query. We store the query text, FalkorDB's run time, and
the node ids it touched, so the viewer can light them up.

    .venv/bin/python spike/export_stories.py --cloud
"""

import json
import sys
from datetime import date
from pathlib import Path

from falkordb import FalkorDB

PROJECT = Path(__file__).resolve().parent.parent
OUT = PROJECT / "data" / "viewer" / "stories.json"

STORIES = [
    {
        "id": "founders",
        "title": "Which founders did I meet at events this year?",
        "focus": "MATCH (m:Me) RETURN id(m)",
        "cypher": f"""MATCH (me:Me)-[:ATTENDED]->(e:Event)<-[:MET_AT]-(p:Person)
      -[:INTO]->(:Topic {{name:'Founder'}})
WHERE e.date >= '{date.today().year}-01-01'
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
]


def connect():
    if "--cloud" in sys.argv:
        env = (PROJECT / ".env").read_text().splitlines()
        url = next(l.split("=", 1)[1].strip() for l in env if l.startswith("FALKORDB_URL="))
        return FalkorDB.from_url(url), "FalkorDB Cloud"
    return FalkorDB(host="localhost", port=6379), "local FalkorDB"


def main():
    db, where = connect()
    g = db.select_graph("network")
    count, max_id = g.query("MATCH (n) RETURN count(n), max(id(n))").result_set[0]
    if count != max_id + 1:
        sys.exit("node ids are not contiguous; viewer indices would not match FalkorDB ids")

    out = []
    for story in STORIES:
        result = g.query(story["cypher"])
        # A column can be a list (collect); flatten it for the lit-node set.
        flat = lambda v: v if isinstance(v, list) else [v]
        ids = sorted({x for row in result.result_set for v in row for x in flat(v) if x is not None})
        # Edges to light: only the path steps, not every edge between lit nodes.
        # Keep direction (me -> friend -> expert) so the viewer can send light pulses along it.
        seen, pairs = set(), []
        for row in result.result_set:
            for a, b in story["pairs"]:
                for x in flat(row[a]):
                    for y in flat(row[b]):
                        if x is not None and y is not None and x != y and (x, y) not in seen:
                            seen.add((x, y))
                            pairs.append([x, y])
        focus = g.query(story["focus"]).result_set[0][0]
        out.append({**{k: story[k] for k in ("id", "title", "cypher")},
                    "focus": focus, "nodes": ids, "pairs": pairs, "rows": len(result.result_set),
                    "ms": round(result.run_time_ms, 2), "source": where})
        print(f"{story['id']:9s} rows={len(result.result_set):4d} nodes={len(ids):4d} pairs={len(pairs):4d} "
              f"{result.run_time_ms:.2f} ms")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
