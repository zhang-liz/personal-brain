"""Where your private data lives. Never inside the installed package.

PB_HOME if set. Else the repo folder when you run from a clone (it has pyproject.toml).
Else ~/.personal-brain. That folder holds .env, my_brain.json, your LinkedIn export and data/.
"""

import os
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
CLONE = PACKAGE.parent.parent
SOCKET_TIMEOUT_S = 30    # longer than any query's own limit, so that a slow query does not look like a dead database
START_DATABASE = ("`docker run -p 6379:6379 -p 3000:3000 -v falkordb_data:/var/lib/falkordb/data -it --rm falkordb/falkordb:6.0.4`, "
                  "or put your FalkorDB Cloud URL in FALKORDB_URL in the .env file")


NO_GRAPH = ("There is no graph on this database yet. Run personal-brain load first. "
            "If your graph is on FalkorDB Cloud, add --cloud to load and export.")


class BadUrl(ValueError):
    """FALKORDB_URL cannot be a database. The message never holds the URL: it has a password in it."""


def cannot_reach(error):
    return f"Cannot reach FalkorDB ({error}). Start it with {START_DATABASE}."


def graph_missing(error):
    """FalkorDB's answer to a read on a graph that does not exist, for example after a restart with no volume."""
    return "empty key" in str(error)


def open_url(url):
    from falkordb import FalkorDB
    try:
        return FalkorDB.from_url(url, socket_timeout=SOCKET_TIMEOUT_S)
    except ValueError:
        raise BadUrl("FALKORDB_URL is not a database URL. It must start with redis://, rediss://, falkor:// or falkors://. "
                     "Check the environment and the .env file.") from None


def printable(text):
    """Text from a graph can hold anything, so keep characters that would control the terminal out of it."""
    return "".join(c for c in text or "" if c.isprintable())


def find_home():
    if os.environ.get("PB_HOME"):
        return Path(os.environ["PB_HOME"]).expanduser()
    return CLONE if (CLONE / "pyproject.toml").exists() else Path.home() / ".personal-brain"


HOME = find_home()


def env_file_url():
    """FALKORDB_URL from the .env file in HOME, or "" if there is none."""
    env_file = HOME / ".env"
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    for line in lines:
        key, _, value = line.strip().removeprefix("export ").partition("=")
        if key.strip() == "FALKORDB_URL":
            return value.split(" #")[0].strip().strip("'\"")
    return ""


def cloud_url():
    """The URL for --cloud: FALKORDB_URL in the environment, then in .env."""
    url = os.environ.get("FALKORDB_URL") or env_file_url()
    if not url:
        sys.exit(f"--cloud needs FALKORDB_URL. Set it in the environment or in {HOME / '.env'}.")
    return url


def write_whole(path, text):
    """An open viewer can read this file at any time, so it must never see half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".{os.getpid()}.tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def refresh_viewer(cloud=False):
    """The viewer's files hold names and ids from the graph, so write them again. With no files, there is nothing to do.

    Returns what went wrong, or nothing."""
    if not (HOME / "data" / "viewer" / "graph.json").exists():
        return ""
    print("Updating the viewer's files...", flush=True)
    r = subprocess.run([sys.executable, "-m", "personal_brain.cli", "export", *(["--cloud"] if cloud else [])], capture_output=True, text=True)
    return r.stderr[-300:] if r.returncode else ""
