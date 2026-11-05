# Changelog

## Unreleased

Fixed:
- A second session no longer deletes a note that another session is saving. Before, about 3 notes in 10 were lost when saves overlapped, and the reply still said "saved".
- A rebuild that stopped halfway no longer overwrites newer notes, or brings back a forgotten person. A rebuild now stops if the backup belongs to another graph.
- After a rebuild, `forget` brings back what the new import says, not an old or empty value.
- A rebuild changes only a current job: a past role at the same company keeps its title and its end. A job the import ended stays ended. A new spelling of a company is one job, not two.
- `Kestrel Capital` is no longer the same company as `Kestrel`, and `Kestrel Motion` is not `Kestrel Notion`. Only common last words such as `Labs` and `Inc` can differ. `remember` says when it treats a name as the same job.
- `forget` with several notes: the right company comes back, a title on one job stays off the others, a job or topic that another note also has stays, and a person from a note goes with their last note.
- `remember` saves the same words again on the same day when the fact has changed since: an email back to its old value, a follow-up asked again after it was done, a return to an old job.
- A save that stops at the location step keeps the person's city. A save that stops at a job change cannot leave two current jobs.
- A person from a note whom you forgot stays out of later imports, unless the graph had someone else with that name. `load` says who it left out.
- The job report counts people, not jobs. Two new jobs show on one line.
- The viewer's text box reads `Dr. Jonas` and `Node.js` correctly.

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
