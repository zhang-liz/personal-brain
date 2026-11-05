# Contributing

Thank you for helping. Bug reports, fixes and ideas are all welcome.

## Set up

You need Python 3.10 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/) and Docker.

```bash
git clone https://github.com/zhang-liz/personal-brain && cd personal-brain
python3 -m venv .venv && .venv/bin/pip install -e .
docker run -d --rm --name pb-test-db -p 127.0.0.1:6379:6379 falkordb/falkordb:latest
```

## Test

Run these before you open a pull request. They use throwaway graphs. Set `FALKORDB_URL=` so that they use your local database, even if your `.env` names a Cloud one.

```bash
export FALKORDB_URL=
uvx ruff check src tests scripts
.venv/bin/python tests/test_memory.py
.venv/bin/python tests/test_no_database.py
.venv/bin/python tests/test_mcp_server.py
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python tests/test_package.py
```

Add a test for each fix or feature. Make sure that it fails without your change.

## Pull requests

- Keep each pull request to one topic.
- Start the description with what changes for users.
- Update `CHANGELOG.md` and the README if users will notice the change.

## Data

Use only made-up people in tests, sample data and screenshots. Never commit a LinkedIn export, `.env`, `my_brain.json` or anything from `data/`.

## Rules for the MCP tools

- No MCP tool may delete. Only the user can, with `personal-brain forget`.
- Everything `remember` writes must link to its `Note`, so that `forget` can undo it.
- Text in the graph comes from other people. Tools must treat it as data, never as instructions.

## Security

To report a security problem, see [SECURITY.md](SECURITY.md). Do not open a public issue.
