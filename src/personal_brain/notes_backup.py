"""Keep your notes through a rebuild.

A rebuild deletes the graph. Before that, snapshot() reads each note and what it
added. They wait in data/notes-backup.json, and restore() puts them back in the
new graph. If a rebuild stops halfway, the next one finds them in that file.
"""

import json

from personal_brain.companies import same_company
from personal_brain.paths import HOME

BACKUP = HOME / "data" / "notes-backup.json"
ONE_PER_PAIR = {"WORKED_AT", "INTO", "ATTENDED", "KNOWS"}   # when the import already has this link, the note adds no second one


def snapshot(graph):
    """Every note, each node a note made, each link a note made or ended, and the nodes on the other end."""
    return {
        "nodes": [{"label": l, "props": p} for l, p in graph.ro_query(
            "MATCH (x) WHERE x:Note OR x.source = 'agent' RETURN labels(x)[0], properties(x)").result_set],
        "about": [{"note": n, "label": l, "props": p} for n, l, p in graph.ro_query(
            "MATCH (n:Note)-[:ABOUT]->(x) RETURN n.id, labels(x)[0], properties(x)").result_set],
        "links": [{"from_label": al, "from": a, "type": t, "to_label": bl, "to": b, "props": r} for al, a, t, bl, b, r in graph.ro_query(
            "MATCH (a)-[r]->(b) WHERE r.note IS NOT NULL OR r.ended_by IS NOT NULL OR r.title_note IS NOT NULL"
            " RETURN labels(a)[0], properties(a), type(r), labels(b)[0], properties(b), properties(r)").result_set],
    }


def pending():
    """The snapshot a rebuild left behind when it stopped halfway, or None."""
    return json.loads(BACKUP.read_text(encoding="utf-8")) if BACKUP.exists() else None


def save(snap):
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    temp = BACKUP.with_suffix(".tmp")
    temp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    temp.replace(BACKUP)


def clear():
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

    imported = {}
    for l in snap["links"]:
        kind = name(l["type"])
        a, b = person_id(l["from_label"], l["from"]), person_id(l["to_label"], l["to"])
        if a == b:
            continue
        ends = f"(a:{name(l['from_label'])} {{id: $a}})", f"(b:{name(l['to_label'])} {{id: $b}})"
        ids = {"a": a, "b": b}
        props = l["props"]
        if not props.get("note"):
            # The import's own link, which a note ended or gave a title.
            changes = {k: props[k] for k in ("ended_on", "ended_by", "title", "title_note", "prev_title") if k in props}
            if "ended_by" in props:
                changes["current"] = False
            graph.query(f"MATCH {ends[0]}-[r:{kind}]->{ends[1]} SET r += $changes", {**ids, "changes": changes})
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
    return sum(n["label"] == "Note" for n in snap["nodes"]), len(merged)
