"""Where your private data lives. Never inside the installed package.

PB_HOME if set. Else the repo folder when you run from a clone (it has pyproject.toml).
Else ~/.personal-brain. That folder holds .env, my_brain.json, your LinkedIn export and data/.
"""

import os
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
CLONE = PACKAGE.parent.parent


def find_home():
    if os.environ.get("PB_HOME"):
        return Path(os.environ["PB_HOME"]).expanduser()
    return CLONE if (CLONE / "pyproject.toml").exists() else Path.home() / ".personal-brain"


HOME = find_home()


def env_file_url():
    """FALKORDB_URL from the .env file in HOME, or "" if there is none."""
    env_file = HOME / ".env"
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    return next((l.split("=", 1)[1].strip() for l in lines if l.startswith("FALKORDB_URL=")), "")


def cloud_url():
    """The URL for --cloud: FALKORDB_URL in the environment, then in .env."""
    url = os.environ.get("FALKORDB_URL") or env_file_url()
    if not url:
        sys.exit(f"--cloud needs FALKORDB_URL. Set it in the environment or in {HOME / '.env'}.")
    return url
