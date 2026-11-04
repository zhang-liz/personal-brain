"""Who changed jobs since the last load: the news in a new LinkedIn export."""

from datetime import date

from personal_brain.companies import same_company

PAGE = 500  # people per page, because FalkorDB Cloud caps a result at 10,000 rows
SHOWN = 20  # lines in the report


def read_jobs(graph):
    """Each person's current jobs, and old ones a note ended: {person id: [job]}."""
    top = graph.ro_query("MATCH (p:Person) RETURN max(id(p))").result_set[0][0]
    jobs = {}
    for lo in range(0, (top or 0) + 1, PAGE):
        for pid, name, cid, company, current, note, noticed_on, left in graph.ro_query(
                "MATCH (p:Person)-[w:WORKED_AT]->(c:Company) WHERE id(p) >= $lo AND id(p) < $hi"
                " AND (w.current = true OR w.ended_by IS NOT NULL)"
                " RETURN p.id, p.name, c.id, c.name, w.current, w.note, w.noticed_on, w.left",
                {"lo": lo, "hi": lo + PAGE}).result_set:
            jobs.setdefault(pid, []).append({"person": name, "id": cid, "company": company, "current": current,
                                             "note": note, "noticed_on": noticed_on, "left": left})
    return jobs


def mark(graph, old):
    """Mark each job that is new since the old graph, and keep older marks. Returns the news as text lines."""
    today = date.today().isoformat()
    news, marks = [], []
    for pid, jobs in read_jobs(graph).items():
        before = old.get(pid)
        if not before:    # new to the network: not a job change
            continue
        for job in jobs:
            same = [b for b in before if same_company(b["id"], job["id"])]
            carried = next((b for b in same if b["noticed_on"]), None)
            if carried:
                marks.append({"p": pid, "c": job["id"], "on": carried["noticed_on"], "left": carried["left"]})
            elif not same:
                # Only what the import said can be left. A job from a note is newer than the export.
                left = [b["company"] for b in before if b["current"] and not b["note"]
                        and not any(same_company(b["id"], j["id"]) for j in jobs)]
                marks.append({"p": pid, "c": job["id"], "on": today, "left": ", ".join(left) or None})
                news.append(f"{job['person']}: " + (f"{', '.join(left)} -> " if left else "") + job["company"])
    for i in range(0, len(marks), PAGE):
        graph.query("UNWIND $rows AS r MATCH (:Person {id: r.p})-[w:WORKED_AT {current: true}]->(:Company {id: r.c})"
                    " SET w.noticed_on = r.on, w.left = r.left", {"rows": marks[i:i + PAGE]})
    return news


def report(news):
    if not news:
        return
    print(f"{len(news)} {'person' if len(news) == 1 else 'people'} changed jobs since your last load:")
    for line in news[:SHOWN]:
        print("  " + "".join(c for c in line if c.isprintable()))
    if len(news) > SHOWN:
        print(f"  and {len(news) - SHOWN} more. Ask your agent who changed jobs.")
