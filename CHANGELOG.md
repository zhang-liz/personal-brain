# Changelog

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
