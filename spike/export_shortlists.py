"""Ranked shortlists from the FalkorDB graph: co-hosts & sponsors, podcast guests, places to speak.

Each list is a few Cypher queries plus a transparent score, so every row says
why it ranks where it does. Writes data/viewer/shortlists.json (for
shortlists.html) and one CSV per list in data/viewer/shortlists/.

    .venv/bin/python spike/export_shortlists.py --cloud
"""

import csv
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from falkordb import FalkorDB

PROJECT = Path(__file__).resolve().parent.parent
OUT = PROJECT / "data" / "viewer"
SETTINGS = PROJECT / "my_brain.json"
TOP = 60

SENIOR_RE = re.compile(r"\b(founder|co-?founder|ceo|cto|chief|head of|vp|vice president|director|principal"
                       r"|staff|distinguished|partner|professor|research scientist|lead)\b", re.I)
ORG_RE = re.compile(r"\b(summit|conference|conf|meetup|meetups|club|community|hackathon|hackathons|ted"
                    r"|developer groups?|gdg|circle|society|festival|collective|guild|events|forum"
                    r"|week|hacks)\b", re.I)
NOT_ORG_RE = re.compile(r"\b(software|university|college|student|students|career|design|school)\b", re.I)
ORGANIZER_RE = re.compile(r"\b(organi[sz]er|co-?organi[sz]er|curator|chapter lead|host|community lead"
                          r"|head of community|community manager|events? (lead|manager|director))\b", re.I)
TALK_RE = re.compile(r"\b(talks?|panel|meetup|lightning|demo|tech talk|summit|conference|conf|workshop"
                     r"|forum|unconference|showcase|fireside|seminar|lecture|keynote|speakers?)\b", re.I)
SOCIAL_RE = re.compile(r"\b(dinner|happy hour|party|drinks|brunch|mixer|golf|run club|yoga|pop-up"
                       r"|retreat|picnic|karaoke)\b", re.I)
EVENT_TOPIC_POINTS = {"Agent Memory": 3, "Knowledge Graphs": 3, "RAG & Search": 2, "AI Agents": 1.5,
                      "Developer Relations": 1, "LLMs & GenAI": 0.5}
# Big tech hosts many events but rarely co-hosts with a startup: listed, but ranked lower.
BIG_TECH = {"google", "meta", "facebook", "microsoft", "apple", "nvidia", "amazon", "amazon web services aws",
            "ibm", "salesforce", "oracle", "github", "intel", "linkedin"}
GUEST_TOPIC_POINTS = {"Agent Memory": 3, "Knowledge Graphs": 2, "AI Agents": 1, "RAG & Search": 1,
                      "LLMs & GenAI": 0.5}


def connect():
    if "--cloud" in sys.argv:
        env = (PROJECT / ".env").read_text().splitlines()
        url = next(l.split("=", 1)[1].strip() for l in env if l.startswith("FALKORDB_URL="))
        return FalkorDB.from_url(url), "FalkorDB Cloud"
    return FalkorDB(host="localhost", port=6379), "local FalkorDB"


def competitors():
    """Companies you would not co-host with (my_brain.json "competitors"), normalized."""
    s = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    return {re.sub(r"[^a-z0-9]+", " ", c.lower()).strip() for c in s.get("competitors", [])}


def main():
    db, where = connect()
    g = db.select_graph("network")
    q = lambda cypher, **params: g.query(cypher, params).result_set
    today = date.today()
    since = f"{today.year - 1}-01-01"
    rivals = competitors()

    # ---------- facts about people ----------
    people = {}
    for pid, name, headline, title, company, url, messages, lead in q(
            "MATCH (:Me)-[:CONNECTED]->(p:Person) RETURN p.id, p.name, p.headline, p.title, p.company,"
            " p.url, p.messages, p.event_contact"):
        people[pid] = {"id": pid, "name": name, "headline": headline or "", "title": title or "",
                       "company": company or "", "url": url or "", "messages": messages or 0,
                       "lead": bool(lead), "topics": set(), "city": "", "employers": [], "hosts": []}
    for pid, topic in q("MATCH (p:Person)-[:INTO]->(t:Topic) RETURN p.id, t.name"):
        if pid in people:
            people[pid]["topics"].add(topic)
    for pid, city in q("MATCH (p:Person)-[:LIVES_IN]->(c:City) RETURN p.id, c.name"):
        if pid in people:
            people[pid]["city"] = city
    for pid, cid, cname, wf, title in q(
            "MATCH (p:Person)-[w:WORKED_AT {current:true}]->(c:Company)"
            " RETURN p.id, c.id, c.name, c.well_funded, w.title"):
        if pid in people:
            people[pid]["employers"].append({"id": cid, "name": cname, "well_funded": bool(wf),
                                             "title": title or ""})
    graph_years = dict(q("MATCH (p:Person)-[x:HAS_EXPERIENCE]->(:Skill {id:'graph databases'})"
                         " RETURN p.id, x.years"))
    met_recently = defaultdict(int)
    for pid, in q("MATCH (p:Person)-[:MET_AT]->(e:Event) WHERE e.date >= $since RETURN p.id", since=since):
        met_recently[pid] += 1
    mine = {cid for cid, in q("MATCH (:Me)-[:WORKED_AT]->(c:Company) RETURN c.id")}

    # ---------- upcoming events and how they link to you ----------
    events = {}
    for eid, name, day, time, url, guests, hosts, venue, blurb in q(
            "MATCH (e:Event {upcoming:true}) RETURN e.id, e.name, e.date, e.time, e.url, e.guests,"
            " e.hosts, e.venue, e.blurb"):
        events[eid] = {"id": eid, "name": name, "date": day, "time": time, "url": url, "guests": guests or 0,
                       "hosts": hosts or "", "venue": venue or "", "blurb": blurb or "", "topics": [],
                       "host_people": [], "host_companies": []}
    for eid, topic in q("MATCH (e:Event {upcoming:true})-[:ABOUT]->(t:Topic) RETURN e.id, t.name"):
        events[eid]["topics"].append(topic)
    for eid, pid in q("MATCH (e:Event {upcoming:true})-[:HOSTED_BY]->(p:Person) RETURN e.id, p.id"):
        events[eid]["host_people"].append(pid)
        if pid in people:
            people[pid]["hosts"].append(eid)
    hosted_by_company = defaultdict(list)
    for eid, cid, cname, via in q("MATCH (e:Event {upcoming:true})-[h:HOSTED_BY]->(c:Company)"
                                  " RETURN e.id, c.id, c.name, h.via"):
        events[eid]["host_companies"].append({"id": cid, "name": cname, "via": via})
        hosted_by_company[cid].append(eid)

    staff = defaultdict(list)   # company id -> your connections working there now
    for p in people.values():
        for emp in p["employers"]:
            staff[emp["id"]].append(p)

    def best_contact(company_id):
        """The event/community lead you message most there, else anyone you message most."""
        ps = sorted(staff[company_id], key=lambda p: (not p["lead"], -p["messages"]))
        return ps[0] if ps else None

    def short_date(day):
        return date.fromisoformat(day).strftime("%b %d")

    # ---------- 1. co-hosts & sponsors ----------
    companies = {}
    for cid, name, wf, funding, employees, industries in q(
            "MATCH (c:Company) RETURN c.id, c.name, c.well_funded, c.funding, c.employees, c.industries"):
        if cid in staff and cid not in mine and cid not in rivals:
            companies[cid] = (name, bool(wf), funding or "", employees or "", industries or "")
    cohosts = []
    for cid, (name, wf, funding, employees, industries) in companies.items():
        ps = staff[cid]
        leads = [p for p in ps if p["lead"]]
        hosted = sorted(set(hosted_by_company[cid]), key=lambda e: events[e]["date"])
        met = sum(1 for p in ps if met_recently[p["id"]])
        ai = sum(1 for p in ps if p["topics"] & {"AI Agents", "Agent Memory", "Knowledge Graphs", "RAG & Search"})
        big = cid in BIG_TECH
        score = (3 * min(len(hosted), 3) + 3 * wf + 2 * min(len(leads), 2) + 0.5 * min(len(ps), 5)
                 + min(met, 3) + (1 if ai >= 2 else 0) - 4 * big)
        if not (hosted or wf or leads or met):
            continue
        why = ["big tech"] if big else []
        if hosted:
            why.append(f"hosts {len(hosted)} upcoming event{'s' * (len(hosted) > 1)}")
        if wf:
            why.append(f"well-funded ({funding})" if funding else "well-funded")
        if leads:
            why.append(f"{len(leads)} event/community lead{'s' * (len(leads) > 1)} you know")
        why.append(f"{len(ps)} connection{'s' * (len(ps) > 1)} there")
        if met:
            why.append(f"met {met} of them at events since {today.year - 1}")
        contact = best_contact(cid)
        cohosts.append({
            "score": round(score, 1), "company": name, "why": " · ".join(why),
            "contact": contact["name"], "contact_title": contact["headline"][:90],
            "contact_url": contact["url"], "contact_messages": contact["messages"],
            "events": "; ".join(f"{short_date(events[e]['date'])} {events[e]['name']}" for e in hosted[:3]),
            "funding": funding, "people": employees})
    cohosts.sort(key=lambda r: -r["score"])

    # ---------- 2. podcast guests ----------
    guests = []
    for p in people.values():
        topic_pts = sum(GUEST_TOPIC_POINTS.get(t, 0) for t in p["topics"])
        if not p["topics"] & {"Agent Memory", "Knowledge Graphs", "AI Agents", "RAG & Search"}:
            continue
        if any(e["id"] in mine for e in p["employers"]):   # your own coworkers
            continue
        role = f"{p['title']} {p['headline']}"
        senior = bool(SENIOR_RE.search(role))
        funded = next((e["name"] for e in p["employers"] if e["well_funded"]), "")
        yrs = graph_years.get(p["id"], 0)
        score = (topic_pts + 2 * senior + ("Podcast & Media" in p["topics"]) + (p["city"] == "SF Bay Area")
                 + bool(p["hosts"]) + (p["messages"] > 0) + bool(funded) + (yrs >= 1))
        why = [", ".join(t for t in GUEST_TOPIC_POINTS if t in p["topics"])]
        if senior:
            why.append("senior")
        if yrs >= 1:
            why.append(f"{yrs:g} yrs graph databases")
        if "Podcast & Media" in p["topics"]:
            why.append("already does podcasts/media")
        if p["hosts"]:
            why.append(f"hosts {events[p['hosts'][0]]['name'][:40]}")
        if funded:
            why.append(f"at {funded} (well-funded)")
        if p["city"] == "SF Bay Area":
            why.append("in SF")
        guests.append({"score": round(score, 1), "name": p["name"], "headline": p["headline"][:110],
                       "company": p["employers"][0]["name"] if p["employers"] else p["company"],
                       "city": p["city"], "why": " · ".join(why),
                       "warmth": f"{p['messages']} messages" if p["messages"] else "connected, no messages yet",
                       "url": p["url"]})
    guests.sort(key=lambda r: -r["score"])

    # ---------- 3a. upcoming events to speak at ----------
    speak = []
    for e in events.values():
        topic_pts = sum(EVENT_TOPIC_POINTS.get(t, 0) for t in e["topics"])
        host_people = [people[pid] for pid in e["host_people"] if pid in people]
        host_cos = [c for c in e["host_companies"] if c["id"] in staff and c["id"] not in mine]
        rival = any(c["id"] in rivals for c in e["host_companies"])
        days = (date.fromisoformat(e["date"]) - today).days
        talk, social = bool(TALK_RE.search(e["name"])), bool(SOCIAL_RE.search(e["name"]))
        score = (topic_pts + 3 * bool(host_people) + 2 * bool(host_cos)
                 + any(best_contact(c["id"])["lead"] for c in host_cos)
                 + talk - social + (e["guests"] >= 150) - (days < 10) - 4 * rival)
        if not (topic_pts or host_people or host_cos) or rival or days < 3:   # under 3 days: too late
            continue
        if host_people:
            ask = f"ask {host_people[0]['name']} (host, your connection)"
        elif host_cos:
            c = max(host_cos, key=lambda c: best_contact(c["id"])["lead"])
            ask = f"ask {best_contact(c['id'])['name']} at {c['name']} ({c['via']})"
        else:
            ask = "cold: message the host on Luma"
        why = [", ".join(t for t in EVENT_TOPIC_POINTS if t in e["topics"]) or "AI"]
        if talk:
            why.append("has talks/demos")
        if social:
            why.append("social, few talk slots")
        if e["guests"] >= 150:
            why.append(f"{e['guests']} going")
        if days < 10:
            why.append("soon: ask now or skip")
        speak.append({"score": round(score, 1), "date": e["date"], "event": e["name"], "why": " · ".join(why),
                      "ask": ask, "hosts": e["hosts"][:120], "guests": e["guests"], "url": e["url"]})
    speak.sort(key=lambda r: (-r["score"], r["date"]))

    # ---------- 3b. organizers you know (recurring stages) ----------
    organizers = []
    for p in people.values():
        orgs = [e for e in p["employers"] if ORG_RE.search(e["name"]) and not NOT_ORG_RE.search(e["name"])]
        role = " ".join([p["title"], p["headline"]] + [e["title"] for e in p["employers"]])
        organizing = bool(ORGANIZER_RE.search(role))
        if not (orgs or (organizing and p["lead"])):
            continue
        ai = bool(p["topics"] & {"AI Agents", "Agent Memory", "Knowledge Graphs", "RAG & Search", "LLMs & GenAI"})
        score = (2 * bool(orgs) + 2 * organizing + 2 * bool(p["hosts"]) + ai
                 + (p["city"] == "SF Bay Area") + (p["messages"] > 0))
        why = []
        if orgs:
            why.append(f"runs/works at {orgs[0]['name']}")
        if organizing:
            why.append("organizer role")
        if p["hosts"]:
            why.append(f"hosts {events[p['hosts'][0]]['name'][:40]} ({short_date(events[p['hosts'][0]]['date'])})")
        if ai:
            why.append("into AI")
        if p["city"] == "SF Bay Area":
            why.append("in SF")
        if p["messages"]:
            why.append(f"{p['messages']} messages")
        organizers.append({"score": score, "name": p["name"], "headline": p["headline"][:110],
                           "org": (orgs[0]["name"] if orgs else p["employers"][0]["name"] if p["employers"]
                                   else p["company"]),
                           "city": p["city"], "why": " · ".join(why), "url": p["url"]})
    organizers.sort(key=lambda r: -r["score"])

    lists = {
        "cohosts": {"title": "Co-hosts & sponsors", "rows": cohosts[:TOP], "total": len(cohosts),
                    "note": "Companies where you know people, ranked by: hosts upcoming AI events (3 each, max 3) "
                            "+ well-funded (3) + event/community leads you know (2 each, max 2) + connections "
                            "(0.5 each, max 5) + people met at events (1 each, max 3) + AI team (1)."},
        "guests": {"title": "Podcast guests", "rows": guests[:TOP], "total": len(guests),
                   "note": "Connections into agents, memory, graphs or RAG, ranked by topic (Agent Memory 3, "
                           "Knowledge Graphs 2, AI Agents 1, RAG 1) + senior (2) + 1 each for: graph DB years, "
                           "does podcasts, hosts events, at a well-funded startup, in SF, you have messaged."},
        "speak": {"title": "Upcoming events to speak at", "rows": speak[:TOP], "total": len(speak),
                  "note": "Upcoming SF Luma events, ranked by topic (Agent Memory 3, Knowledge Graphs 3, RAG 2, "
                          "AI Agents 1.5) + host is your connection (3) + you know people at the host company (2, "
                          "+1 if an event lead) + talks (1) − social only (1) + 150+ going (1) − under 10 days (1)."},
        "organizers": {"title": "Organizers you know", "rows": organizers[:TOP], "total": len(organizers),
                       "note": "People at conferences, meetups and communities, or with organizer roles. Recurring "
                               "stages: ask them for a slot at the next one."},
    }
    payload = {"generated": today.isoformat(), "source": where, "lists": lists}
    (OUT / "shortlists.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    (OUT / "shortlists").mkdir(exist_ok=True)
    for key, lst in lists.items():
        rows = lst["rows"]
        if rows:
            with open(OUT / "shortlists" / f"{key}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["rank"] + list(rows[0]))
                w.writeheader()
                w.writerows({"rank": i + 1, **r} for i, r in enumerate(rows))
        print(f"{key:10s} {lst['total']:4d} candidates, top: "
              + " | ".join(r.get("name") or r.get("company") or r.get("event") for r in rows[:5]))
    print(f"wrote {OUT / 'shortlists.json'}")


if __name__ == "__main__":
    main()
