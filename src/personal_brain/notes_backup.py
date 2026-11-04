"""Keep your notes through a rebuild.

A rebuild deletes the graph. Before that, snapshot() reads each note and what it
added. They wait in data/notes-backup.json, and restore() puts them back in the
new graph. If a rebuild stops halfway, the next one finds them in that file.
"""

import json

from personal_brain.paths import HOME

BACKUP = HOME / "data" / "notes-backup.json"
ONE_PER_PAIR = {"WORKED_AT", "INTO", "ATTENDED"}   # when the import already has this link, the note adds no second one


def snapshot(graph):
    """Every note, each node a note made, each link a note made or ended, and the nodes on the other end."""
    return {
        "nodes": [{"label": l, "props": p} for l, p in graph.ro_query(
            "MATCH (x) WHERE x:Note OR x.source = 'agent' RETURN labels(x)[0], properties(x)").result_set],
        "about": [{"note": n, "label": l, "props": p} for n, l, p in graph.ro_query(
            "MATCH (n:Note)-[:ABOUT]->(x) RETURN n.id, labels(x)[0], properties(x)").result_set],
        "links": [{"from_label": al, "from": a, "type": t, "to_label": bl, "to": b, "props": r} for al, a, t, bl, b, r in graph.ro_query(
            "MATCH (a)-[r]->(b) WHERE r.note IS NOT NULL OR r.ended_by IS NOT NULL"
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


def restore(graph, snap):
    """Put the snapshot back. What the new import already has stays as the import says."""
    def add_node(label, props, note):
        if label != "Me":
            # A node missing from the new import now comes from the note, so forgetting the note removes it.
            graph.query(f"MERGE (n:{name(label)} {{id: $id}}) ON CREATE SET n += $props",
                        {"id": props["id"], "props": {"source": "agent", "note": note, **props}})

    for n in snap["nodes"]:
        add_node(n["label"], n["props"], n["props"].get("note") or n["props"]["id"])
    for a in snap["about"]:
        add_node(a["label"], a["props"], a["note"])
    for l in snap["links"]:
        note = l["props"].get("note") or l["props"].get("ended_by")
        add_node(l["from_label"], l["from"], note)
        add_node(l["to_label"], l["to"], note)

    for n in snap["nodes"]:
        if n["label"] == "Note":
            graph.query("MATCH (me:Me), (n:Note {id: $id}) CREATE (me)-[:WROTE]->(n)", {"id": n["props"]["id"]})
    for a in snap["about"]:
        graph.query(f"MATCH (n:Note {{id: $note}}), (x:{name(a['label'])} {{id: $id}}) CREATE (n)-[:ABOUT]->(x)",
                    {"note": a["note"], "id": a["props"]["id"]})

    imported = {}
    for l in snap["links"]:
        kind = name(l["type"])
        pair = f"(a:{name(l['from_label'])} {{id: $a}})-[r:{kind}]->(b:{name(l['to_label'])} {{id: $b}})"
        ids = {"a": l["from"]["id"], "b": l["to"]["id"]}
        props = l["props"]
        if not props.get("note"):
            # The import's own link, which a note ended.
            graph.query(f"MATCH {pair} SET r.current = false, r.ended_on = $on, r.ended_by = $by",
                        {**ids, "on": props.get("ended_on"), "by": props["ended_by"]})
            continue
        key = (kind, ids["a"], ids["b"])
        if kind in ONE_PER_PAIR and key not in imported:
            imported[key] = graph.ro_query(f"MATCH {pair} RETURN count(r)", ids).result_set[0][0] > 0
        if imported.get(key):
            continue
        graph.query(f"MATCH (a:{name(l['from_label'])} {{id: $a}}), (b:{name(l['to_label'])} {{id: $b}})"
                    f" CREATE (a)-[r:{kind}]->(b) SET r = $props", {**ids, "props": props})
        if kind == "WORKED_AT" and props.get("current"):
            graph.query("MATCH (a {id: $a}) SET a.company = $company", {"a": ids["a"], "company": l["from"].get("company", "")})
    return sum(n["label"] == "Note" for n in snap["nodes"])
