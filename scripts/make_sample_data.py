"""Write sample_data/: a made-up network in the same file formats as the real inputs.

Every person, startup and investor here is invented. Big tech companies and
universities are real names, so the example questions have something to find.
The output is committed; run this again only to change the sample.

    python3 scripts/make_sample_data.py
"""

import csv
import json
import random
import unicodedata
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "sample_data"
rnd = random.Random(7)

FIRST = ["Priya", "Jonas", "Tomás", "Amara", "Kenji", "Lucía", "Mateo", "Ifeoma", "Noor", "Elif", "Rafael", "Hana",
         "Diego", "Ingrid", "Kwame", "Mei", "Arjun", "Zofia", "Yusuf", "Camille", "Oskar", "Leilani", "Ravi", "Freya",
         "Anouk", "Thabo", "Valentina", "Hiro", "Saoirse", "Dmitri", "Ayesha", "Nikolai", "Imani", "Bruno", "Linnea",
         "Farah", "Emeka", "Rosa", "Tariq", "Greta", "Sven", "Adaeze", "Paolo", "Mariam", "Teodor", "Esme", "Kai",
         "Beatriz", "Ola", "Sanjay", "Maren", "Rahel", "Joon", "Celeste", "Viktor", "Nadia", "Idris", "Wren", "Lior"]
LAST = ["Raman", "Weber", "Ortega", "Okafor", "Tanaka", "Ferreira", "Novak", "Haddad", "Lindqvist", "Mensah", "Kaur",
        "Moreau", "Sato", "Achebe", "Kowalski", "Nair", "Castillo", "Yilmaz", "Brennan", "Abara", "Holm", "Petrov",
        "Delgado", "Bianchi", "Osei", "Varga", "Iyer", "Dubois", "Kim", "Nakamura", "Eze", "Fischer", "Rossi", "Ali",
        "Jansen", "Mwangi", "Silva", "Popescu", "Larsen", "Quispe", "Bauer", "Adeyemi", "Morales", "Park", "Horvat"]

# Invented startups: (name, team size, funded, topic words for their people)
STARTUPS = [("Kestrel Graph", "11-50", True, "knowledge graph"), ("Paperlantern AI", "51-100", True, "AI agents"),
            ("Quietloop", "11-50", True, "agent memory"), ("Brightfjord", "101-250", True, "RAG and semantic search"),
            ("Saltmarsh Labs", "1-10", False, "LLMs"), ("Cobaltfern", "51-100", True, "AI agents"),
            ("Thimble AI", "11-50", True, "agent memory"), ("Lanternfish Data", "251-500", True, "embeddings"),
            ("Pebblestack", "1-10", False, "developer tools"), ("Cinderpath", "11-50", True, "generative AI"),
            ("Marigold Robotics", "51-100", True, "robotics"), ("Fernhollow", "1-10", False, "community"),
            ("Orbitwise", "11-50", True, "AI agents"), ("Gullwing Analytics", "51-100", False, "machine learning"),
            ("Mossgate", "1-10", False, "knowledge graph"), ("Tinderbox Labs", "11-50", True, "LLMs"),
            ("Heronfield", "101-250", True, "semantic search"), ("Quillmark", "1-10", False, "podcast"),
            ("Larkspur Systems", "51-100", True, "agent memory"), ("Bramblewood AI", "11-50", False, "AI agents")]
BIG = ["Google", "Meta", "Microsoft", "NVIDIA", "Amazon", "Salesforce"]
SCHOOLS = ["Stanford University", "University of Waterloo", "UC Berkeley", "Georgia Tech", "University of Toronto",
           "IIT Bombay", "Technical University of Munich", "University of São Paulo", "ETH Zürich", "Carnegie Mellon University",
           "University of Michigan", "KTH Royal Institute of Technology", "University of Lagos", "Seoul National University",
           "University of Cape Town", "McGill University", "Tsinghua University", "University of Edinburgh",
           "University of Washington", "UNAM", "Delft University of Technology", "University of Nairobi"]
INVESTORS = ["Harborlight Ventures", "Granite Owl Capital", "Saffron Seed", "Tallgrass Partners", "Bluewater Fund",
             "Copperleaf Ventures", "Juniper Angels", "Riverstone Seed"]
CITIES = ["San Francisco, California, United States", "San Francisco Bay Area", "New York, New York, United States",
          "Berlin, Germany", "London, England, United Kingdom", "Toronto, Ontario, Canada", "Seattle, Washington, United States"]
ROLES = [("Software Engineer", ["Python", "Kubernetes"]), ("ML Engineer", ["Machine Learning", "Python", "LLMs"]),
         ("Founder & CEO", ["Leadership", "Fundraising"]), ("Co-founder & CTO", ["Python", "Rust", "Knowledge Graphs"]),
         ("Developer Relations Lead", ["Public Speaking", "Community Building"]), ("Head of Community & Events", ["Event Planning", "Community Building"]),
         ("Product Manager", ["Product Management", "LLMs"]), ("Partnerships Manager", ["Business Development", "Partnerships"]),
         ("Research Scientist", ["Machine Learning", "PyTorch"]), ("Principal at a VC firm", ["Venture Capital", "Due Diligence"]),
         ("Podcast host", ["Content Creation", "Public Speaking"]), ("Staff Engineer", ["Go", "Distributed Systems", "Rust"])]
EVENT_DAYS = {"2026-03-12": "Agent Builders Night", "2026-05-21": "Graph & RAG Meetup", "2026-07-09": "AI Engineer Summit",
              "2026-09-17": "Memory for Agents Workshop", "2026-10-08": "Tech Week: Agent Memory Night"}

used, people = set(), []
while len(people) < 240:
    name = f"{rnd.choice(FIRST)} {rnd.choice(LAST)}"
    if name in used:
        continue
    used.add(name)
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    # "pb-sample-" so no link can open a real person's profile.
    slug = "pb-sample-" + plain.lower().replace(" ", "-") + f"-{rnd.randint(100, 999)}"
    people.append({"name": name, "slug": slug, "url": f"https://www.linkedin.com/in/{slug}"})
# Two people with the same name, so the agent has to ask which one you mean.
twin = dict(people[0], slug=people[0]["slug"][:-3] + "777")
twin["url"] = f"https://www.linkedin.com/in/{twin['slug']}"
people.append(twin)

event_days = list(EVENT_DAYS)
for i, p in enumerate(people):
    p["role"], p["skills"] = rnd.choice(ROLES)
    if i % 6 == 0:
        startup = rnd.choice(STARTUPS)
        p["company"], p["topic"] = startup[0], startup[3]
    elif i % 6 == 1:
        p["company"], p["topic"] = rnd.choice(BIG), "LLMs"
    else:
        startup = rnd.choice(STARTUPS)
        p["company"], p["topic"] = (startup[0], startup[3]) if rnd.random() < 0.7 else (rnd.choice(BIG), "machine learning")
    # About 1 in 4 connections were added on an event day (5+ on one day reads as an event).
    if rnd.random() < 0.25:
        p["connected"] = date.fromisoformat(rnd.choice(event_days))
    else:
        p["connected"] = date(2019, 1, 1) + timedelta(days=rnd.randint(0, 2800))
    p["city"] = rnd.choice(CITIES)
    p["school"] = rnd.choice(SCHOOLS)

linkedin = OUT / "linkedin"
linkedin.mkdir(parents=True, exist_ok=True)
with open(linkedin / "Connections.csv", "w", newline="", encoding="utf-8") as f:
    f.write("Notes:\n\"Made-up sample data. Every person here is invented.\"\n\n")
    w = csv.writer(f)
    w.writerow(["First Name", "Last Name", "URL", "Email Address", "Company", "Position", "Connected On"])
    for p in people:
        first, last = p["name"].split(" ", 1)
        w.writerow([first, last, p["url"], "", p["company"], p["role"], p["connected"].strftime("%d %b %Y")])
with open(linkedin / "Profile.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["First Name", "Last Name", "Headline", "Geo Location"])
    w.writerow(["Sample", "User", "Developer Relations · AI agents and knowledge graphs", "San Francisco Bay Area"])
with open(linkedin / "Positions.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["Company Name", "Title"])
    w.writerow(["Kestrel Graph", "Developer Relations Lead"])
    w.writerow(["Google", "Developer Advocate"])
with open(linkedin / "Education.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["School Name"])
    w.writerow(["University of Waterloo"])
me_url = "https://www.linkedin.com/in/pb-sample-me"
with open(linkedin / "messages.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["CONVERSATION ID", "SENDER PROFILE URL", "RECIPIENT PROFILE URLS"])
    for i, p in enumerate(rnd.sample(people, 60)):
        for _ in range(rnd.randint(1, 12)):
            w.writerow([f"dm-{i}", rnd.choice([me_url, p["url"]]), me_url if rnd.random() < 0.5 else p["url"]])
    # A few group chats: everyone in one knows each other.
    for g in range(4):
        members = rnd.sample(people, 4)
        w.writerow([f"group-{g}", me_url, ",".join(m["url"] for m in members)])
        for m in members:
            w.writerow([f"group-{g}", m["url"], ",".join([me_url] + [x["url"] for x in members if x is not m])])


def span(start_year, years):
    end = start_year + years
    period = {"startDate": {"year": start_year, "month": rnd.randint(1, 12)}}
    if end < 2026:
        period["endDate"] = {"year": end, "month": rnd.randint(1, 12)}
    return period


# Profiles: job histories (for "who worked together"), schools, skills, headlines.
items = []
for p in people:
    start = rnd.randint(2016, 2022)
    past = rnd.choice([c for c in BIG + [s[0] for s in STARTUPS] if c != p["company"]])
    positions = [{"company": {"name": p["company"]}, "title": p["role"], "description": f"Working on {p['topic']}.",
                  "timePeriod": {"startDate": {"year": start + 2, "month": rnd.randint(1, 12)}}},
                 {"company": {"name": past}, "title": "Engineer", "description": "Built backend services in Python.",
                  "timePeriod": span(start - 1, 3)}]
    school_start = start - rnd.randint(4, 12)
    items.append({"inputUrl": p["url"], "headline": f"{p['role']} at {p['company']} · {p['topic']}",
                  "summary": f"I work on {p['topic']}.", "geoLocationName": p["city"],
                  "skills": [{"name": s} for s in p["skills"]], "positions": positions,
                  "educations": [{"schoolName": p["school"], "degreeName": "BSc", "fieldOfStudy": "Computer Science",
                                  "timePeriod": {"startDate": {"year": school_start}, "endDate": {"year": school_start + 4}}}]})
enriched = OUT / "enriched"
enriched.mkdir(parents=True, exist_ok=True)
(enriched / "batch_sample.json").write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1))

companies = []
for name, team, funded, _ in STARTUPS:
    backers = rnd.sample(INVESTORS, rnd.randint(2, 4))
    rounds = [{"round": "Series A" if funded else "Pre Seed", "investors": backers, "leadInvestors": backers[:1]}]
    companies.append({"name": name, "searchTerm": name, "fundingRounds": rounds, "employeeCount": team,
                      "ipoStatus": "private", "operatingStatus": "active", "numInvestors": len(backers),
                      "industries": ["Artificial Intelligence", "Software"]})
(enriched / "companies_sample.json").write_text(json.dumps({"items": companies}, ensure_ascii=False, indent=1))

# Upcoming events in 2027, hosted by people in the sample or named after its startups.
hosts = [p for p in people if "Community" in p["role"] or "Relations" in p["role"]][:6]
events = []
for i, (title, topic) in enumerate([("Agent Memory Meetup", "agent memory and AI agents"),
                                    ("Knowledge Graph Night x Kestrel Graph", "knowledge graph and GraphRAG"),
                                    ("RAG Builders Breakfast", "RAG and semantic search"),
                                    ("AI Agents Hackathon", "AI agents"),
                                    ("DevRel Coffee", "developer relations"),
                                    ("LLM Evals Workshop x Paperlantern AI", "LLMs and evals")]):
    host = hosts[i % len(hosts)]
    events.append({"eventId": f"sample-{i}", "name": title, "description": f"An evening about {topic}.",
                   "startAt": f"2027-0{i + 1}-1{i}T01:30:00Z", "timezone": "America/Los_Angeles",
                   "hosts": [{"name": host["name"], "bio": f"{host['role']} @ {host['company']}",
                              "socialHandles": {"linkedin": host["url"]}}],
                   "location": {"name": "Community space", "city": "San Francisco"}, "guestCount": rnd.randint(40, 300),
                   "eventUrl": "", "categoryNames": ["AI", "Tech"]})
(enriched / "events_sample.json").write_text(json.dumps({"items": events}, ensure_ascii=False, indent=1))

(OUT / "my_brain.json").write_text(json.dumps({"events": EVENT_DAYS, "skip_companies": []}, indent=1))
print(f"wrote {OUT}: {len(people)} people, {len(STARTUPS)} startups, {len(events)} upcoming events")
