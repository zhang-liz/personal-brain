"""Keep your notes through a rebuild.

A rebuild deletes the graph. Before that, snapshot() reads each note and what it
added. They wait in data/notes-backup.json, and restore() puts them back in the
new graph. If a rebuild stops halfway, the next one finds them in that file.
"""

import json

from personal_brain.companies import norm, same_company
from personal_brain.paths import HOME

BACKUP = HOME / "data" / "notes-backup.json"
FIELDS = ("title", "location", "email")    # facts a note can correct on a person: the same as memory.FIELDS
ONE_PER_PAIR = {"INTO", "ATTENDED", "KNOWS"}   # when the import already has this link, the note adds no second one
PAGE = 5000    # rows per page when the backup reads the graph


def every_row(graph, match, columns, params=None, timeout=None):
    """All rows of a query, in pages: a result holds at most 10,000 rows, and the rest would be lost with no error.

    `match` ends with a WHERE that names the place of each row as `at`."""
    rows, last = [], -1
    while page := graph.ro_query(f"{match} AND at > $last RETURN {columns}, at ORDER BY at LIMIT {PAGE}",
                                 {**(params or {}), "last": last}, timeout=timeout).result_set:
        rows += [row[:-1] for row in page]
        last = page[-1][-1]
    return rows


def sync_company(graph, pid, only_with_jobs=False):
    """A person's company follows their jobs: it stays if it is still a current job, else it is another current job, else empty.

    only_with_jobs: with no current job, leave it. The import can name a company with no job link, such as "Stealth"."""
    rows = graph.ro_query("MATCH (p:Person {id: $id}) OPTIONAL MATCH (p)-[:WORKED_AT {current: true}]->(c:Company)"
                          " RETURN p.company, collect(c.name)", {"id": pid}).result_set
    if not rows or (only_with_jobs and not rows[0][1]):
        return
    company, jobs = rows[0]
    if not any(same_company(norm(job), norm(company)) for job in jobs):
        graph.query("MATCH (p:Person {id: $id}) SET p.company = $company, p.headline = CASE WHEN p.source = 'agent' THEN $company ELSE p.headline END",
                    {"id": pid, "company": jobs[0] if jobs else ""})


def snapshot(graph):
    """Every note, each node a note made, each link a note made or ended, and the nodes on the other end."""
    return {
        "nodes": [{"label": l, "props": p} for l, p in every_row(
            graph, "MATCH (x) WITH x, id(x) AS at WHERE (x:Note OR x.source = 'agent')", "labels(x)[0], properties(x)")],
        "about": [{"note": n, "label": l, "props": p} for n, l, p in every_row(
            graph, "MATCH (n:Note)-[r:ABOUT]->(x) WITH n, x, id(r) AS at WHERE true", "n.id, labels(x)[0], properties(x)")],
        "links": [{"from_label": al, "from": a, "type": t, "to_label": bl, "to": b, "props": r} for al, a, t, bl, b, r in every_row(
            graph, "MATCH (a)-[r]->(b) WITH a, r, b, id(r) AS at WHERE (r.note IS NOT NULL OR r.ended_by IS NOT NULL OR r.title_note IS NOT NULL)",
            "labels(a)[0], properties(a), type(r), labels(b)[0], properties(b), properties(r)")],
    }


def pending():
    """The snapshot a rebuild left behind when it stopped halfway, or None."""
    return json.loads(BACKUP.read_text(encoding="utf-8")) if BACKUP.exists() else None


def owner(props):
    """The note that made or changed this link."""
    return props.get("note") or props.get("ended_by") or props.get("title_note")


def merge(saved, live):
    """The backup, plus each note saved in the half-built graph since the rebuild stopped."""
    if not live:
        return saved
    have = {n["props"]["id"] for n in saved["nodes"] if n["label"] == "Note"}
    new = {n["props"]["id"] for n in live["nodes"] if n["label"] == "Note"} - have
    return {"nodes": saved["nodes"] + [n for n in live["nodes"] if n["props"]["id"] in new or n["props"].get("note") in new],
            "about": saved["about"] + [a for a in live["about"] if a["note"] in new],
            "links": saved["links"] + [l for l in live["links"] if owner(l["props"]) in new]}


def without_forgotten(snap, forgotten):
    """A person forgotten since the backup stays forgotten, with every note about them."""
    notes = {a["note"] for a in snap["about"] if a["props"]["id"] in forgotten}
    return {"nodes": [n for n in snap["nodes"] if not {n["props"]["id"], n["props"].get("note")} & (notes | forgotten)],
            "about": [a for a in snap["about"] if a["note"] not in notes],
            "links": [l for l in snap["links"] if owner(l["props"]) not in notes and not {l["from"]["id"], l["to"]["id"]} & forgotten]}


def choose(saved, live, place, forgotten):
    """What a rebuild must keep: the backup from a rebuild that stopped, the live graph (None if it has none), or both."""
    if saved is None:
        return live and {**live, "place": place, "graph_deleted": False}
    if saved.get("place", place) != place:
        raise SystemExit(f"A rebuild of {saved['place']} stopped halfway, and the only copy of its notes is in {BACKUP}.\n"
                         f"Run load for {saved['place']} first, or delete that file to drop those notes.")
    if live and saved.get("graph_deleted") is False:
        return {**live, "place": place, "graph_deleted": False}    # it stopped before it deleted the graph, so the graph is right
    return {**without_forgotten(merge(saved, live), forgotten), "place": place, "graph_deleted": True}


def save(snap):
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    temp = BACKUP.with_suffix(".tmp")
    temp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    temp.replace(BACKUP)


def clear(place=None):
    """Delete the backup, unless it belongs to another graph: it may be the only copy of those notes."""
    saved = pending() if place else None
    if not saved or saved.get("place", place) == place:
        BACKUP.unlink(missing_ok=True)


def name(text):
    """Labels and link types go into the query as text, so accept only plain names."""
    if not text.isidentifier():
        raise ValueError(f"Bad name in {BACKUP}: {text}")
    return text


def merge_targets(graph, snap):
    """People saved by a note whom the new import now has: same name, and the same company or no company saved."""
    saved_jobs = {}
    for l in snap["links"]:
        if l["type"] == "WORKED_AT" and l["from_label"] == "Person":
            saved_jobs.setdefault(l["from"]["id"], []).append(l["to"]["id"])
    merged = {}
    for n in snap["nodes"]:
        props = n["props"]
        if n["label"] != "Person" or props.get("source") != "agent" or not props.get("search"):
            continue
        rows = graph.ro_query("MATCH (p:Person {search: $search}) OPTIONAL MATCH (p)-[:WORKED_AT]->(c:Company)"
                              " RETURN p.id, collect(c.id)", {"search": props["search"]}).result_set
        mine = saved_jobs.get(props["id"], [])
        at = [pid for pid, jobs in rows if any(same_company(a, b) for a in mine for b in jobs)]
        if len(at) == 1:
            merged[props["id"]] = at[0]
        elif not mine and len(rows) == 1:
            merged[props["id"]] = rows[0][0]
    return merged


def restore(graph, snap):
    """Put the snapshot back. What the new import already has stays as the import says.

    Returns how many notes came back, and how many people merged with someone in the import."""
    merged = merge_targets(graph, snap)

    def person_id(label, props):
        return merged.get(props["id"], props["id"]) if label == "Person" else props["id"]

    # What this import says about each person. Forgetting the oldest note brings this back, not what an older import said.
    imported_facts = {}
    for pid in {person_id(a["label"], a["props"]) for a in snap["about"] if a["label"] == "Person"}:
        rows = graph.ro_query("MATCH (p:Person {id: $id}) OPTIONAL MATCH (p)-[:LIVES_IN]->(c:City)"
                              " RETURN p.title, p.location, p.email, p.company, collect(c.id), collect(c.name)", {"id": pid}).result_set
        if rows:
            imported_facts[pid] = rows[0]

    def add_node(label, props, note):
        if label != "Me" and person_id(label, props) == props["id"]:
            # A node missing from the new import now comes from the note, so forgetting the note removes it.
            graph.query(f"MERGE (n:{name(label)} {{id: $id}}) ON CREATE SET n += $props",
                        {"id": props["id"], "props": {"source": "agent", "note": note, **props}})

    for n in snap["nodes"]:
        add_node(n["label"], n["props"], n["props"].get("note") or n["props"]["id"])
    for a in snap["about"]:
        add_node(a["label"], a["props"], a["note"])
    for l in snap["links"]:
        note = l["props"].get("note") or l["props"].get("ended_by") or l["props"].get("title_note")
        add_node(l["from_label"], l["from"], note)
        add_node(l["to_label"], l["to"], note)

    for n in snap["nodes"]:
        if n["label"] == "Note":
            graph.query("MATCH (me:Me), (n:Note {id: $id}) CREATE (me)-[:WROTE]->(n)", {"id": n["props"]["id"]})
    for a in snap["about"]:
        graph.query(f"MATCH (n:Note {{id: $note}}), (x:{name(a['label'])} {{id: $id}}) CREATE (n)-[:ABOUT]->(x)",
                    {"note": a["note"], "id": person_id(a["label"], a["props"])})

    # Facts a note corrected. The city from the import goes, or the person would live in two places.
    for a in snap["about"]:
        pid = person_id(a["label"], a["props"])
        for field in ("title", "location", "email"):
            if a["props"].get(f"{field}_note"):
                graph.query(f"MATCH (p:Person {{id: $id}}) SET p.{field} = $value, p.{field}_note = $note",
                            {"id": pid, "value": a["props"].get(field), "note": a["props"][f"{field}_note"]})
                if field == "location":
                    graph.query("MATCH (:Person {id: $id})-[r:LIVES_IN]->() WHERE r.note IS NULL DELETE r", {"id": pid})

    def same_job_now(pid, company):
        """This person's current jobs at this company, as the new import spells it."""
        rows = graph.ro_query("MATCH (:Person {id: $id})-[:WORKED_AT {current: true}]->(c:Company) RETURN c.id", {"id": pid}).result_set
        return [c for (c,) in rows if same_company(c, company)]

    def change_job(pid, company, props):
        """Put a note's change on the import's current job. A past role at the same company stays as it was."""
        changes = {}
        if props.get("ended_by"):
            changes = {"ended_on": props.get("ended_on"), "ended_by": props["ended_by"], "current": False}
        if props.get("title_note"):
            changes.update({k: props[k] for k in ("title", "title_note", "title_log") if k in props})
            graph.query("MATCH (:Person {id: $id})-[r:WORKED_AT {current: true}]->(:Company {id: $c}) WHERE r.title_note IS NULL"
                        " SET r.prev_title = coalesce(r.title, '')", {"id": pid, "c": company})
        graph.query("MATCH (:Person {id: $id})-[r:WORKED_AT {current: true}]->(:Company {id: $c}) SET r += $changes",
                    {"id": pid, "c": company, "changes": changes})

    imported = {}
    # The import's own links first, then ended jobs, then current ones: "moved away and came back" must end with a current job.
    for l in sorted(snap["links"], key=lambda l: (bool(l["props"].get("note")), bool(l["props"].get("current")))):
        kind = name(l["type"])
        a, b = person_id(l["from_label"], l["from"]), person_id(l["to_label"], l["to"])
        if a == b:
            continue
        ends = f"(a:{name(l['from_label'])} {{id: $a}})", f"(b:{name(l['to_label'])} {{id: $b}})"
        ids = {"a": a, "b": b}
        props = l["props"]
        if kind == "WORKED_AT" and (not props.get("note") or props.get("current") or props.get("ended_by")):
            # A job the import may have too, perhaps spelled another way, or ended by now.
            now = same_job_now(a, b)
            for company in now:
                change_job(a, company, props)
            if now or not props.get("note"):
                continue
        key = (kind, a, b)
        if kind in ONE_PER_PAIR and key not in imported:
            arrow = f"-[r:{kind}]-" if kind == "KNOWS" else f"-[r:{kind}]->"
            imported[key] = graph.ro_query(f"MATCH {ends[0]}{arrow}{ends[1]} RETURN count(r)", ids).result_set[0][0] > 0
        if imported.get(key):
            continue
        graph.query(f"MATCH {ends[0]}, {ends[1]} CREATE (a)-[r:{kind}]->(b) SET r = $props", {**ids, "props": props})
        if kind == "WORKED_AT" and props.get("current"):
            graph.query("MATCH (a {id: $a}) SET a.company = $company", {"a": a, "company": l["from"].get("company", "")})
    graph.query("MATCH (c:Company {source: 'agent'}) WHERE NOT (c)--() DELETE c")    # a note's spelling of a job the import has

    started = {(person_id("Person", l["from"]), l["to"].get("name")) for l in snap["links"] if l["type"] == "WORKED_AT" and l["props"].get("note")}
    notes = {n["props"]["id"]: n["props"] for n in snap["nodes"] if n["label"] == "Note"}
    for a in snap["about"]:
        pid, note, facts = person_id(a["label"], a["props"]), notes.get(a["note"]), imported_facts.get(person_id(a["label"], a["props"]))
        if not note or not facts:
            continue
        undo = {f"prev_{field}": value or "" for field, value in zip(FIELDS, facts)
                if note.get(f"set_{field}") and not note.get(f"prev_{field}_note")}
        if "prev_location" in undo:
            undo.update(prev_cities=facts[4], prev_city_names=facts[5])
        if "prev_company" in note and (pid, note["prev_company"]) not in started:
            undo["prev_company"] = facts[3] or ""
        if undo:
            graph.query("MATCH (n:Note {id: $id}) SET n += $undo", {"id": a["note"], "undo": undo})
        if note.get("ended_only"):
            sync_company(graph, pid)
    return sum(n["label"] == "Note" for n in snap["nodes"]), len(merged)
