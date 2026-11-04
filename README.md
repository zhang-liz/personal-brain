# Personal Brain

Turn your LinkedIn network into a graph you can ask questions, stored in
[FalkorDB](https://www.falkordb.com/), and fly through it in 3D.

[![Personal Brain demo: a galaxy of 19k nodes answering questions](https://raw.githubusercontent.com/zhang-liz/personal-brain/main/docs/demo.gif)](https://zhang-liz.github.io/personal-brain/)

**[Try the live demo](https://zhang-liz.github.io/personal-brain/)**. All connections in it are made up, and its companies and events are only example names. To rebuild it, see `scripts/build_demo.py`.

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
- **Remember**: type "Met Maya from Supabase at AI Engineer Summit, runs developer events" and it is written to FalkorDB live and appears in the galaxy.
- **Demo mode**: the whole show hands-free (press `D`).
- **Agent memory (MCP)**: Claude Code, or any MCP client, can find people, ask the graph, and remember who you met. It cannot delete anything: only you can, with `personal-brain forget`.

Everything runs on your laptop. The viewer works offline (three.js is vendored).

## Quick start: a sample network in 5 minutes

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) and FalkorDB. `uvx` downloads the right Python for you.

The made-up sample network has 241 invented people, startups and events, in the same file formats as a real LinkedIn export. You can try everything before you download your own data.

**1. Start FalkorDB.** Run it in Docker, or see [FalkorDB Cloud](#falkordb-cloud):

```bash
docker run -p 6379:6379 -p 3000:3000 -it --rm falkordb/falkordb:latest
```

**2. Load the sample, and add the server to Claude Code:**

```bash
uvx personal-brain load --sample
claude mcp add personal-brain -- uvx personal-brain mcp
```

Try:

- "Who in my network works at Kestrel Graph?"
- "Who did I talk to about agent memory?"
- "Who could introduce me to someone at a well-funded startup?"
- "I met Oskar Mensah at the Graph & RAG Meetup. He's building agent memory." (There are two Oskar Mensahs, so it asks which one.)

**Other MCP clients:** use command `uvx`, arguments `personal-brain mcp` (stdio).

### FalkorDB Cloud

A free [FalkorDB Cloud](https://app.falkordb.cloud/) instance works without Docker. Pass its URL to the server, and add `--cloud` to `load` and `export`:

```bash
export FALKORDB_URL='falkors://user:password@host:port'
uvx personal-brain load --sample --cloud
claude mcp add personal-brain -e FALKORDB_URL="$FALKORDB_URL" -- uvx personal-brain mcp
```

Or put `FALKORDB_URL=...` in `~/.personal-brain/.env`.

## Use it as your agent's memory (MCP)

`uvx personal-brain mcp` gives your agent three tools on one graph:

| Tool | What it does |
|---|---|
| `remember` | Saves who you met, where they work and where you met. Matches the name to people already in the graph. When it is not sure which person you mean, it saves nothing and asks. A new company ends the old job and keeps it in the history. If the person has several current jobs, it asks which ended. The same call twice saves one meeting. Say when you met if it was not today. |
| `find` | Finds people, companies and events by name, in any script, typos and missing accents included. With `about`, finds people by words in your notes, their topics, skills and headlines, such as "who did I talk to about agent memory?", and says why each one matches. |
| `ask` | Runs a read-only Cypher query (FalkorDB refuses writes), with a 5 second limit and at most 50 rows. The tool description carries the schema. |

No tool deletes, so text hidden in a LinkedIn headline cannot make your agent delete anything. To delete, run the command yourself. It asks first, unless you add `--yes`:

```bash
uvx personal-brain forget note <id>         # one note and everything it added. remember's reply gives this command
uvx personal-brain forget person "Name"     # a person and every note about them. They stay out of future rebuilds
uvx personal-brain forget all-notes         # every note and everything they added, to clear a live demo
```

Every write hangs off a `Note` node, so you can trace each fact to the note that made it. Running `personal-brain load` again with a new LinkedIn export keeps your notes and what they added. If the new import has the same fact, the import wins. `--force` drops your notes. `PB_GRAPH` picks the graph (default `network`). If FalkorDB is not running, the server still starts, and each tool says how to start it. Windows is not tested.

## How it works

```
LinkedIn data export ──┐
                       ├─> load_graph.py ──> FalkorDB ──┬─> mcp_server.py ──> your agent
data/enriched/ ────────┘   (graph "network")            ├─> export_graph.py ──> layout_graph.py ──> viewer
(optional)                                              ├─> export_stories.py ──> viewer
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

### Graph schema

| Nodes | Edges |
|---|---|
| `Me`, `Person`, `Company`, `School`, `Skill`, `Topic`, `Event`, `Investor`, `City`, `Note` | `(Me)-[:CONNECTED]->(Person)`, `(Person)-[:WORKED_AT {title, start, end, current}]->(Company)`, `(Person)-[:STUDIED_AT]->(School)`, `(Person)-[:HAS_SKILL]->(Skill)`, `(Person)-[:HAS_EXPERIENCE {years}]->(Skill)`, `(Person)-[:INTO]->(Topic)`, `(Person)-[:KNOWS {via, place}]-(Person)`, `(Me)-[:ATTENDED]->(Event)`, `(Person)-[:MET_AT]->(Event)`, `(Company)-[:FUNDED_BY {lead, round}]->(Investor)`, `(Person)-[:LIVES_IN]->(City)`, `(Event {upcoming})-[:HOSTED_BY {via}]->(Person or Company)`, `(Event)-[:ABOUT]->(Topic)`, `(Event)-[:HELD_IN]->(City)`, `(Me)-[:MET]->(Person)` and `(Me)-[:WROTE]->(Note)-[:ABOUT]->(Person)` (from Remember) |

## Build your own

**1. Get your LinkedIn data.** LinkedIn > Settings > Data privacy > Get a copy of your data.
Two choices:

- **Connections only**: usually ready in about 10 minutes. You get people, their current company and title, and the day you connected. That is enough for the graph, events and the agent tools.
- **Download larger data archive**: ready in about a day. It adds your messages (only counts are used), posts, jobs and schools.

Unzip it into `~/.personal-brain` (the folder name contains `LinkedInDataExport`). `personal-brain --help` shows your data folder. To use another folder, set `PB_HOME`.

**Or start empty.** Skip the import. The MCP server creates you in an empty graph, and the graph grows as you tell your agent who you met.

Optional: copy [`my_brain.example.json`](my_brain.example.json) to `~/.personal-brain/my_brain.json` to name your events and skip companies.

**2. Build the graph and the viewer data.** Add `--cloud` to both when you use FalkorDB Cloud:

```bash
uvx personal-brain load
uvx personal-brain export
```

**3. Open it.**

```bash
uvx personal-brain view
```

Open http://127.0.0.1:8765. Keys: `2` / `3` switch views, `T` time-lapse, `B` big bang, `D` demo, `Esc` reset.

To explore in the FalkorDB Browser instead, see `docs/demo_queries.cypher`.

## Work on the code

You need Python 3.10 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/) and Docker. In a clone, your data folder is the clone itself, and Claude Code reads `.mcp.json` from any folder inside it.

```bash
git clone https://github.com/zhang-liz/personal-brain && cd personal-brain
python3 -m venv .venv && .venv/bin/pip install -e .
docker run -p 6379:6379 -p 3000:3000 -it --rm falkordb/falkordb:latest
```

Then, in another terminal, run the tests. They use throwaway graphs:

```bash
.venv/bin/python tests/test_memory.py       # the memory tools (add --cloud to use FALKORDB_URL)
.venv/bin/python tests/test_mcp_server.py   # the server, started the way an agent starts it
.venv/bin/python tests/test_package.py      # the built package, used from another folder
```

## Your data stays yours

- Your data stays in `~/.personal-brain`, outside the package. Only you can open that folder. In a clone, the export, `data/`, `.env` and `my_brain.json` are in `.gitignore`. Never commit them.
- Message text is never read into the graph, only how many messages you exchanged with each person.
- `data/viewer/graph.json` in your data folder holds real names. Share screenshots, not that file.
- The local server listens on `127.0.0.1` only and serves only `data/viewer/`.

## Files

| Path | What it does |
|---|---|
| `src/personal_brain/cli.py` | The `personal-brain` command |
| `src/personal_brain/paths.py` | Where your data folder is |
| `src/personal_brain/load_graph.py` | Builds the FalkorDB graph and all inferred links |
| `src/personal_brain/export_graph.py` | Exports every node and edge to `data/viewer/graph.json` |
| `src/personal_brain/layout_graph.py` | Precomputes the layout |
| `src/personal_brain/export_stories.py` | The "Ask the graph" questions and their Cypher |
| `src/personal_brain/server.py` | Serves the viewer and the live Remember API, to the viewer's own page only |
| `src/personal_brain/memory.py` | The memory itself: remember, find, ask, forget (used by the MCP server, the viewer and the forget command) |
| `src/personal_brain/mcp_server.py` | The MCP server for Claude Code and other agents |
| `src/personal_brain/forget.py` | The `personal-brain forget` command |
| `src/personal_brain/notes_backup.py` | Keeps your notes through a rebuild |
| `.mcp.json` | Tells Claude Code how to start the MCP server in a clone |
| `tests/` | End-to-end checks of the memory tools, the MCP server and the built package, on throwaway graphs |
| `src/personal_brain/sample_data/` | The made-up sample network |
| `scripts/make_sample_data.py` | Writes `sample_data/` |
| `src/personal_brain/viewer/index.html` | The WebGL viewer (three.js r170, MIT, vendored) |
| `docs/demo_queries.cypher` | Queries to paste into the FalkorDB Browser |

This is a demo-quality build made at a hackathon, not a product.

## License

MIT, see [LICENSE](LICENSE). three.js in `src/personal_brain/viewer/vendor/` is MIT too (its own LICENSE file).
