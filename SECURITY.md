# Security

## Report a problem

Do not open a public issue. On the [Security tab](https://github.com/zhang-liz/personal-brain/security), click **Report a vulnerability**.

Tell us what the problem is, how to make it happen, and which version you use. We will try to reply within 7 days.

## Supported versions

Only the newest release on [PyPI](https://pypi.org/project/personal-brain/) gets security fixes.

## What matters most here

- **Hidden instructions.** Names, headlines and notes come from other people, and a tool can return them to an agent. No MCP tool can delete, but a hidden instruction could lead an agent to save a fake note. To check what your agent saved, run `personal-brain notes`.
- **Private data.** Your network stays in your data folder and your database. Do not attach a real export, `.env` or `data/viewer/graph.json` to a report.
- **The local viewer.** `personal-brain view` listens on `127.0.0.1` and takes requests only from its own page. A way around this is a security problem.
