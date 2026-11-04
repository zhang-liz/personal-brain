"""What your agent just did, so the viewer can light it up. A small file in your data folder: names, never note text."""

import json
import os
import time

from personal_brain.paths import HOME

FILE = HOME / "data" / "activity.json"
KEEP = 50
MOST_NAMES = 30


def names_in(tool, result):
    """The names of the people, companies and events in a tool's reply."""
    if tool == "remember":
        found = [result["person"]] if result.get("person") else result.get("candidates", [])
    elif tool == "about":
        found = result.get("people", [])
    elif tool == "find":
        found = result
    else:
        columns = result.get("columns", [])
        found = [v if isinstance(v, dict) else {"name": v}
                 for row in result.get("rows", []) for column, v in zip(columns, row)
                 if isinstance(v, dict) or (isinstance(v, str) and column.lower().endswith("name"))]
    names = [m["name"] for m in found if isinstance(m, dict) and isinstance(m.get("name"), str) and m["name"]]
    return list(dict.fromkeys(names))[:MOST_NAMES]


def read():
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"seq": 0, "events": []}


def log(tool, result):
    """Add one event. This file is only for the viewer, so a problem here must never fail the agent's tool call."""
    try:
        if isinstance(result, dict) and "error" in result:
            return
        data = read()
        seq = data["seq"] + 1
        event = {"seq": seq, "tool": tool, "names": names_in(tool, result),
                 "saved": tool == "remember" and result.get("status") == "saved", "at": round(time.time())}
        FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = FILE.with_suffix(f".{os.getpid()}.tmp")
        temp.write_text(json.dumps({"seq": seq, "events": (data["events"] + [event])[-KEEP:]}, ensure_ascii=False), encoding="utf-8")
        temp.replace(FILE)
    except Exception:
        pass


def since(seq):
    """Events after seq. A negative seq asks only where the list is now, so a new page does not replay old events."""
    data = read()
    return {"seq": data["seq"], "events": [e for e in data["events"] if seq >= 0 and e["seq"] > seq]}


def clear():
    """The names may be of someone you just forgot."""
    FILE.unlink(missing_ok=True)
