// THROWAWAY SPIKE: demo queries for the FalkorDB Browser (http://localhost:3000, graph "network").
// Paste one query at a time into the query bar and press RUN.
// Tip: Settings > Query Execution > Limit is set to 5000 so big queries show everything.

// 1. WOW SHOT: my whole "who knows whom" web (people linked by shared jobs, schools, group chats)
MATCH (m:Me)-[c:CONNECTED]->(p:Person)-[k:KNOWS]->(q:Person) RETURN m,c,p,k,q

// 2. "Where did I meet them?" Everyone I met at my biggest event, and where they work now
MATCH (:Me)-[:ATTENDED]->(top:Event) WITH top ORDER BY top.people DESC LIMIT 1
MATCH (m:Me)-[a:ATTENDED]->(top)<-[r:MET_AT]-(p:Person)-[w:WORKED_AT {current:true}]->(c:Company)
RETURN m,a,top,r,p,w,c

// 3. WARM INTROS: events / community / partnerships people I never talked to,
//    reachable through a friend I message often (3+ messages)
MATCH (m:Me)-[c:CONNECTED]->(f:Person)-[k:KNOWS]-(t:Person {event_contact:true})-[w:WORKED_AT {current:true}]->(co:Company)
WHERE f.messages >= 3 AND t.messages = 0
RETURN m,c,f,k,t,w,co

// 4. PODCAST GUESTS: founders who work on agents, agent memory or knowledge graphs
MATCH (t:Topic)<-[i:INTO]-(p:Person)-[:INTO]->(:Topic {name:'Founder'})
WHERE t.name IN ['Agent Memory', 'Knowledge Graphs', 'AI Agents']
MATCH (p)-[w:WORKED_AT {current:true}]->(c:Company)
RETURN t,i,p,w,c

// 5. SPONSOR / CO-HOST TARGETS: companies where I know event, community or partnership people
MATCH (p:Person {event_contact:true})-[w:WORKED_AT {current:true}]->(c:Company)
WITH c, collect(p) AS people, collect(w) AS rels
WHERE size(people) >= 2
UNWIND range(0, size(people) - 1) AS i
RETURN c, people[i], rels[i]

// 6. WHO CAN GET ME INTO A COMPANY? Friends I talk to who know someone there (change the name)
MATCH (m:Me)-[c:CONNECTED]->(f:Person)-[k:KNOWS]-(t:Person)-[w:WORKED_AT {current:true}]->(co:Company {name:'OpenAI'})
WHERE f.messages >= 1
RETURN m,c,f,k,t,w,co

// 7. WHO CAN I MEET ON MY TRIP? People in a city and where they work now (change the city)
MATCH (m:Me)-[c:CONNECTED]->(p:Person)-[l:LIVES_IN]->(city:City {name:'Berlin'})
MATCH (p)-[w:WORKED_AT {current:true}]->(co:Company)
RETURN m,c,p,l,city,w,co
