"""Load your LinkedIn network into FalkorDB (graph "network").

Builds the graph from your LinkedIn data export, plus enriched profiles and
company data in data/enriched/ when you have them. Real names, but no message
text (only message counts). A connections-only export works too.
Your own event labels and company skip list live in my_brain.json (not in git).

    personal-brain load --cloud            # FALKORDB_URL from the environment or .env
    personal-brain load                    # local Docker FalkorDB
    personal-brain load --sample --cloud   # the made-up network in sample_data/

A rebuild replaces the graph, and keeps your agent's notes and what they added.
--force drops them.
"""

import csv
import functools
import io
import itertools
import json
import re
import sys
import os
import unicodedata
import urllib.parse
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from falkordb import FalkorDB

from personal_brain import job_changes, notes_backup
from personal_brain.companies import norm
from personal_brain.paths import HOME, PACKAGE, cloud_url, open_url, printable, refresh_viewer
SAMPLE = "--sample" in sys.argv
SAMPLE_DIR = Path(os.environ.get("PB_SAMPLE_DIR", PACKAGE / "sample_data"))
ENRICHED = (SAMPLE_DIR if SAMPLE else HOME / "data") / "enriched"
SETTINGS = (SAMPLE_DIR if SAMPLE else HOME) / "my_brain.json"
FORGOTTEN = HOME / "data" / "forgotten.json"   # people you told the agent to forget


def graph_name():
    if "--graph" not in sys.argv:
        return os.environ.get("PB_GRAPH", "network")
    at = sys.argv.index("--graph") + 1
    if at == len(sys.argv) or sys.argv[at].startswith("--"):
        sys.exit("--graph needs a name: --graph <name>")
    return sys.argv[at]


GRAPH = graph_name()
NOW = date.today().year + date.today().month / 12
csv.field_size_limit(10**9)

GENERIC_COMPANIES = {"self employed", "freelance", "stealth", "stealth startup",
                     "confidential", "independent", "stealth mode", "freelancer"}



def forgotten_list(path=FORGOTTEN):
    """The ids and names on the keep-out list. Stops if it cannot read it: going on could bring someone back, or lose them from the list."""
    try:
        return set(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else set()
    except (ValueError, TypeError) as e:
        sys.exit(f"Cannot read {path}: {e}. It lists the people you forgot. Fix the file, then try again.")


def without_accents(text):
    """Only Latin accents go. Other scripts stay as they are, because their marks can change the name."""
    return "".join(unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode() or c
                   for c in unicodedata.normalize("NFKC", text or ""))


def fold(text):
    """Name as lowercase words without accents, for search: "Tomás Ortega" -> "tomas ortega"."""
    return norm(without_accents(text))


def load_settings():
    """Your own labels (my_brain.json, see my_brain.example.json). Optional."""
    try:
        s = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
        # Names for the days you added many connections ("YYYY-MM-DD": "Event name").
        events = {date.fromisoformat(d): name for d, name in s.get("events", {}).items()}
        # Companies that should never count as well-funded startups (e.g. subsidiaries).
        skip = {norm(c) for c in s.get("skip_companies", [])}
    except (ValueError, AttributeError, TypeError) as e:
        sys.exit(f"Cannot read {SETTINGS}: {e}. See my_brain.example.json.")
    return events, skip


KNOWN_EVENTS, NOT_STARTUPS = {}, set()    # from load_settings. Only load reads the file, so a typo in it cannot stop the MCP server.

TOPICS = {
    "AI Agents": [r"\bagents?\b", r"\bagentic\b", r"multi-agent"],
    "Agent Memory": [r"agent memory", r"memory layer", r"long[- ]term memory",
                     r"context engineering", r"\bmem0\b", r"\bletta\b", r"\bzep\b",
                     r"graphiti", r"cognee", r"memgpt"],
    "Knowledge Graphs": [r"knowledge graph", r"graph ?rag", r"graph database",
                         r"\bneo4j\b", r"falkordb", r"graph db"],
    "RAG & Search": [r"\brag\b", r"retrieval", r"vector (database|db|search)",
                     r"embeddings?", r"semantic search"],
    "LLMs & GenAI": [r"\bllms?\b", r"large language model", r"generative ai",
                     r"\bgen ?ai\b", r"foundation model"],
    "Developer Relations": [r"devrel", r"developer relations", r"developer advocat",
                            r"developer evangel", r"dev advocate"],
    "Community & Events": [r"\bcommunity\b", r"\bevents?\b", r"meetups?", r"hackathons?"],
    "Partnerships": [r"partnerships?", r"alliances", r"business development",
                     r"ecosystem"],
    "Investing": [r"venture capital", r"\bvc\b", r"investor", r"general partner",
                  r"angel invest"],
    "Founder": [r"\bfounder\b", r"co-founder", r"cofounder"],
    "Podcast & Media": [r"podcast", r"youtube", r"content creator", r"newsletter"],
}
TOPIC_RE = {t: re.compile("|".join(p), re.I) for t, p in TOPICS.items()}
# Years of hands-on experience per technology, from jobs whose title or
# description mention it (overlapping jobs are merged, not double counted).
TECH = {
    "LLMs": r"\bllms?\b|large language model|generative ai|\bgen ?ai\b",
    "Rust": r"\brust\b",
    "Go": r"\bgolang\b|\bgo (developer|engineer|programming|language|services)\b",
    "Kubernetes": r"kubernetes|\bk8s\b",
    "Machine Learning": r"machine learning|\bml\b",
    "Computer Vision": r"computer vision",
    "C++": r"c\+\+",
    "TypeScript": r"typescript",
    "Python": r"\bpython\b",
    "Robotics": r"robotic",
    "Graph Databases": r"graph database|knowledge graph|\bneo4j\b|falkordb",
}
TECH_RE = {t: re.compile(p, re.I) for t, p in TECH.items()}
# Roles to reach for event collaboration (from current title/headline).
EVENT_ROLE_RE = re.compile(r"\bevents?\b|community|partnerships?|devrel|developer relations"
                           r"|developer advocat|ecosystem|alliances", re.I)


def create_search_indexes(graph):
    """Full-text indexes on the folded names, so the agent can find people by name, typos included."""
    for label in ["Person", "Company", "Event"]:
        try:
            graph.query(f"CREATE FULLTEXT INDEX FOR (n:{label}) ON (n.search)")    # FalkorDB 6 refuses the older CALL db.idx.fulltext.createNodeIndex('{label}', 'search')
        except Exception as e:
            if "already indexed" not in str(e):
                raise
    try:
        graph.query("CREATE INDEX FOR (n:Note) ON (n.id)")
    except Exception as e:
        if "already" not in str(e):
            raise


def export_date(folder):
    """The day in a name like Complete_LinkedInDataExport_03-04-2026, where the month comes first. Else the day of its file."""
    found = re.search(r"(\d{2})-(\d{2})-(\d{4})", folder.name)
    if found:
        first, second, year = map(int, found.groups())
        for month, day in ((first, second), (second, first)):
            try:
                return date(year, month, day)
            except ValueError:
                pass
    return date.fromtimestamp((folder / "Connections.csv").stat().st_mtime)


def find_export():
    """The newest unzipped LinkedIn data export in your data folder (or $LINKEDIN_EXPORT, or the sample)."""
    if SAMPLE:
        return SAMPLE_DIR / "linkedin"
    if os.environ.get("LINKEDIN_EXPORT"):
        return Path(os.environ["LINKEDIN_EXPORT"])
    found = [p for p in HOME.glob("*LinkedInDataExport*") if (p / "Connections.csv").exists()]
    if not found:
        sys.exit(f"No LinkedIn export found. Unzip it into {HOME}, or try --sample (see README.md).")
    return max(found, key=lambda p: (export_date(p), p.name))    # on the same day, Complete comes after Basic


# Cities: metro level, from profile locations ("Berlin Metropolitan Area" -> Berlin).
BAY_AREA = {"san francisco", "san francisco bay", "san jose", "oakland", "berkeley", "palo alto",
            "mountain view", "sunnyvale", "santa clara", "menlo park", "redwood city", "san mateo",
            "cupertino", "fremont", "stanford", "burlingame", "south san francisco", "los altos",
            "san carlos", "emeryville", "alameda", "foster city", "san bruno", "daly city"}
CITY_ALIASES = {"new york city": "New York"}


def city_of(location, countries):
    """Metro-level city for a LinkedIn location, or None if it only names a country."""
    first = (location or "").split(",")[0].strip()
    if not first or ("," not in location and first in countries):
        return None
    base = re.sub(r"^greater\s+", "", first, flags=re.I)
    base = re.sub(r"\s+(metropolitan area|metro area|bay area|area|county|region)$", "", base, flags=re.I).strip()
    if norm(base) in BAY_AREA:
        return "SF Bay Area"
    return CITY_ALIASES.get(norm(base), base)


def slug_of(url):
    url = urllib.parse.unquote((url or "").strip()).split("?")[0].rstrip("/").lower()
    return url.split("/in/")[-1] if "/in/" in url else ""


def years(tp):
    start = (tp or {}).get("startDate") or {}
    end = (tp or {}).get("endDate") or {}
    if not start.get("year"):
        return None, None
    a = start["year"] + ((start.get("month") or 1) - 1) / 12
    b = end["year"] + ((end.get("month") or 12) - 1) / 12 if end.get("year") else NOW
    return round(a, 2), round(b, 2)


def experience_years(positions, rx):
    spans = sorted(s for s in (years(p.get("timePeriod")) for p in positions
                               if rx.search(f"{p.get('title') or ''} {p.get('description') or ''}"))
                   if s[0] is not None)
    total, end = 0.0, None
    for a, b in spans:
        if end is None or a > end:
            total += b - a
            end = b
        elif b > end:
            total += b - end
            end = b
    return round(total, 1)


def read_csv(path, starts_with=""):
    """The rows of a LinkedIn file. A missing cell or column is empty, and odd bytes cannot stop the import.

    starts_with skips the notes LinkedIn writes above the header line."""
    text = path.read_text(encoding="utf-8-sig", errors="replace").replace("\x00", "")
    if starts_with:
        header = re.search(rf"^{starts_with}", text, re.M)
        if not header:
            sys.exit(f"{path} has no line that starts with {starts_with!r}. Is it from a LinkedIn export?")
        text = text[header.start():]
    for row in csv.DictReader(io.StringIO(text, newline="")):
        yield defaultdict(str, {k.strip(): v or "" for k, v in row.items() if k})


def connected_day(text):
    try:
        return datetime.strptime(text.strip(), "%d %b %Y").date()
    except ValueError:
        return None


def load_me():
    """Your name and headline, from Profile.csv in the export (a connections-only export has none)."""
    row = next(read_csv(find_export() / "Profile.csv"), None) if (find_export() / "Profile.csv").exists() else None
    if row is None:
        return {"name": "Me", "title": "", "location": ""}
    return {"name": f"{row['First Name']} {row['Last Name']}".strip() or "Me", "title": row["Headline"], "location": row["Geo Location"]}


def load_connections():
    forgotten = forgotten_list()
    people = {}
    for row in read_csv(find_export() / "Connections.csv", "First Name,"):
        slug = slug_of(row["URL"])
        if not slug or slug in forgotten:
            continue
        if "name:" + fold(f"{row['First Name']} {row['Last Name']}") in forgotten:
            print(printable(f"Left out {row['First Name']} {row['Last Name']}: you forgot someone with this name. To undo, edit {FORGOTTEN}."))
            continue
        people[slug] = {
            "id": slug,
            "name": f"{row['First Name']} {row['Last Name']}".strip(),
            "url": row["URL"].strip(),
            "company": row["Company"].strip(),
            "title": row["Position"].strip(),
            "connected_on": connected_day(row["Connected On"]),
        }
    undated = sum(p["connected_on"] is None for p in people.values())
    if undated:
        print(f"{undated} connection{'s have' if undated > 1 else ' has'} no date that this can read. They stay in the graph, and in no event.")
    return people


def load_enriched():
    profiles = {}
    for path in sorted(ENRICHED.glob("batch_*.json")):
        for item in json.loads(path.read_text(encoding="utf-8"))["items"]:
            if item.get("headline") or item.get("positions"):
                profiles[slug_of(item.get("inputUrl"))] = item
    return profiles


# Company funding (data/enriched/companies_*.json), keyed by normalized company name.
SMALL_TEAMS = {"1-10", "11-50", "51-100", "101-250", "251-500", "501-1000", "1001-5000"}


def load_companies():
    found = {}
    for path in sorted(ENRICHED.glob("companies_*.json")):
        for item in json.loads(path.read_text(encoding="utf-8"))["items"]:
            if item.get("name") and item.get("searchTerm"):
                found[norm(item["searchTerm"])] = item
    return found


def well_funded(key, cb):
    """Private, active, at most 5,000 people, and a Series round or 8+ investors."""
    rounds = [r.get("round") or "" for r in cb.get("fundingRounds") or []]
    return (key not in NOT_STARTUPS and cb.get("ipoStatus") == "private"
            and cb.get("operatingStatus") == "active" and cb.get("employeeCount") in SMALL_TEAMS
            and (any("Series" in r for r in rounds) or (cb.get("numInvestors") or 0) >= 8))


def funding_line(cb):
    """Short card text, e.g. "Series B · 51-100 people · 13 investors"."""
    names = [r.get("round") or "" for r in cb.get("fundingRounds") or []]
    stage = next((r for r in names if r.startswith(("Series", "Seed", "Pre Seed"))), names[0] if names else "")
    return " · ".join(x for x in [stage, cb.get("employeeCount") and f"{cb['employeeCount']} people",
                                  cb.get("numInvestors") and f"{cb['numInvestors']} investors"] if x)


def load_messages():
    """Message counts per person, group-chat participant sets, and each person's last message date (by slug)."""
    if not (find_export() / "messages.csv").exists():
        return Counter(), [], {}
    rows = [(row["CONVERSATION ID"],
             {slug_of(u) for u in [row["SENDER PROFILE URL"]] + row["RECIPIENT PROFILE URLS"].split(",")
              if u.strip()} - {""},
             row["DATE"][:10])    # only the day: the text of a message never leaves the export
            for row in read_csv(find_export() / "messages.csv")]
    # You are in almost every message: the most common profile is you.
    mine = Counter(s for _, slugs, _ in rows for s in slugs).most_common(1)
    me = mine[0][0] if mine else ""
    counts = Counter()
    last = {}
    convs = defaultdict(set)
    for conv, slugs, day in rows:
        slugs = slugs - {me}
        for s in slugs:
            counts[s] += 1
            if re.fullmatch(r"\d{4}-\d\d-\d\d", day) and day > last.get(s, ""):
                last[s] = day
        convs[conv] |= slugs
    groups = [s for s in convs.values() if len(s) >= 2]
    return counts, groups, last


def load_share_hints():
    """Your own posts (date, first words), used as hints to name inferred events."""
    hints = []
    for path in find_export().glob("Shares*.csv"):
        for row in read_csv(path):
            try:
                day = date.fromisoformat(row["Date"][:10])
            except ValueError:
                continue
            text = " ".join(row["ShareCommentary"].replace('"', "").split())
            hints.append((day, text[:90]))
    return hints


# Upcoming public events (data/enriched/events_*.json), linked to your network through their hosts.
EVENT_TOPICS = ["AI Agents", "Agent Memory", "Knowledge Graphs", "RAG & Search", "LLMs & GenAI",
                "Developer Relations"]
WORDS = Path("/usr/share/dict/words")


@functools.lru_cache(maxsize=1)
def common_words():
    return set(WORDS.read_text(encoding="utf-8", errors="ignore").lower().split()) if WORDS.exists() else set()


def is_common(name):
    """True when every word of a name is an English word ("Open Source", "Builders", "The Post")."""
    words = re.findall(r"[a-z0-9]+", name.lower())   # "neo4j" is one word, and not an English one
    return bool(words) and all(w in common_words() or (w.endswith("s") and w[:-1] in common_words())
                               for w in words)


FORMER_RE = re.compile(r"\b(ex|former|formerly|prev|previously|past)\b", re.I)


def mentions(text, name):
    """Whole-word, case-sensitive mention of a company in an event name. A name that is also an
    English word (Notion, Step) only counts next to a co-host marker: "x Notion", "@ Notion"."""
    n = re.escape(name)
    if not re.search(rf"(?<!\w){n}(?!\w)", text):
        return False
    return not is_common(name) or bool(re.search(
        rf"(?i:\bx|@|\bw/|\bwith|\bby|\bat)\s+{n}(?!\w)|(?<!\w){n}\s+(?i:x\b|presents|hosts|&)", text))


def works_at(bio, name):
    """Does a host's bio say they work at this company now? ("DevRel @ Orbitwise", not "ex-Orbitwise")."""
    n = re.escape(name)
    for part in re.split(r"[|·•,;\n]", bio):
        # "at Foundation Capital" is another company: no capitalized word may follow.
        if re.search(rf"(?i:@|\bat)\s*{n}(?!\w)(?!\s+[A-Z][a-z])", part) and not FORMER_RE.search(part):
            return True
    return False


def event_start(e):
    """When an event starts, in its own time zone. With no time zone data, or an unknown zone, in UTC."""
    start = datetime.fromisoformat(e["startAt"].replace("Z", "+00:00"))
    try:
        start = start.astimezone(ZoneInfo(e.get("timezone") or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        pass
    # The made-up sample must stay in the future, however old the package is.
    while SAMPLE and start.date() < date.today():
        start = start.replace(year=start.year + 1)
    return start


def load_upcoming(people, current, countries):
    """Upcoming events on your topics, and how each one links to your network.

    `current` maps company key -> name for companies where a connection works now.
    Returns event rows and (event, Person or Company, via) host links."""
    items = {}
    for path in sorted(ENRICHED.glob("events_*.json")):
        for e in json.loads(path.read_text(encoding="utf-8"))["items"]:
            items[e["eventId"]] = e
    name_count = Counter(norm(p["name"]) for p in people.values())
    slug_by_name = {norm(p["name"]): s for s, p in people.items()}
    targets = [(k, n) for k, n in current.items()
               if len(k) >= 3 and k not in BAY_AREA and "san francisco" not in k]
    rows, hosted = [], []
    for e in items.values():
        start = event_start(e)
        text = f"{e['name']} {(e.get('description') or '')[:2000]}"
        topics = [t for t in EVENT_TOPICS if TOPIC_RE[t].search(text)]
        if start.date() < date.today() or not (topics or {"AI", "Tech"} & set(e.get("categoryNames") or [])):
            continue
        eid = "luma-" + e["eventId"]
        links = {}
        for h in e.get("hosts") or []:
            slug, key = slug_of((h.get("socialHandles") or {}).get("linkedin")), norm(h.get("name"))
            if slug in people:
                links[("Person", slug)] = "host"
            elif not slug and len(key.split()) >= 2 and name_count[key] == 1:
                links[("Person", slug_by_name[key])] = "host (same name)"
            if key in current:
                links.setdefault(("Company", key), "host")
            bio = h.get("bio") or ""
            for k, n in targets:
                if n in bio and works_at(bio, n):
                    links.setdefault(("Company", k), f"host {h.get('name') or ''} works there".replace("  ", " "))
        for k, n in targets:
            if n in e["name"] and mentions(e["name"], n):
                links.setdefault(("Company", k), "named in the title")
        loc = e.get("location") or {}
        guests = e.get("guestCount") or 0
        rows.append({"id": eid, "name": e["name"], "search": fold(e["name"]), "date": start.date().isoformat(),
                     "time": start.strftime("%H:%M"), "url": e.get("eventUrl") or "", "upcoming": True,
                     "venue": loc.get("name") or "", "guests": guests,
                     "hosts": ", ".join(h["name"] for h in e.get("hosts") or [] if h.get("name"))[:200],
                     "blurb": " ".join((e.get("description") or "").split())[:300],
                     "summary": f"{start:%b %d} · {loc.get('city') or 'online'} · {guests} going",
                     "topics": topics, "city": city_of(loc.get("city") or "", countries)})
        hosted += [{"e": eid, "label": label, "id": key, "via": via} for (label, key), via in links.items()]
    return rows, hosted


def confirm_sample(graph, place):
    """The sample must not take the place of a real network with no question: it comes back only from a LinkedIn export."""
    real = graph.ro_query("MATCH (p:Person) WHERE p.source IS NULL RETURN count(p)").result_set[0][0]
    marked = graph.ro_query("MATCH (m:Me) RETURN m.sample").result_set
    if not real or (marked and marked[0][0]):
        return
    print(f"{place} holds {real} people from a real network. --sample replaces them with the made-up network,\n"
          "and only your LinkedIn export can bring them back. To keep them, use another graph: PB_GRAPH=sample personal-brain load --sample\n"
          "Replace them? [y/N] ", end="", flush=True)
    if sys.stdin.readline().strip().lower() not in ("y", "yes"):
        sys.exit("Nothing changed.")


def main():
    global KNOWN_EVENTS, NOT_STARTUPS
    KNOWN_EVENTS, NOT_STARTUPS = load_settings()
    if not SAMPLE:
        print(f"Reading {find_export()}")
    people = load_connections()
    profiles = load_enriched()
    msg_counts, groups, last_messages = load_messages()
    share_hints = load_share_hints()
    print(f"connections {len(people)}, enriched {len(profiles)}, group chats {len(groups)}")

    me = load_me()
    # A location with no comma that is also the last part of other locations is just a country.
    countries = {loc.rsplit(",", 1)[-1].strip() for prof in profiles.values()
                 for loc in [prof.get("geoLocationName") or ""] if "," in loc}
    cities, lives = {}, []
    persons, companies, schools, skills_count = [], {}, {}, Counter()
    works, studied, has_skill, interested, experience = [], [], [], [], []
    work_spans = defaultdict(list)   # company key -> [(slug, a, b)]
    school_spans = defaultdict(list)
    coworkers_now = defaultdict(list)   # company key -> [slug], people with no job history

    def company_key(name):
        key = norm(name)
        if key and key not in GENERIC_COMPANIES:
            companies.setdefault(key, name.strip())
            return key
        return None

    for slug, p in people.items():
        prof = profiles.get(slug, {})
        headline = prof.get("headline") or p["title"]
        about = (prof.get("summary") or "")[:400]
        location = prof.get("geoLocationName") or ""
        city = city_of(location, countries)
        if city:
            cities.setdefault(norm(city), city)
            lives.append({"p": slug, "c": norm(city)})
        skill_names = [s.get("name", "") for s in prof.get("skills") or [] if s.get("name")]
        text = " ".join([headline, about, p["title"], " ".join(skill_names)])
        topics = [t for t, rx in TOPIC_RE.items() if rx.search(text)]
        role_text = f"{p['title']} {headline}"
        persons.append({
            "id": slug, "name": p["name"], "search": fold(p["name"]), "url": p["url"], "headline": headline,
            "about": about, "location": location, "title": p["title"],
            "company": p["company"], "connected_on": p["connected_on"] and p["connected_on"].isoformat(),
            "messages": msg_counts.get(slug, 0), "last_message": last_messages.get(slug), "enriched": bool(prof),
            "event_contact": bool(EVENT_ROLE_RE.search(role_text)),
        })
        for t in topics:
            interested.append({"p": slug, "t": t})

        positions = prof.get("positions") or []
        for tech, rx in TECH_RE.items():
            yrs = experience_years(positions, rx)
            if yrs >= 0.5:
                experience.append({"p": slug, "s": norm(tech), "label": tech, "years": yrs})
        if positions:
            for pos in positions:
                key = company_key((pos.get("company") or {}).get("name"))
                if not key:
                    continue
                a, b = years(pos.get("timePeriod"))
                current = not ((pos.get("timePeriod") or {}).get("endDate"))
                works.append({"p": slug, "c": key, "title": pos.get("title") or "",
                              "start": a, "end": None if current else b, "current": current})
                if a is not None:
                    work_spans[key].append((slug, a, b))
        else:
            key = company_key(p["company"])
            if key:
                works.append({"p": slug, "c": key, "title": p["title"],
                              "start": None, "end": None, "current": True})
                coworkers_now[key].append(slug)

        for edu in prof.get("educations") or []:
            key = norm(edu.get("schoolName"))
            if not key:
                continue
            schools.setdefault(key, edu["schoolName"].strip())
            a, b = years(edu.get("timePeriod"))
            studied.append({"p": slug, "s": key, "degree": edu.get("degreeName") or "",
                            "field": edu.get("fieldOfStudy") or "", "start": a, "end": b})
            if a is not None:
                school_spans[key].append((slug, a, b))

        for name in skill_names:
            skills_count[norm(name)] += 1
            has_skill.append({"p": slug, "s": norm(name), "label": name})

    # Keep skills shared by at least 3 people, so the graph stays readable.
    skill_names = {}
    for edge in has_skill:
        if skills_count[edge["s"]] >= 3:
            skill_names.setdefault(edge["s"], edge["label"])
    has_skill = [e for e in has_skill if e["s"] in skill_names]
    for edge in experience:
        skill_names.setdefault(edge["s"], edge["label"])

    # Inferred KNOWS edges: same company or school, overlapping at least 3 months.
    knows = {}

    def add_overlaps(spans, via, names, cohort_above=None):
        for key, lst in spans.items():
            if len(lst) > 400:
                continue
            # At big schools, overlapping years is weak: require the same start year.
            same_cohort = cohort_above is not None and len(lst) > cohort_above
            for (a, sa, ea), (b, sb, eb) in itertools.combinations(lst, 2):
                overlap = min(ea, eb) - max(sa, sb)
                if same_cohort and int(sa) != int(sb):
                    continue
                if a != b and overlap >= 0.25:
                    pair = tuple(sorted((a, b)))
                    if pair not in knows or knows[pair]["overlap"] < overlap:
                        knows[pair] = {"a": pair[0], "b": pair[1], "via": via,
                                       "place": names[key], "overlap": round(overlap, 1)}

    add_overlaps(work_spans, "coworkers", companies)
    add_overlaps(school_spans, "classmates", schools, cohort_above=30)
    # No job history (a connections-only export): the same company now is the only clue.
    for key, lst in coworkers_now.items():
        if len(lst) <= 400:
            for a, b in itertools.combinations(sorted(lst), 2):
                knows.setdefault((a, b), {"a": a, "b": b, "via": "coworkers", "place": companies[key],
                                          "overlap": 0.0})
    for group in groups:
        for a, b in itertools.combinations(sorted(group & people.keys()), 2):
            knows[(a, b)] = {"a": a, "b": b, "via": "group chat", "place": "LinkedIn group chat",
                             "overlap": 0.0}

    # Inferred events: days when you added 5+ connections.
    by_day = defaultdict(list)
    for slug, p in people.items():
        if p["connected_on"]:
            by_day[p["connected_on"]].append(slug)
    events, met = [], []
    for day, slugs in by_day.items():
        if len(slugs) < 5:
            continue
        hint = next((t for d, t in share_hints if 0 <= (d - day).days <= 3 and t), "")
        name = KNOWN_EVENTS.get(day, f"Event on {day.strftime('%d %b %Y')}")
        events.append({"id": day.isoformat(), "name": name, "search": fold(name), "date": day.isoformat(),
                       "people": len(slugs), "confirmed": day in KNOWN_EVENTS, "hint": hint})
        met += [{"p": s, "e": day.isoformat()} for s in slugs]

    # Your own jobs and schools.
    me_jobs, me_schools = [], []
    if (find_export() / "Positions.csv").exists():
        for row in read_csv(find_export() / "Positions.csv"):
            key = company_key(row["Company Name"])
            if key:
                me_jobs.append({"c": key, "title": row["Title"]})
    if (find_export() / "Education.csv").exists():
        for row in read_csv(find_export() / "Education.csv"):
            key = norm(row["School Name"])
            if key:
                schools.setdefault(key, row["School Name"].strip())
                me_schools.append({"s": key})

    # Funding: company facts plus Investor nodes from Crunchbase rounds.
    crunchbase = load_companies()
    company_rows, investors, funded_by = [], {}, {}
    for key, name in companies.items():
        cb = crunchbase.get(key)
        row = {"id": key, "name": name, "search": fold(name), "cb": bool(cb)}
        if cb:
            rounds = cb.get("fundingRounds") or []
            row.update({"employees": cb.get("employeeCount") or "", "ipo": cb.get("ipoStatus") or "",
                        "rounds": len(rounds), "last_round": rounds[0].get("round", "") if rounds else "",
                        "num_investors": cb.get("numInvestors") or 0,
                        "industries": ", ".join((cb.get("industries") or [])[:4]),
                        "well_funded": well_funded(key, cb)})
            row["funding"] = funding_line(cb)
            for rnd in rounds:
                leads = set(rnd.get("leadInvestors") or [])
                for inv in rnd.get("investors") or []:
                    ikey = norm(inv)
                    if not ikey:
                        continue
                    investors.setdefault(ikey, inv.strip())
                    edge = funded_by.setdefault((key, ikey), {"c": key, "i": ikey, "lead": False,
                                                              "round": rnd.get("round") or ""})
                    edge["lead"] = edge["lead"] or inv in leads
        company_rows.append(row)
    print(f"companies with Crunchbase data {sum(r['cb'] for r in company_rows)}, "
          f"well funded {sum(r.get('well_funded', False) for r in company_rows)}, investors {len(investors)}")

    current = {w["c"]: companies[w["c"]] for w in works if w["current"]}
    upcoming, hosted = load_upcoming(people, current, countries)
    for ev in upcoming:
        if ev["city"]:
            cities.setdefault(norm(ev["city"]), ev["city"])
    print(f"upcoming events {len(upcoming)}, linked to your network {len({h['e'] for h in hosted})} "
          f"(hosts you know {sum(h['label'] == 'Person' for h in hosted)}, "
          f"companies {sum(h['label'] == 'Company' for h in hosted)})")

    if "--cloud" in sys.argv:
        db = open_url(cloud_url())
    else:
        db = FalkorDB(host="localhost", port=6379)
    graph = db.select_graph(GRAPH)
    exists = GRAPH in db.list_graphs()
    kept = None
    place = ("cloud/" if "--cloud" in sys.argv else "local/") + GRAPH
    saved = notes_backup.pending()
    other_graph = saved and saved.get("place", place) != place
    went = bool(saved and not other_graph and saved.get("graph_deleted"))
    # After a rebuild that stopped, the graph holds only part of the old jobs, or the new ones. The backup has them.
    old_jobs = saved["old_jobs"] if went and "old_jobs" in saved else job_changes.read_jobs(graph) if exists else {}
    if SAMPLE and exists and "--yes" not in sys.argv:
        confirm_sample(graph, place)
    if "--force" not in sys.argv:
        forgotten = forgotten_list()
        kept = notes_backup.choose(saved, notes_backup.snapshot(graph) if exists else None, place, forgotten)
        if saved and "graph_deleted" not in saved and kept["graph_deleted"]:
            print("An older version stopped during a rebuild. Its backup has notes that this graph does not.\n"
                  "They are back now. If you forgot one of them since, run personal-brain forget again.")
    backup = kept or {"nodes": [], "about": [], "links": [], "place": place, "graph_deleted": went}
    if not other_graph:
        notes_backup.save({**backup, "old_jobs": old_jobs})
    try:
        graph.delete()
    except Exception:
        pass
    if not other_graph:
        # From here on, the backup is the only copy. A stop before here leaves the graph as it was.
        notes_backup.save({**backup, "old_jobs": old_jobs, "graph_deleted": GRAPH not in db.list_graphs()})
    graph = db.select_graph(GRAPH)

    def run(query, rows, size=1000):
        for i in range(0, len(rows), size):
            graph.query(query, {"rows": rows[i:i + size]})

    for label in ["Person", "Company", "School", "Skill", "Topic", "Event", "Investor", "City"]:
        graph.query(f"CREATE INDEX FOR (n:{label}) ON (n.id)")
    graph.query("CREATE (:Me {id: 'me', name: $name, title: $title, location: $location, sample: $sample})", {**me, "sample": SAMPLE or None})
    run("UNWIND $rows AS r CREATE (:Person {id: r.id, name: r.name, search: r.search, url: r.url, headline: r.headline,"
        " about: r.about, location: r.location, title: r.title, company: r.company,"
        " connected_on: r.connected_on, messages: r.messages, last_message: r.last_message, enriched: r.enriched,"
        " event_contact: r.event_contact})", persons)
    run("UNWIND $rows AS r CREATE (c:Company {id: r.id, name: r.name}) SET c += r", company_rows)
    run("UNWIND $rows AS r CREATE (:Investor {id: r.id, name: r.name})",
        [{"id": k, "name": v} for k, v in investors.items()])
    run("UNWIND $rows AS r CREATE (:School {id: r.id, name: r.name})",
        [{"id": k, "name": v} for k, v in schools.items()])
    run("UNWIND $rows AS r CREATE (:Skill {id: r.id, name: r.name})",
        [{"id": k, "name": v} for k, v in skill_names.items()])
    run("UNWIND $rows AS r CREATE (:Topic {id: r, name: r})", list(TOPICS))
    run("UNWIND $rows AS r CREATE (:Event {id: r.id, name: r.name, search: r.search, date: r.date,"
        " people: r.people, confirmed: r.confirmed, hint: r.hint})", events)
    me_city = city_of(me["location"], countries)
    if me_city:
        cities.setdefault(norm(me_city), me_city)
    residents = Counter(r["c"] for r in lives)
    run("UNWIND $rows AS r CREATE (:City {id: r.id, name: r.name, people: r.people})",
        [{"id": k, "name": v, "people": residents[k]} for k, v in cities.items()])

    run("UNWIND $rows AS r MATCH (m:Me), (p:Person {id: r.id})"
        " CREATE (m)-[:CONNECTED {on: r.connected_on, messages: r.messages}]->(p)", persons)
    run("UNWIND $rows AS r MATCH (p:Person {id: r.p}), (c:Company {id: r.c})"
        " CREATE (p)-[:WORKED_AT {title: r.title, start: r.start, end: r.end, current: r.current}]->(c)",
        works)
    run("UNWIND $rows AS r MATCH (p:Person {id: r.p}), (s:School {id: r.s})"
        " CREATE (p)-[:STUDIED_AT {degree: r.degree, field: r.field, start: r.start, end: r.end}]->(s)",
        studied)
    run("UNWIND $rows AS r MATCH (p:Person {id: r.p}), (s:Skill {id: r.s}) CREATE (p)-[:HAS_SKILL]->(s)",
        has_skill)
    run("UNWIND $rows AS r MATCH (p:Person {id: r.p}), (s:Skill {id: r.s})"
        " CREATE (p)-[:HAS_EXPERIENCE {years: r.years}]->(s)", experience)
    run("UNWIND $rows AS r MATCH (p:Person {id: r.p}), (t:Topic {id: r.t}) CREATE (p)-[:INTO]->(t)",
        interested)
    run("UNWIND $rows AS r MATCH (p:Person {id: r.p}), (e:Event {id: r.e})"
        " CREATE (p)-[:MET_AT {inferred: true}]->(e)", met)
    run("UNWIND $rows AS r MATCH (m:Me), (e:Event {id: r.id}) CREATE (m)-[:ATTENDED]->(e)", events)
    run("UNWIND $rows AS r MATCH (a:Person {id: r.a}), (b:Person {id: r.b})"
        " CREATE (a)-[:KNOWS {via: r.via, place: r.place, overlap_years: r.overlap}]->(b)",
        list(knows.values()))
    run("UNWIND $rows AS r MATCH (m:Me), (c:Company {id: r.c}) CREATE (m)-[:WORKED_AT {title: r.title}]->(c)",
        me_jobs)
    run("UNWIND $rows AS r MATCH (c:Company {id: r.c}), (i:Investor {id: r.i})"
        " CREATE (c)-[:FUNDED_BY {lead: r.lead, round: r.round}]->(i)", list(funded_by.values()))
    run("UNWIND $rows AS r MATCH (m:Me), (s:School {id: r.s}) CREATE (m)-[:STUDIED_AT]->(s)", me_schools)
    run("UNWIND $rows AS r MATCH (p:Person {id: r.p}), (c:City {id: r.c}) CREATE (p)-[:LIVES_IN]->(c)", lives)
    run("UNWIND $rows AS r CREATE (e:Event {id: r.id}) SET e += r",
        [{k: v for k, v in ev.items() if k not in ("topics", "city")} for ev in upcoming])
    for label in ["Person", "Company"]:
        run(f"UNWIND $rows AS r MATCH (e:Event {{id: r.e}}), (x:{label} {{id: r.id}})"
            " CREATE (e)-[:HOSTED_BY {via: r.via}]->(x)", [h for h in hosted if h["label"] == label])
    run("UNWIND $rows AS r MATCH (e:Event {id: r.e}), (t:Topic {id: r.t}) CREATE (e)-[:ABOUT]->(t)",
        [{"e": ev["id"], "t": t} for ev in upcoming for t in ev["topics"]])
    run("UNWIND $rows AS r MATCH (e:Event {id: r.e}), (c:City {id: r.c}) CREATE (e)-[:HELD_IN]->(c)",
        [{"e": ev["id"], "c": norm(ev["city"])} for ev in upcoming if ev["city"]])
    if me_city:
        graph.query("MATCH (m:Me), (c:City {id: $c}) CREATE (m)-[:LIVES_IN]->(c)", {"c": norm(me_city)})
    create_search_indexes(graph)
    job_news = job_changes.mark(graph, old_jobs)
    if kept and kept["nodes"]:
        notes, merged = notes_backup.restore(graph, kept)
        print(f"Kept {notes} notes and what they added. Use --force to drop them.")
        if merged:
            print(f"Merged {merged} people you saved with the same people in this import.")
    notes_backup.clear(place)
    job_changes.report(job_news)
    # A rebuild gives every node a new id, and the viewer's files find people by id. Another graph has other files.
    if GRAPH == os.environ.get("PB_GRAPH", "network"):
        problem = refresh_viewer(cloud="--cloud" in sys.argv)
        if problem:
            print(f"The graph is ready, but the viewer's files are old. Run personal-brain export.\n{problem}")

    nodes = graph.query("MATCH (n) RETURN labels(n)[0], count(n) ORDER BY count(n) DESC").result_set
    rels = graph.query("MATCH ()-[r]->() RETURN type(r), count(r) ORDER BY count(r) DESC").result_set
    print("nodes:", nodes, "total", sum(c for _, c in nodes))
    print("edges:", rels, "total", sum(c for _, c in rels))
    via = Counter(k["via"] for k in knows.values())
    print("KNOWS by source:", dict(via))


if __name__ == "__main__":
    sys.exit(main())
