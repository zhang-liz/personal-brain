# Changelog

## Unreleased

Security:
- `ask` refuses `LOAD CSV`. FalkorDB allows it in a read-only query, so a query could send anything in your graph to any `https://` server, and a hidden instruction in a headline could ask an agent to do that.

Fixed:
- A tool's error is marked with `isError`, as the MCP spec asks. Before, a client saw every error as a success.
- `ask` no longer says `row_count: 10000` when it cuts a longer result, because 10,000 is only where FalkorDB stops. It says the total is unknown and to use `count()`.
- `ask` stops at 40,000 characters. Before, one query could return 170,000 characters, which is more than Claude Code accepts, so the agent got nothing.
- `find` says `more: true` when it shows 10 matches and there are others. Before, 10 of 116 people looked like all of them.
- `find` about shows at most 300 characters of a note.
- A misspelled field is refused. Before, `notes` for `note` lost the note, and `remember` still said `saved`.
- A slow query over a URL says it was slow. Before, it said FalkorDB could not be reached, and told the user to start it.
- A silent local database no longer holds a tool forever.

Changed:
- `find` by name always returns an object: `found`, `matches`, and `more` and `message` when they apply. Before, it returned a list, or an object when nothing matched.
- Replies are JSON with no indents: 16% to 46% shorter in our test.
- `remember` no longer needs `name` when you pass `person_id` or `follow_up_done`.
- Tools have titles, and say that they touch only this graph (`openWorldHint`) and that a repeat of `remember` changes nothing (`idempotentHint`).

## 0.3.2 (2026-11-04)

Fixed:
- A rebuild keeps all of a note's links. FalkorDB returns at most 10,000 rows by default, so the backup lost the rest, and people could lose their current job, while `load` still said it kept every note.
- `load --sample` asks before it replaces a real network. `--yes` does not ask.
- `load` picks the newest LinkedIn export by the date in the folder name. Before, an older export could win. It prints which one it reads.
- Two notes that differ only in punctuation are two notes. Before, "Loves C#" after "Loves C++" returned `already_saved` and was lost.
- `ended_jobs` with no new company ends the job, with no meeting, and `forget` brings it back. Before, it said `saved`, ended nothing and recorded a meeting.
- `forget person` writes the keep-out list before it deletes, so someone cannot come back after a delete that stopped.
- A second session no longer deletes a note that another session is saving. Before, about 3 notes in 10 were lost when saves overlapped, and the reply still said "saved".
- A rebuild that stopped halfway no longer overwrites newer notes, or brings back a forgotten person. A rebuild now stops if the backup belongs to another graph.
- After a rebuild, `forget` brings back what the new import says, not an old or empty value.
- A rebuild changes only a current job: a past role at the same company keeps its title and its end. A job the import ended stays ended. A new spelling of a company is one job, not two.
- `Kestrel Capital` is no longer the same company as `Kestrel`, and `Kestrel Motion` is not `Kestrel Notion`. Only common last words such as `Labs` and `Inc` can differ. `remember` says when it treats a name as the same job.
- `forget` with several notes: the right company comes back in every order, a title on one job stays off the others, a job or topic that another note also has stays, and a person from a note goes with their last note.
- `remember` saves the same words again on the same day when the fact has changed since: an email back to its old value, a follow-up asked again after it was done, a return to an old job.
- A save that stops at the location step keeps the person's city. A save that stops at a job change cannot leave two current jobs.
- A person from a note whom you forgot stays out of later imports, unless the graph had someone else with that name. `load` says who it left out.
- The job report counts people, not jobs. Two new jobs show on one line.
- The viewer's text box reads `Dr. Jonas` and `Node.js` correctly.
- The viewer's stories read every row. Before, a story with more than 10,000 rows lit up only part of its answer.
- An open viewer updates itself when an export writes new files, when it needs more room, or when an id comes to mean someone else. Before, it could show a forgotten person, or the wrong name for a new person, until you reloaded it. You no longer reload by hand.
- `load` writes the viewer's files again, because a rebuild gives every node a new id. Before, the viewer showed the wrong people until you ran `export`.
- A company or event one letter off a known one no longer merges on a guess. `remember` saves nothing and returns `needs_company` or `needs_event`, so the agent asks. A rebuild and the job report treat them as two companies.
- A backup left by 0.3.1 no longer brings back a forgotten note, or reopens a follow-up, when the graph is still whole. If none of its notes is in the graph, `load` keeps them and says so.
- Forgetting the note that started a job keeps the job when another 0.3.1 note named it too.
- The job report is complete after a rebuild that stopped: the backup now holds the old jobs.

- One odd row or file no longer stops `load`: a line separator in a title, a blank date, a short row, a byte order mark, old bytes, an empty `Profile.csv`, or a job with no month. A person with no date stays in the graph.
- `load --sample` works with no time zone data.
- The MCP server recovers when the graph goes while it runs, for example after a restart with no volume. Before, each tool returned `Invalid graph operation on empty key` until you restarted it.
- A broken `my_brain.json` stops only `load`, and `load` says which file. Before, it stopped every command, the MCP server too.
- `.env` takes quotes, `export`, spaces and a comment after the URL. A bad `FALKORDB_URL` gets a clear message that does not show the URL.
- `notes`, `followups` and `export` with no graph say to run `load`. `export` no longer makes an empty graph.
- `forget` keeps terminal control characters out of what it prints, so hidden codes in a note cannot change the question.
- Sample events stay in the future, and the founders story looks back one year, so the demo does not go empty with time.
- `view` says when its port is in use, and Ctrl-C stops any command with no traceback. `--graph` with no name says what to do.
- The viewer makes room for everything saved since the export. Before, new nodes past 500 ids did not show. It refuses text over 500 characters instead of cutting it.
- `find` with `about`, `forget all-notes` and the viewer's live updates read past FalkorDB's 10,000 row limit.
- The viewer's text box reads `J. Lind` and `St. John` as names.

Changed:
- Every command says how to start FalkorDB when it cannot reach it, with no traceback.
- `view` before `export` says what to run.
- `--help` says first that this is an MCP server.
- The README has a JSON config for other MCP clients.

## 0.3.1 (2026-11-04)

Changed:
- The package description and README now say first that this is an MCP server.
- Listed in the official MCP Registry as `io.github.zhang-liz/personal-brain`.
- `falkordb` 1.8.1 and `igraph` 1.0.0.
- The tests run with pytest.

## 0.3.0 (2026-11-04)

Added:
- `load` prints who changed jobs since your last load, and marks the new job with `noticed_on` and `left`.
- `remember` takes `follow_up`, `due` and `follow_up_done`. `personal-brain followups` lists what is open.
- `remember` takes `title`, `location` and `email`. A correction replaces the old value, and `forget` brings it back.
- `Person.last_message` holds the date of your last LinkedIn message, never its text.
- The viewer lights up each person your agent looks up or saves.
- `personal-brain notes` lists what your agent saved.

Changed:
- `load` keeps your notes through a rebuild. `--force` drops them.
- The `docker run` command has a volume, so local notes survive a restart.
- A new job adds `KNOWS` links to current coworkers.

Fixed:
- A company or event said another way no longer makes a false job or a second event.
- A person you saved first merges with the same person in a new import.
- `export` works after you forget an older person.
- `load` obeys `PB_GRAPH`. Before, it wrote over the `network` graph.
- A running MCP server reads the right fields after a rebuild.
- A save that stops halfway is undone.

## 0.2.0 (2026-11-04)

Removed:
- The `forget` MCP tool. Use `personal-brain forget`, so that only you can delete.
- Shortlists, and `rel_id` from `ask`.

Added:
- `remember` asks which jobs ended when a person has several, saves a repeated call once, and takes a date in `on`.
- `find` takes `about`, to look for people by words in notes, topics, skills and headlines.

Security:
- The viewer's server takes requests only from its own page.

## 0.1.0 (2026-11-04)

First release on PyPI.
