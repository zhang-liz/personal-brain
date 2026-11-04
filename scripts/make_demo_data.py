"""Write data/demo/: a big made-up network for the public demo, in the sample_data/ formats.

Every connection is invented. Only the owner at the center is real. Companies, investors, schools and events are real names,
so the example questions read well. Jobs, funding links and event dates are illustrative.

    python3 scripts/make_demo_data.py               # about 19k nodes
    python3 scripts/make_demo_data.py --people 1000 # quick test
"""

import argparse
import csv
import json
import random
import unicodedata
from datetime import date, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 11, 4)
rnd = random.Random(7)

FIRST = """Emma Olivia Sophie Charlotte Amelia Hannah Grace Lucy Claire Laura Sarah Rachel Megan Emily Kate Anna Julia
Helen Alice Eleanor Isabel Natalie Rebecca Abigail Caroline Victoria Margaret Lauren Holly Molly Paige Heather Jenna
Kristen Allison Bethany Danielle Erin Hailey Lindsay Meredith Morgan Naomi Tessa Whitney Greta Ingrid Astrid Freya
Elsa Linnea Maja Klara Annika Nora Silje Saskia Marlene Heidi Monika Petra Elena Chiara Giulia Francesca Martina
Camille Margaux Juliette Elodie Colette James John Robert Michael William David Richard Thomas Daniel Matthew Andrew
Christopher Ryan Nathan Adam Peter Paul Mark Stephen Kevin Brian Patrick Sean Connor Liam Noah Oliver Henry Jack
Benjamin Samuel Joseph Alexander Nicholas Tyler Jacob Ethan Luke Owen Graham Grant Spencer Trevor Garrett Wesley
Bradley Dylan Evan Ian Colin Simon Hugo Felix Lukas Jonas Tobias Matthias Stefan Florian Sebastian Henrik Lars Nils
Anders Erik Magnus Sven Bjorn Marco Luca Matteo Alessandro Pierre Antoine Etienne Laurent Pieter Bram Daan Tomasz
Marek Piotr Andrei""".split()
LAST = """Smith Johnson Williams Brown Miller Davis Wilson Anderson Taylor Thomas Moore Martin Thompson White Harris
Clark Lewis Robinson Walker Hall Allen Young King Wright Scott Hill Green Adams Baker Nelson Carter Mitchell Roberts
Turner Phillips Campbell Parker Evans Edwards Collins Stewart Morris Rogers Reed Cook Morgan Bell Murphy Bailey Cooper
Richardson Cox Howard Ward Peterson Gray Watson Brooks Bennett Wood Barnes Ross Henderson Coleman Jenkins Perry Powell
Long Hughes Foster Bryant Russell Sullivan Fisher Hayes Myers Ford Hamilton Graham Wallace Cole West Jordan Owens
Reynolds Ellis Harrison Gibson McDonald Marshall Murray Freeman Wells Webb Simpson Stevens Tucker Porter Hunter Hicks
Crawford Henry Boyd Mason Kennedy Warren Dixon Burns Gordon Shaw Holmes Rice Robertson Hunt Black Daniels Palmer Mills
Nichols Grant Knight Ferguson Rose Stone Dunn Perkins Hudson Spencer Gardner Stephens Payne Pierce Berry Matthews
Arnold Wagner Willis Ray Watkins Olsen Carroll Duncan Snyder Hart Cunningham Bradley Lane Andrews Harper Fox Riley
Armstrong Carpenter Weaver Greene Lawrence Elliott Chapman Sims Austin Peters Kelley Franklin Lawson Muller Schmidt
Schneider Fischer Weber Meyer Wagner Becker Schulz Hoffmann Koch Richter Klein Wolf Neumann Schwarz Zimmermann Lange
Krause Larsen Hansen Nielsen Jensen Lindqvist Bergstrom Nystrom Johansson Eriksson Rossi Ferrari Bianchi Romano
Colombo Ricci Moreau Laurent Lefebvre Dubois Bernard Girard Fournier Janssen Visser Bakker Novak Kowalski Nowak
Horvat Popescu""".split()
OTHER_FIRST = """Sana Arjun Ananya Ravi Mei Wei Jin Hana Kenji Yuki Amara Kwame Thabo Ifeoma Emeka Noor Omar Farah
Yusuf Leila Mateo Lucia Diego Valentina Camila Rafael Joon Minji""".split()
OTHER_LAST = """Patel Sharma Iyer Nair Chen Wang Liu Zhang Kim Park Tanaka Sato Okafor Mensah Adeyemi Mwangi Haddad
Khan Rahman Yilmaz Garcia Rodriguez Silva Morales Nguyen Tran""".split()

CITIES = [("San Francisco Bay Area", 40), ("New York, New York, United States", 22), ("Seattle, Washington, United States", 10),
          ("Boston, Massachusetts, United States", 8), ("Austin, Texas, United States", 7), ("Los Angeles, California, United States", 8),
          ("Chicago, Illinois, United States", 5), ("Denver, Colorado, United States", 4), ("Atlanta, Georgia, United States", 3),
          ("Miami, Florida, United States", 3), ("Washington, District of Columbia, United States", 3), ("Toronto, Ontario, Canada", 6),
          ("Vancouver, British Columbia, Canada", 3), ("Montreal, Quebec, Canada", 2), ("London, England, United Kingdom", 14),
          ("Cambridge, England, United Kingdom", 1), ("Dublin, Ireland", 3), ("Paris, France", 6), ("Berlin, Germany", 7),
          ("Munich, Germany", 3), ("Amsterdam, Netherlands", 4), ("Zurich, Switzerland", 3), ("Stockholm, Sweden", 3),
          ("Copenhagen, Denmark", 2), ("Oslo, Norway", 1), ("Helsinki, Finland", 1), ("Lisbon, Portugal", 2), ("Madrid, Spain", 2),
          ("Barcelona, Spain", 2), ("Milan, Italy", 1), ("Warsaw, Poland", 1), ("Prague, Czechia", 1), ("Tel Aviv, Israel", 2),
          ("Singapore, Singapore", 2), ("Sydney, Australia", 2), ("Melbourne, Australia", 1), ("Bengaluru, Karnataka, India", 2),
          ("Tokyo, Japan", 1), ("Seoul, South Korea", 1), ("Sao Paulo, Brazil", 1), ("Mexico City, Mexico", 1)]

TOP_SCHOOLS = """Stanford University|Massachusetts Institute of Technology|Harvard University|UC Berkeley|Carnegie Mellon University
University of Pennsylvania|Cornell University|Columbia University|University of Michigan|University of Washington
University of Waterloo|University of Oxford|University of Cambridge|Georgia Tech|New York University""".replace("\n", "|").split("|")
SCHOOLS = """Yale University|Princeton University|Duke University|Northwestern University|University of Chicago|Brown University
Dartmouth College|Rice University|Vanderbilt University|Johns Hopkins University|UCLA|UC San Diego|UC Davis|UC Santa Barbara
University of Southern California|University of Texas at Austin|Purdue University|University of Illinois Urbana-Champaign
University of Wisconsin-Madison|University of Virginia|University of Maryland|Boston University|Northeastern University
Penn State University|Ohio State University|University of Colorado Boulder|Arizona State University|University of Florida
Virginia Tech|Caltech|University of Toronto|McGill University|University of British Columbia|ETH Zurich|EPFL
Imperial College London|University College London|London School of Economics|University of Edinburgh
Technical University of Munich|LMU Munich|RWTH Aachen University|Humboldt University of Berlin|Sorbonne University
HEC Paris|INSEAD|Ecole Polytechnique|Delft University of Technology|University of Amsterdam|KTH Royal Institute of Technology
Stockholm School of Economics|University of Copenhagen|Trinity College Dublin|Bocconi University|Politecnico di Milano
University of Warsaw|Tel Aviv University|Technion|National University of Singapore|University of Melbourne|University of Sydney
IIT Bombay|Tsinghua University|Seoul National University|University of Tokyo""".replace("\n", "|").split("|")

MEGA = """Google|Meta|Microsoft|Apple|NVIDIA|Amazon|Amazon Web Services (AWS)|IBM|Salesforce|Oracle|Intel|Cisco|Adobe|Netflix
LinkedIn|Uber|Airbnb|Tesla|SpaceX|Accenture|Deloitte|McKinsey & Company|Boston Consulting Group|Bain & Company|Goldman Sachs
JPMorgan Chase|Morgan Stanley|Bank of America|Citi|Capital One|American Express|Visa|Mastercard|PayPal|Walmart|Target
Nike|Disney|Spotify|ServiceNow""".replace("\n", "|").split("|")
LARGE = """AMD|Qualcomm|Dell Technologies|HP|VMware|SAP|Workday|Intuit|Autodesk|Atlassian|Shopify|Block|Coinbase|Robinhood
Lyft|DoorDash|Instacart|Pinterest|Snap|Reddit|Dropbox|Box|Zoom|Slack|Twilio|Okta|Cloudflare|Datadog|MongoDB|Elastic
Confluent|GitHub|GitLab|HashiCorp|Red Hat|Palantir|Snowflake|Unity|Roblox|Electronic Arts|Sony|Samsung|Siemens|Bosch
Philips|Ericsson|Nokia|Booking.com|Expedia|Zillow|Peloton|Etsy|eBay|Wayfair|Yelp|Duolingo|TikTok|ByteDance|Alibaba
Tencent|Baidu|Arm|ASML|TSMC|Micron|Broadcom|Pfizer|Johnson & Johnson|Moderna|Boeing|Lockheed Martin|General Electric
Ford|General Motors|Rivian|BMW|Mercedes-Benz|Volkswagen|Toyota|Coca-Cola|PepsiCo|Procter & Gamble|Unilever|Starbucks
Verizon|AT&T|T-Mobile|Comcast|The New York Times|Bloomberg|Reuters|Forbes|TechCrunch|Wells Fargo|BlackRock|Fidelity
Charles Schwab|KPMG|PwC|EY|Infosys|Tata Consultancy Services|Capgemini""".replace("\n", "|").split("|")

# Well-known private companies: name, team size, latest stage, investors, AI company?
STARTUPS = [
    ("OpenAI", "1001-5000", "Series F", ["Microsoft", "Thrive Capital", "Khosla Ventures", "SoftBank", "Sequoia Capital", "Andreessen Horowitz"], True),
    ("Anthropic", "1001-5000", "Series F", ["Google", "Amazon", "Spark Capital", "Lightspeed Venture Partners", "ICONIQ Capital", "Menlo Ventures"], True),
    ("xAI", "1001-5000", "Series C", ["Andreessen Horowitz", "Sequoia Capital", "Valor Equity Partners", "Fidelity"], True),
    ("Mistral AI", "251-500", "Series C", ["Lightspeed Venture Partners", "Andreessen Horowitz", "General Catalyst", "NVIDIA"], True),
    ("Cohere", "251-500", "Series D", ["Index Ventures", "Radical Ventures", "NVIDIA", "Salesforce Ventures"], True),
    ("Perplexity", "251-500", "Series D", ["IVP", "NEA", "NVIDIA", "Bessemer Venture Partners"], True),
    ("Databricks", "1001-5000", "Series J", ["Andreessen Horowitz", "Thrive Capital", "NEA", "Microsoft", "Coatue"], True),
    ("Scale AI", "501-1000", "Series F", ["Accel", "Index Ventures", "Founders Fund", "Tiger Global"], True),
    ("Hugging Face", "251-500", "Series D", ["Lux Capital", "Sequoia Capital", "Google", "NVIDIA"], True),
    ("LangChain", "51-100", "Series B", ["Sequoia Capital", "Benchmark", "IVP"], True),
    ("LlamaIndex", "11-50", "Series A", ["Norwest Venture Partners", "Greylock"], True),
    ("Pinecone", "101-250", "Series B", ["Andreessen Horowitz", "ICONIQ Capital", "Menlo Ventures"], True),
    ("Weaviate", "51-100", "Series B", ["Index Ventures", "Battery Ventures", "NEA"], True),
    ("Neo4j", "501-1000", "Series F", ["Eurazeo", "GV", "One Peak"], True),
    ("Mem0", "11-50", "Series A", ["Basis Set Ventures", "Y Combinator"], True),
    ("Letta", "1-10", "Seed", ["Felicis"], True),
    ("Cursor", "101-250", "Series C", ["Thrive Capital", "Andreessen Horowitz", "Accel"], True),
    ("Cognition", "51-100", "Series B", ["Founders Fund", "Khosla Ventures"], True),
    ("Replit", "101-250", "Series C", ["Andreessen Horowitz", "Khosla Ventures", "Coatue"], True),
    ("Vercel", "501-1000", "Series E", ["Accel", "GV", "CRV", "Bessemer Venture Partners"], True),
    ("Supabase", "101-250", "Series D", ["Accel", "Coatue", "Felicis", "Y Combinator"], True),
    ("Notion", "501-1000", "Series C", ["Sequoia Capital", "Index Ventures", "Coatue"], True),
    ("Figma", "1001-5000", "Series E", ["Index Ventures", "Greylock", "Sequoia Capital", "Kleiner Perkins"], False),
    ("Canva", "1001-5000", "Series F", ["Sequoia Capital", "Blackbird Ventures", "T. Rowe Price"], False),
    ("Stripe", "1001-5000", "Series I", ["Sequoia Capital", "Andreessen Horowitz", "General Catalyst", "Thrive Capital"], False),
    ("Ramp", "501-1000", "Series E", ["Founders Fund", "Thrive Capital", "Khosla Ventures"], False),
    ("Brex", "501-1000", "Series D", ["Greenoaks", "Kleiner Perkins", "Y Combinator"], False),
    ("Plaid", "501-1000", "Series D", ["Index Ventures", "Kleiner Perkins", "Andreessen Horowitz"], False),
    ("Rippling", "1001-5000", "Series F", ["Founders Fund", "Greenoaks", "Coatue", "Kleiner Perkins"], False),
    ("Deel", "1001-5000", "Series D", ["Andreessen Horowitz", "Spark Capital", "Coatue"], False),
    ("Anduril", "1001-5000", "Series F", ["Founders Fund", "General Catalyst", "Thrive Capital"], False),
    ("Revolut", "1001-5000", "Series E", ["Index Ventures", "Tiger Global", "SoftBank"], False),
    ("Klarna", "1001-5000", "Series H", ["Sequoia Capital", "SoftBank", "Silver Lake"], False),
    ("Grammarly", "501-1000", "Series C", ["General Catalyst", "IVP", "Spark Capital"], True),
    ("Glean", "501-1000", "Series F", ["Kleiner Perkins", "Lightspeed Venture Partners", "Sequoia Capital"], True),
    ("Harvey", "251-500", "Series D", ["Sequoia Capital", "Kleiner Perkins", "Coatue"], True),
    ("Sierra", "101-250", "Series B", ["Sequoia Capital", "Benchmark", "Greenoaks"], True),
    ("Runway", "101-250", "Series D", ["General Atlantic", "NVIDIA", "Felicis"], True),
    ("ElevenLabs", "251-500", "Series C", ["Andreessen Horowitz", "ICONIQ Capital", "Sequoia Capital"], True),
    ("Midjourney", "11-50", "Seed", ["Y Combinator"], True),
    ("Character.AI", "101-250", "Series A", ["Andreessen Horowitz", "Google"], True),
    ("Together AI", "101-250", "Series B", ["Salesforce Ventures", "Kleiner Perkins", "NVIDIA"], True),
    ("Fireworks AI", "51-100", "Series B", ["Sequoia Capital", "Benchmark", "NVIDIA"], True),
    ("Anyscale", "101-250", "Series C", ["Andreessen Horowitz", "NEA", "Addition"], True),
    ("Weights & Biases", "251-500", "Series C", ["Insight Partners", "Coatue", "Felicis"], True),
    ("Linear", "51-100", "Series B", ["Accel", "Sequoia Capital"], False),
    ("Airtable", "501-1000", "Series F", ["Thrive Capital", "Greylock", "Coatue"], False),
    ("Discord", "501-1000", "Series H", ["Greylock", "Index Ventures", "Benchmark"], False),
    ("Waymo", "1001-5000", "Series C", ["Andreessen Horowitz", "Fidelity", "Silver Lake"], True),
    ("Figure AI", "101-250", "Series B", ["Microsoft", "NVIDIA", "OpenAI", "Bezos Expeditions"], True),
]
VCS = """Sequoia Capital|Andreessen Horowitz|Accel|Benchmark|Greylock|Index Ventures|Kleiner Perkins|Lightspeed Venture Partners
General Catalyst|Founders Fund|Thrive Capital|Khosla Ventures|NEA|Bessemer Venture Partners|Coatue|Tiger Global|ICONIQ Capital
Menlo Ventures|Spark Capital|IVP|Felicis|GV|CRV|Lux Capital|Radical Ventures|Norwest Venture Partners|Battery Ventures
Insight Partners|Greenoaks|Y Combinator|SoftBank|Salesforce Ventures|General Atlantic|Blackbird Ventures|Basis Set Ventures
Silver Lake|Eurazeo|One Peak|Addition|Valor Equity Partners|T. Rowe Price|Bezos Expeditions""".replace("\n", "|").split("|")
NON_VC_INVESTORS = {"Google", "Amazon", "Microsoft", "NVIDIA", "OpenAI", "Fidelity"}

AI_TOPICS = ["AI agents", "agent memory", "knowledge graphs and GraphRAG", "RAG and semantic search", "LLMs", "generative AI"]
OTHER_TOPICS = ["developer tools", "cloud infrastructure", "payments", "data platforms", "security", "consumer apps", "robotics"]
# Ordinary roles: title, weight, skills, job description text.
ROLES = [("Software Engineer", 22, ["Python", "Distributed Systems"], "Built backend services in Python."),
         ("Senior Software Engineer", 16, ["TypeScript", "Kubernetes"], "Built product features in TypeScript on Kubernetes."),
         ("Staff Engineer", 6, ["Go", "Distributed Systems"], "Led platform work in Golang services."),
         ("Machine Learning Engineer", 9, ["Machine Learning", "PyTorch"], "Shipped machine learning models and LLMs."),
         ("Research Scientist", 3, ["Machine Learning", "Deep Learning"], "Published research on large language models."),
         ("Data Scientist", 6, ["Python", "Statistics"], "Built machine learning models in Python."),
         ("Data Engineer", 4, ["SQL", "Spark"], "Built data pipelines in Python."),
         ("Product Manager", 10, ["Product Management", "Strategy"], "Owned product roadmap."),
         ("Senior Product Designer", 4, ["Figma", "UX Design"], "Designed product flows."),
         ("Developer Advocate", 3, ["Public Speaking", "Community Building"], "Ran developer relations programs."),
         ("Head of Community", 2, ["Community Building", "Event Planning"], "Ran community and events."),
         ("Events Manager", 2, ["Event Planning", "Marketing"], "Ran meetups and events."),
         ("Partnerships Manager", 3, ["Business Development", "Partnerships"], "Ran partnerships and alliances."),
         ("Account Executive", 6, ["Sales", "Negotiation"], "Closed enterprise accounts."),
         ("Solutions Architect", 4, ["Cloud Computing", "AWS"], "Designed customer deployments."),
         ("Marketing Manager", 5, ["Marketing", "Content Strategy"], "Ran marketing campaigns."),
         ("Recruiter", 2, ["Recruiting", "Talent Acquisition"], "Hired engineers."),
         ("Engineering Manager", 5, ["Leadership", "Mentoring"], "Led an engineering team."),
         ("Director of Engineering", 3, ["Leadership", "Strategy"], "Led several engineering teams."),
         ("Director of Product", 2, ["Product Management", "Leadership"], "Led product teams."),
         ("VP of Engineering", 1.5, ["Leadership", "Strategy"], "Led engineering."),
         ("VP of Sales", 1, ["Sales", "Leadership"], "Led sales."),
         ("VP of Marketing", 0.8, ["Marketing", "Leadership"], "Led marketing."),
         ("Podcast Host", 0.5, ["Content Creation", "Public Speaking"], "Hosted a podcast and newsletter.")]
C_SUITE = ["CTO", "COO", "CFO", "CMO", "Chief Product Officer", "Chief Revenue Officer", "Chief People Officer"]
VC_TITLES = ["Partner", "General Partner", "Managing Partner", "Principal", "Investor", "Venture Partner", "Associate", "Head of Platform"]
SKILLS_EXTRA = """Leadership|Strategy|Python|Java|JavaScript|React|Node.js|C++|Rust|AWS|Google Cloud|Azure|Docker|SQL|PostgreSQL
Redis|GraphQL|Agile|Scrum|Project Management|Analytics|Public Speaking|Fundraising|Venture Capital|Due Diligence|Angel Investing
Computer Vision|NLP|LLMs|Knowledge Graphs|Vector Databases|Prompt Engineering|Open Source|Technical Writing|Growth Marketing
SEO|Customer Success|Enterprise Sales|Product Strategy|Mentoring""".replace("\n", "|").split("|")

# Past events: (day you added many connections, name). Dates are illustrative.
PAST_EVENTS = {"2023-03-20": "NVIDIA GTC 2023", "2023-05-10": "Google I/O 2023", "2023-06-27": "Collision 2023",
               "2023-09-19": "TechCrunch Disrupt 2023", "2023-11-06": "OpenAI DevDay 2023", "2023-11-13": "Web Summit 2023",
               "2023-11-27": "AWS re:Invent 2023", "2023-12-10": "NeurIPS 2023", "2024-01-09": "CES 2024",
               "2024-03-08": "SXSW 2024", "2024-03-18": "NVIDIA GTC 2024", "2024-05-14": "Google I/O 2024",
               "2024-05-21": "Microsoft Build 2024", "2024-06-10": "Databricks Data + AI Summit 2024",
               "2024-06-25": "AI Engineer World's Fair 2024", "2024-09-17": "Dreamforce 2024", "2024-10-01": "OpenAI DevDay 2024",
               "2024-10-28": "TechCrunch Disrupt 2024", "2024-11-11": "Web Summit 2024", "2024-12-02": "AWS re:Invent 2024",
               "2024-12-10": "NeurIPS 2024", "2025-01-07": "CES 2025", "2025-03-07": "SXSW 2025", "2025-03-18": "NVIDIA GTC 2025",
               "2025-05-19": "Microsoft Build 2025", "2025-05-20": "Google I/O 2025", "2025-05-22": "Code with Claude 2025",
               "2025-06-03": "AI Engineer World's Fair 2025", "2025-06-09": "Databricks Data + AI Summit 2025",
               "2025-10-06": "OpenAI DevDay 2025", "2025-10-14": "Dreamforce 2025", "2025-10-27": "TechCrunch Disrupt 2025",
               "2025-11-10": "Web Summit 2025", "2025-12-01": "AWS re:Invent 2025", "2025-12-02": "NeurIPS 2025",
               "2026-01-06": "CES 2026", "2026-03-13": "SXSW 2026", "2026-03-16": "NVIDIA GTC 2026", "2026-05-19": "Google I/O 2026",
               "2026-05-20": "Microsoft Build 2026", "2026-06-15": "Databricks Data + AI Summit 2026",
               "2026-06-30": "AI Engineer World's Fair 2026", "2026-09-15": "Dreamforce 2026", "2026-10-13": "TechCrunch Disrupt 2026"}
# Upcoming events: name, day, city, host company, what it is about. Dates are illustrative.
UPCOMING = [("Web Summit 2026", "2026-11-09", "Lisbon", None, "AI agents, startups and founders"),
            ("AWS re:Invent 2026", "2026-11-30", "Las Vegas", "Amazon Web Services (AWS)", "AI agents and RAG on the cloud"),
            ("NeurIPS 2026", "2026-12-06", "San Diego", None, "LLMs, generative AI and retrieval research"),
            ("CES 2027", "2027-01-06", "Las Vegas", None, "generative AI in consumer tech"),
            ("NVIDIA GTC 2027", "2027-03-15", "San Jose", "NVIDIA", "AI agents, LLMs and accelerated computing"),
            ("Google Cloud Next 2027", "2027-04-20", "Las Vegas", "Google", "AI agents and generative AI on the cloud"),
            ("Stripe Sessions 2027", "2027-04-28", "San Francisco", "Stripe", "AI agents for payments"),
            ("LangChain Interrupt 2027", "2027-05-13", "San Francisco", "LangChain", "AI agents and agent memory"),
            ("Google I/O 2027", "2027-05-18", "Mountain View", "Google", "AI agents and LLMs for developers"),
            ("Microsoft Build 2027", "2027-05-18", "Seattle", "Microsoft", "AI agents for developers"),
            ("Code with Claude 2027", "2027-05-20", "San Francisco", "Anthropic", "AI agents and agent memory"),
            ("Snowflake Summit 2027", "2027-06-07", "San Francisco", "Snowflake", "RAG, retrieval and data for AI agents"),
            ("Databricks Data + AI Summit 2027", "2027-06-14", "San Francisco", "Databricks", "AI agents, RAG and knowledge graph work"),
            ("AI Engineer World's Fair 2027", "2027-06-22", "San Francisco", None, "AI agents, agent memory, GraphRAG and evals"),
            ("Vercel Ship 2027", "2027-06-24", "New York", "Vercel", "AI agents for web developers"),
            ("Dreamforce 2027", "2027-09-21", "San Francisco", "Salesforce", "AI agents for business"),
            ("OpenAI DevDay 2027", "2027-10-05", "San Francisco", "OpenAI", "AI agents and LLMs for developers"),
            ("TechCrunch Disrupt 2027", "2027-10-26", "San Francisco", None, "AI agents, startups and founders")]


def weighted(pairs):
    names, weights = zip(*pairs)
    return lambda: rnd.choices(names, weights)[0]


def build(args):
    startups = {s[0]: s for s in STARTUPS}
    ai_names = {s[0] for s in STARTUPS if s[4]} | {"Google", "Meta", "Microsoft", "NVIDIA", "Amazon Web Services (AWS)"}
    company_pool = ([(c, args.mega) for c in MEGA] + [(c, args.large) for c in LARGE]
                    + [(s[0], args.startup) for s in STARTUPS])
    all_names = [c for c, _ in company_pool]
    pick_company, pick_city = weighted(company_pool), weighted(CITIES)
    pick_school = weighted([(s, args.top_school) for s in TOP_SCHOOLS] + [(s, 1) for s in SCHOOLS])
    role_names = [r for r in ROLES]
    role_weights = [r[1] for r in ROLES]

    used = set()

    def new_person():
        while True:
            if rnd.random() < 0.1:
                name = f"{rnd.choice(OTHER_FIRST)} {rnd.choice(OTHER_LAST)}"
            else:
                name = f"{rnd.choice(FIRST)} {rnd.choice(LAST)}"
            if name not in used or len(used) > 0.8 * (len(FIRST) * len(LAST)):
                used.add(name)
                plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
                # "pb-demo-" so no link can open a real person's profile.
                slug = f"pb-demo-{plain.lower().replace(' ', '-')}-{rnd.randint(1000, 9999)}"
                return {"name": name, "slug": slug, "url": f"https://www.linkedin.com/in/{slug}"}

    people = []

    def add(company, title, skills, desc):
        p = new_person()
        p.update(company=company, title=title, skills=list(skills), desc=desc)
        people.append(p)

    # Executives and founders first, so every company has them.
    for name in all_names:
        s = startups.get(name)
        add(name, "Founder & CEO" if s else "CEO", ["Leadership", "Fundraising" if s else "Strategy"], "Leads the company.")
        if s:
            for title in rnd.sample(["Co-founder & CTO", "Co-founder & President", "Co-founder & Chief Scientist", "Co-founder"], rnd.randint(1, 3)):
                add(name, title, ["Leadership", "Python"], "Co-founded the company.")
        for title in rnd.sample(C_SUITE, rnd.randint(2, 4)):
            add(name, title, ["Leadership", "Strategy"], "Member of the executive team.")
    # Investors: people at every VC firm.
    for firm in VCS:
        add(firm, "Managing Partner", ["Venture Capital", "Due Diligence"], "Leads investments at a venture capital firm.")
        for _ in range(rnd.randint(args.vc_min, args.vc_max)):
            add(firm, rnd.choice(VC_TITLES), ["Venture Capital", "Due Diligence", "Angel Investing"][:rnd.randint(1, 3)], "Investor at a venture capital firm.")
    # Everyone else.
    while len(people) < args.people:
        role = rnd.choices(role_names, role_weights)[0]
        add(pick_company(), role[0], role[2], role[3])
    rnd.shuffle(people)
    people = people[:max(args.people, 1)]

    # Connection dates: at most 4 on a normal day, so only real events read as events (5+ on one day).
    event_days = [d for d in PAST_EVENTS if date.fromisoformat(d) < TODAY]
    start, span = date(2011, 1, 1), (TODAY - date(2011, 1, 1)).days
    per_day = {}
    event_share = 0.13
    for p in people:
        leader = p["company"] in VCS or any(w in p["title"] for w in ("Founder", "founder", "CEO", "Chief", "CTO", "COO", "CFO", "CMO"))
        if rnd.random() < (0.5 if leader else event_share):
            p["connected"] = date.fromisoformat(rnd.choice(event_days))
            continue
        for _ in range(200):
            d = start + timedelta(days=int(span * rnd.random() ** 0.7))
            if d.isoformat() not in PAST_EVENTS and per_day.get(d, 0) < 4:
                per_day[d] = per_day.get(d, 0) + 1
                p["connected"] = d
                break
        else:
            sys_day = next(start + timedelta(days=i) for i in range(span) if (start + timedelta(days=i)).isoformat() not in PAST_EVENTS and per_day.get(start + timedelta(days=i), 0) < 4)
            per_day[sys_day] = per_day.get(sys_day, 0) + 1
            p["connected"] = sys_day

    items = []
    for p in people:
        is_vc = p["company"] in VCS
        if is_vc:
            topic = "venture capital"
        elif p["company"] in ai_names:
            topic = rnd.choice(AI_TOPICS) if rnd.random() < 0.8 else rnd.choice(OTHER_TOPICS)
        else:
            topic = rnd.choice(AI_TOPICS) if rnd.random() < 0.2 else rnd.choice(OTHER_TOPICS)
        p["topic"] = topic
        headline = f"{p['title']} at {p['company']}" + (f" · {topic}" if rnd.random() < args.topic_share or is_vc else "")
        if (is_vc and rnd.random() < 0.3) or (p["title"].startswith("VP") and rnd.random() < 0.25):
            headline += " · former founder"
        cur_start = rnd.randint(2013, 2025)
        positions = [{"company": {"name": p["company"]}, "title": p["title"], "description": p["desc"] if rnd.random() < args.tech_share else "Worked on the team.",
                      "timePeriod": {"startDate": {"year": cur_start, "month": rnd.randint(1, 12)}}}]
        end = cur_start
        for _ in range(rnd.choices([0, 1, 2], args.past_jobs)[0]):
            length = rnd.randint(1, 3)
            job_start = end - length
            past = pick_company()
            if past == p["company"] or job_start < 2000:
                continue
            role = rnd.choices(role_names, role_weights)[0]
            positions.append({"company": {"name": past}, "title": role[0], "description": role[3] if rnd.random() < args.tech_share else "Worked on the team.",
                              "timePeriod": {"startDate": {"year": job_start, "month": rnd.randint(1, 12)},
                                             "endDate": {"year": end, "month": rnd.randint(1, 12)}}})
            end = job_start
        item = {"inputUrl": p["url"], "headline": headline, "summary": "", "geoLocationName": pick_city() if rnd.random() < args.city_share else "",
                "skills": [], "positions": positions, "educations": []}
        if rnd.random() < args.skill_share:
            extra = rnd.sample(SKILLS_EXTRA, rnd.randint(0, 1))
            item["skills"] = [{"name": s} for s in dict.fromkeys(p["skills"][:rnd.randint(1, 2)] + extra)]
        if rnd.random() < args.school_share:
            y = end - rnd.randint(1, 6) - 4
            item["educations"] = [{"schoolName": pick_school(), "degreeName": rnd.choice(["BS", "BA", "MS", "MBA", "PhD"]),
                                   "fieldOfStudy": rnd.choice(["Computer Science", "Economics", "Engineering", "Business", "Mathematics", "Design"]),
                                   "timePeriod": {"startDate": {"year": y}, "endDate": {"year": y + 4}}}]
        items.append(item)

    out = Path(args.out)
    linkedin, enriched = out / "linkedin", out / "enriched"
    linkedin.mkdir(parents=True, exist_ok=True)
    enriched.mkdir(parents=True, exist_ok=True)
    with open(linkedin / "Connections.csv", "w", newline="", encoding="utf-8") as f:
        f.write("Notes:\n\"Made-up demo data. Every person here is invented.\"\n\n")
        w = csv.writer(f)
        w.writerow(["First Name", "Last Name", "URL", "Email Address", "Company", "Position", "Connected On"])
        for p in people:
            first, last = p["name"].split(" ", 1)
            w.writerow([first, last, p["url"], "", p["company"], p["title"], p["connected"].strftime("%d %b %Y")])
    with open(linkedin / "Profile.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["First Name", "Last Name", "Headline", "Geo Location"])
        w.writerow(["Liz", "Zhang", "Developer Relations at FalkorDB", ""])
    with open(linkedin / "Positions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Company Name", "Title"])
        w.writerow(["FalkorDB", "Developer Relations"])
    me_url = "https://www.linkedin.com/in/pb-demo-me"
    with open(linkedin / "messages.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        dates = random.Random(11)    # its own generator, so the rest of the demo stays the same

        def sent():
            return f"{dates.randint(2023, 2026)}-{dates.randint(1, 12):02d}-{dates.randint(1, 28):02d} 12:00:00 UTC"

        w.writerow(["CONVERSATION ID", "SENDER PROFILE URL", "RECIPIENT PROFILE URLS", "DATE"])
        for i, p in enumerate(rnd.sample(people, len(people) // 6)):
            for _ in range(rnd.randint(1, 8)):
                w.writerow([f"dm-{i}", rnd.choice([me_url, p["url"]]), me_url if rnd.random() < 0.5 else p["url"], sent()])
        for g in range(40):
            members = rnd.sample(people, min(rnd.randint(3, 7), len(people)))
            w.writerow([f"group-{g}", me_url, ",".join(m["url"] for m in members), sent()])
            for m in members:
                w.writerow([f"group-{g}", m["url"], ",".join([me_url] + [x["url"] for x in members if x is not m]), sent()])
    (enriched / "batch_demo.json").write_text(json.dumps({"items": items}, ensure_ascii=False))

    companies = [{"name": s[0], "searchTerm": s[0], "employeeCount": s[1], "ipoStatus": "private", "operatingStatus": "active",
                  "numInvestors": len(s[3]), "industries": ["Artificial Intelligence" if s[4] else "Technology", "Software"],
                  "fundingRounds": [{"round": s[2], "investors": s[3], "leadInvestors": s[3][:1]}]} for s in STARTUPS]
    (enriched / "companies_demo.json").write_text(json.dumps({"items": companies}, ensure_ascii=False))

    hosts = [p for p in people if p["title"] in ("Head of Community", "Events Manager", "Developer Advocate")]
    events = []
    for i, (title, day, city, company, about) in enumerate(UPCOMING):
        host = rnd.choice(hosts) if hosts else None
        rows = [{"name": host["name"], "bio": f"{host['title']} @ {host['company']}", "socialHandles": {"linkedin": host["url"]}}] if host else []
        if company:
            rows.append({"name": company, "bio": "", "socialHandles": {}})
        events.append({"eventId": f"demo-{i}", "name": title, "description": f"A conference about {about}.",
                       "startAt": f"{day}T17:00:00Z", "timezone": "America/Los_Angeles", "hosts": rows,
                       "location": {"name": "Convention center", "city": city}, "guestCount": rnd.randint(3000, 40000),
                       "eventUrl": "", "categoryNames": ["AI", "Tech"]})
    (enriched / "events_demo.json").write_text(json.dumps({"items": events}, ensure_ascii=False))
    (out / "my_brain.json").write_text(json.dumps({"events": PAST_EVENTS, "skip_companies": []}, indent=1))
    print(f"wrote {out}: {len(people)} people, {len(all_names) + len(VCS)} companies, {len(events)} upcoming events")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--people", type=int, default=18520)
    ap.add_argument("--out", default=str(PROJECT / "data" / "demo"))
    # Knobs that set the edge count and the look of the galaxy.
    ap.add_argument("--mega", type=float, default=160)
    ap.add_argument("--large", type=float, default=3)
    ap.add_argument("--startup", type=float, default=6)
    ap.add_argument("--top-school", type=float, default=20)
    ap.add_argument("--school-share", type=float, default=0.45)
    ap.add_argument("--skill-share", type=float, default=0.3)
    ap.add_argument("--topic-share", type=float, default=0.5)
    ap.add_argument("--vc-min", type=int, default=6)
    ap.add_argument("--vc-max", type=int, default=16)
    ap.add_argument("--past-jobs", type=lambda s: [float(x) for x in s.split(",")], default=[0.7, 0.25, 0.05])
    ap.add_argument("--tech-share", type=float, default=0.25)
    ap.add_argument("--city-share", type=float, default=0.72)
    build(ap.parse_args())
