# Personal Brain

Turn your LinkedIn network into a graph you can ask questions, stored in
[FalkorDB](https://www.falkordb.com/), and fly through it in 3D.

After every event you meet people. Two weeks later you forget who they were,
what they work on, and who could introduce you to whom. Personal Brain keeps
that memory as a graph: people, companies, schools, skills, events, topics,
investors and cities, plus inferred "who knows whom" links.

What the viewer does:

- Shows your whole network as a galaxy in WebGL (tested with 19k nodes and 117k edges), with a 2D / 3D toggle.
- **Time-lapse**: watch your network grow, connection by connection, with your events popping up.
- **Ask the graph**: a question types itself, then its Cypher query, then the answer lights up with light pulses along the paths. Example questions:
  - Which founders did I meet at events this year?
  - Who in my network works at big tech?
  - Who can warm-intro me to people at well-funded startups?
  - Which startups host AI events, and who do I know there?
  - Who should I invite to my podcast on agents and memory?
  - Which upcoming agent events can I speak at through people I know?
- **Shortlists**: ranked tables (and CSVs) of co-hosts & sponsors, podcast guests, upcoming events to speak at, and organizers you know. Every row says why it ranks there.
- **Remember**: type "Met Maya from Supabase at AI Engineer Summit, runs developer events" and it is written to FalkorDB live and appears in the galaxy.
- **Demo mode**: the whole show hands-free (press `D`).
- **Agent memory (MCP)**: Claude Code, or any MCP client, can find people, ask the graph, remember who you met and forget on request.

Everything runs on your laptop. The viewer works offline (three.js is vendored).

## Quick start: a sample network in 5 minutes

`sample_data/` is a made-up network (241 invented people, startups and events) in the same file formats as a real LinkedIn export, so you can try everything before you download your own data.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # paste your FalkorDB Cloud URL into FALKORDB_URL
.venv/bin/python spike/load_graph.py --sample --cloud
```

A free [FalkorDB Cloud](https://app.falkordb.cloud/) instance is the fastest start. To run FalkorDB locally instead, start it with Docker and leave out `--cloud`:

```bash
docker run -p 6379:6379 -p 3000:3000 -it --rm falkordb/falkordb:latest
```

Then open Claude Code in this folder. It finds `.mcp.json` and asks to start the `personal-brain` server. Try:

- "Who in my network works at Kestrel Graph?"
- "Who could introduce me to someone at a well-funded startup?"
- "I met Oskar Mensah at the Graph & RAG Meetup. He's building agent memory." (There are two Oskar Mensahs, so it asks which one.)
- "Forget that note."

## Use it as your agent's memory (MCP)

`spike/mcp_server.py` gives your agent four tools on one graph:

| Tool | What it does |
|---|---|
| `remember` | Saves who you met, where they work and where you met. Matches the name to people already in the graph. When it is not sure which person you mean, it saves nothing and asks. A new company ends the old job and keeps it in the history. |
| `find` | Finds people, companies and events by name, typos and missing accents included. |
| `ask` | Runs a read-only Cypher query (FalkorDB refuses writes), with a 5 second limit and at most 50 rows. The tool description carries the schema. |
| `forget` | Forgets one note and everything it added, one relationship, or one person. A forgotten person stays out of future rebuilds. |

Every write hangs off a `Note` node, so you can trace each fact to the note that made it. Claude Code reads `.mcp.json`. For another client, run `.venv/bin/python spike/mcp_server.py` (stdio) from this folder. `PB_GRAPH` picks the graph (default `network`).

Check it end to end on a throwaway graph: `.venv/bin/python tests/test_memory.py --cloud`.

## How it works

```
LinkedIn data export ──┐
                       ├─> load_graph.py ──> FalkorDB ──┬─> mcp_server.py ──> your agent
data/enriched/ ────────┘   (graph "network")            ├─> export_graph.py ──> layout_graph.py ──> viewer
(optional)                                              ├─> export_stories.py, export_shortlists.py ──> viewer
                                                        └─> server.py (live "Remember" API) ──> viewer
```

1. **Your LinkedIn export** gives connections (with the date you connected), your messages (only counts are used, never text), your posts, jobs and schools.
2. **Optional files in `data/enriched/`** add each connection's full job history, schools and skills, company funding, and upcoming events. They use the JSON format of `sample_data/enriched/`. This repo has no tool to collect them; everything works without them.
3. **load_graph.py** builds the graph and infers:
   - `KNOWS` between two connections who worked at the same company or studied at the same school for 3+ months at the same time (at big schools, same start year only), who work at the same company now (when there is no job history), or who are in the same group chat.
   - `Event` nodes: days when you added 5+ connections are probably events. Name them in `my_brain.json`.
   - `Topic` interests (AI agents, agent memory, knowledge graphs, founders, investors, podcasts…) from headlines and skills.
   - With enriched files only:
     - Years of experience per technology from job titles and descriptions.
     - `well_funded` startups: private, active, at most 5,000 people, and a Series round or 8+ investors.
     - `City` from each profile's location, at metro level ("Berlin Metropolitan Area" and "Berlin" are one city; Palo Alto and San Jose are "SF Bay Area").
     - Who hosts each upcoming event: a host who is your connection (LinkedIn match), or a company where you know people (host is the company, a host works there, or the title names it, like "Notion x Clover").
4. **export_graph.py + layout_graph.py** export the graph to JSON and compute a 2D layout (igraph DrL). In 3D, each node type sits on its own layer.
5. **export_stories.py** runs the "Ask the graph" Cypher queries on FalkorDB and saves the answers and timings for the viewer.
6. **export_shortlists.py** ranks co-hosts, podcast guests and places to speak with a simple score you can read and change.

### Graph schema

| Nodes | Edges |
|---|---|
| `Me`, `Person`, `Company`, `School`, `Skill`, `Topic`, `Event`, `Investor`, `City`, `Note` | `(Me)-[:CONNECTED]->(Person)`, `(Person)-[:WORKED_AT {title, start, end, current}]->(Company)`, `(Person)-[:STUDIED_AT]->(School)`, `(Person)-[:HAS_SKILL]->(Skill)`, `(Person)-[:HAS_EXPERIENCE {years}]->(Skill)`, `(Person)-[:INTO]->(Topic)`, `(Person)-[:KNOWS {via, place}]-(Person)`, `(Me)-[:ATTENDED]->(Event)`, `(Person)-[:MET_AT]->(Event)`, `(Company)-[:FUNDED_BY {lead, round}]->(Investor)`, `(Person)-[:LIVES_IN]->(City)`, `(Event {upcoming})-[:HOSTED_BY {via}]->(Person or Company)`, `(Event)-[:ABOUT]->(Topic)`, `(Event)-[:HELD_IN]->(City)`, `(Me)-[:MET]->(Person)` and `(Me)-[:WROTE]->(Note)-[:ABOUT]->(Person)` (from Remember) |

## Build your own

You need Python 3.10+ and FalkorDB (a free [FalkorDB Cloud](https://app.falkordb.cloud/)
instance, or Docker).

**1. Get your LinkedIn data.** LinkedIn > Settings > Data privacy > Get a copy of your data.
Two choices:

- **Connections only**: usually ready in about 10 minutes. You get people, their current company and title, and the day you connected. That is enough for the graph, events and the agent tools.
- **Download larger data archive**: ready in about a day. It adds your messages (only counts are used), posts, jobs and schools.

Unzip it into this folder (the folder name contains `LinkedInDataExport`).

**Or start empty.** Skip the import. The MCP server creates you in an empty graph, and the graph grows as you tell your agent who you met.

**2. Install.**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env                  # add your FALKORDB_URL
cp my_brain.example.json my_brain.json  # optional: name your events, list competitors
```

For local FalkorDB instead of Cloud:

```bash
docker run -p 6379:6379 -p 3000:3000 -it --rm falkordb/falkordb:latest
```

Then leave out `--cloud` in the commands below.

**3. Build the graph and the viewer data.**

```bash
.venv/bin/python spike/load_graph.py --cloud
.venv/bin/python spike/export_graph.py --cloud
.venv/bin/python spike/layout_graph.py --dims=2
.venv/bin/python spike/export_stories.py --cloud
.venv/bin/python spike/export_shortlists.py --cloud
```

**4. Open it.**

```bash
./spike/serve_viewer.sh
```

Open http://127.0.0.1:8765 (shortlists: http://127.0.0.1:8765/shortlists.html). Keys: `2` / `3` switch views, `T` time-lapse, `B` big bang, `D` demo, `Esc` reset.

To explore in the FalkorDB Browser instead, see `spike/demo_queries.cypher`.

## Your data stays yours

- The LinkedIn export, everything in `data/`, `.env` and `my_brain.json` are in `.gitignore`. Never commit them.
- Message text is never read into the graph, only how many messages you exchanged with each person.
- `data/viewer/graph.json` and the shortlists hold real names. Share screenshots, not those files.
- The local server listens on `127.0.0.1` only and serves only `data/viewer/`.

## Files

| Path | What it does |
|---|---|
| `spike/load_graph.py` | Builds the FalkorDB graph and all inferred links |
| `spike/export_graph.py` | Exports every node and edge to `data/viewer/graph.json` |
| `spike/layout_graph.py` | Precomputes the layout |
| `spike/export_stories.py` | The "Ask the graph" questions and their Cypher |
| `spike/export_shortlists.py` | Ranked shortlists: co-hosts & sponsors, podcast guests, places to speak |
| `spike/viewer/shortlists.html` | The shortlists page (filter, download CSV) |
| `spike/server.py` | Serves the viewer and the live Remember / Forget API |
| `spike/memory.py` | The memory itself: remember, find, ask, forget (used by the MCP server and the viewer) |
| `spike/mcp_server.py` | The MCP server for Claude Code and other agents |
| `.mcp.json` | Tells Claude Code how to start the MCP server |
| `tests/test_memory.py` | End-to-end check of the memory tools on a throwaway graph |
| `sample_data/` | The made-up sample network |
| `scripts/make_sample_data.py` | Writes `sample_data/` |
| `spike/viewer/index.html` | The WebGL viewer (three.js r170, MIT, vendored) |
| `spike/demo_queries.cypher` | Queries to paste into the FalkorDB Browser |

This is a demo-quality build made at a hackathon, not a product.

## License

MIT, see [LICENSE](LICENSE). three.js in `spike/viewer/vendor/` is MIT too (its own LICENSE file).
