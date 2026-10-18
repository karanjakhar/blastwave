"""Seed two isolated tenant graphs with a synthetic engineering org.

`acme`   - fintech with ~30 services, the main demo tenant.
`globex` - tiny tenant that proves graph-per-tenant isolation.

Story baked into `acme`: 38 minutes ago ledger-service shipped v2.14.0, which shrank the
payments-db connection pool. Checkout starts throwing 5xx two hops away. A near-identical
incident (INC-1042) happened in March and Priya resolved it; the procedure was recorded.
"""
from .db import graph, now, client, GRAPH_PREFIX

TEAMS = {
    "Platform": "Kubernetes, gateway, mesh, shared infra",
    "Payments": "Money movement, ledger, card processing",
    "Identity": "Auth, sessions, user profiles",
    "Commerce": "Checkout, cart, pricing, inventory",
    "Growth": "Notifications, search, recommendations",
    "Data": "Event bus, warehouse, analytics",
}

PEOPLE = [
    # name, handle, team, role
    ("Priya Raman", "priya", "Payments", "Staff Engineer"),
    ("Marco Silva", "marco", "Payments", "Senior Engineer"),
    ("Aisha Khan", "aisha", "Payments", "Engineering Manager"),
    ("Dev Patel", "dev", "Payments", "Engineer"),
    ("Lena Novak", "lena", "Commerce", "Senior Engineer"),
    ("Tom Becker", "tom", "Commerce", "Engineer"),
    ("Sara Lindqvist", "sara", "Commerce", "Engineering Manager"),
    ("Kenji Watanabe", "kenji", "Platform", "Principal Engineer"),
    ("Olu Adeyemi", "olu", "Platform", "SRE"),
    ("Hana Cho", "hana", "Platform", "SRE"),
    ("Ravi Iyer", "ravi", "Identity", "Senior Engineer"),
    ("Emma Dubois", "emma", "Identity", "Engineer"),
    ("Jonas Weber", "jonas", "Identity", "Engineering Manager"),
    ("Maya Torres", "maya", "Growth", "Senior Engineer"),
    ("Ali Hassan", "ali", "Growth", "Engineer"),
    ("Nora Berg", "nora", "Growth", "Engineering Manager"),
    ("Chen Wei", "chen", "Data", "Staff Engineer"),
    ("Isla Murphy", "isla", "Data", "Data Engineer"),
    ("Yusuf Demir", "yusuf", "Data", "Engineer"),
    ("Grace Okafor", "grace", "Platform", "VP Engineering"),
]

# name, kind, team, tier (1 = customer facing), on_call handle
SERVICES = [
    ("web-frontend", "service", "Commerce", 1, "tom"),
    ("mobile-bff", "service", "Commerce", 1, "lena"),
    ("api-gateway", "service", "Platform", 1, "olu"),
    ("checkout-service", "service", "Commerce", 1, "lena"),
    ("cart-service", "service", "Commerce", 2, "tom"),
    ("pricing-service", "service", "Commerce", 2, "tom"),
    ("inventory-service", "service", "Commerce", 2, "lena"),
    ("inventory-db", "datastore", "Commerce", 2, "lena"),
    ("payments-api", "service", "Payments", 1, "marco"),
    ("ledger-service", "service", "Payments", 1, "dev"),
    ("payments-db", "datastore", "Payments", 1, "dev"),
    ("fraud-scorer", "service", "Payments", 2, "marco"),
    ("card-processor", "service", "Payments", 1, "marco"),
    ("auth-service", "service", "Identity", 1, "ravi"),
    ("user-service", "service", "Identity", 2, "emma"),
    ("session-store", "datastore", "Identity", 1, "ravi"),
    ("users-db", "datastore", "Identity", 2, "emma"),
    ("notification-service", "service", "Growth", 3, "ali"),
    ("email-worker", "service", "Growth", 3, "ali"),
    ("sms-worker", "service", "Growth", 3, "ali"),
    ("search-service", "service", "Growth", 2, "maya"),
    ("search-index", "datastore", "Growth", 2, "maya"),
    ("recommendation-service", "service", "Growth", 3, "maya"),
    ("feature-store", "datastore", "Data", 3, "isla"),
    ("event-bus", "datastore", "Data", 1, "chen"),
    ("analytics-pipeline", "service", "Data", 3, "yusuf"),
    ("warehouse", "datastore", "Data", 3, "yusuf"),
    ("config-service", "service", "Platform", 1, "hana"),
    ("service-mesh", "service", "Platform", 1, "kenji"),
]

# caller -> callee
DEPS = [
    ("web-frontend", "api-gateway"), ("mobile-bff", "api-gateway"),
    ("api-gateway", "auth-service"), ("api-gateway", "checkout-service"), ("api-gateway", "search-service"),
    ("api-gateway", "cart-service"), ("api-gateway", "user-service"), ("api-gateway", "recommendation-service"),
    ("checkout-service", "cart-service"), ("checkout-service", "pricing-service"),
    ("checkout-service", "inventory-service"), ("checkout-service", "payments-api"),
    ("checkout-service", "notification-service"),
    ("cart-service", "pricing-service"), ("cart-service", "session-store"),
    ("inventory-service", "inventory-db"),
    ("payments-api", "ledger-service"), ("payments-api", "fraud-scorer"), ("payments-api", "card-processor"),
    ("ledger-service", "payments-db"), ("ledger-service", "event-bus"),
    ("fraud-scorer", "feature-store"), ("fraud-scorer", "user-service"),
    ("card-processor", "config-service"),
    ("auth-service", "session-store"), ("auth-service", "user-service"),
    ("user-service", "users-db"),
    ("notification-service", "email-worker"), ("notification-service", "sms-worker"),
    ("notification-service", "event-bus"),
    ("search-service", "search-index"),
    ("recommendation-service", "feature-store"), ("recommendation-service", "search-service"),
    ("analytics-pipeline", "event-bus"), ("analytics-pipeline", "warehouse"),
    ("feature-store", "warehouse"),
    ("api-gateway", "service-mesh"), ("payments-api", "config-service"), ("checkout-service", "config-service"),
]

# minutes_ago, service, version, author, summary, risky
DEPLOYS = [
    (38, "ledger-service", "v2.14.0", "dev", "Refactor DB client; set pool max_connections=10 (was 50)", True),
    (95, "search-service", "v5.2.1", "maya", "Bump analyzer for typo tolerance", False),
    (180, "pricing-service", "v3.8.0", "tom", "Add regional tax rules", False),
    (240, "fraud-scorer", "v1.30.2", "marco", "Model weights refresh", False),
    (300, "web-frontend", "v9.1.0", "tom", "New checkout button styles", False),
    (420, "auth-service", "v4.4.4", "emma", "Rotate JWT signing key", False),
    (600, "notification-service", "v2.0.7", "ali", "Retry backoff tweak", False),
    (1440, "payments-api", "v7.3.0", "marco", "Idempotency key TTL 24h", False),
    (2000, "analytics-pipeline", "v0.9.3", "yusuf", "Partition by day", False),
    (2880, "config-service", "v1.12.0", "hana", "Hot reload for feature flags", False),
    (60 * 24 * 210, "ledger-service", "v2.9.0", "marco", "Switch ORM; pool sized from env default", True),
    (60 * 24 * 120, "auth-service", "v4.1.0", "ravi", "Session TTL change", True),
    (60 * 24 * 60, "search-service", "v5.0.0", "maya", "Reindex with new mapping", True),
]

# id, title, sev, days_ago, root-cause deploy (service, version), affected, resolved_by, summary
INCIDENTS = [
    ("INC-1042", "Checkout failures: payments-db connection pool exhausted", "SEV1", 210,
     ("ledger-service", "v2.9.0"), ["checkout-service", "payments-api", "ledger-service", "payments-db"], "priya",
     "ledger-service v2.9.0 picked up default pool size 5; under peak, payments-api timed out and checkout returned 503s. "
     "Rolled back ledger-service, then pinned DB_POOL_MAX=50."),
    ("INC-1077", "Login storm after session TTL change", "SEV2", 120,
     ("auth-service", "v4.1.0"), ["auth-service", "session-store", "api-gateway"], "ravi",
     "Session TTL shortened to 5m caused refresh storm on session-store. Reverted TTL."),
    ("INC-1103", "Search returns empty results", "SEV2", 60,
     ("search-service", "v5.0.0"), ["search-service", "search-index", "recommendation-service"], "maya",
     "New mapping dropped the title field. Reindexed from snapshot."),
    ("INC-0988", "Event bus partition lag", "SEV3", 300, None, ["event-bus", "analytics-pipeline"], "chen",
     "Consumer group rebalance loop. Increased session.timeout.ms."),
]

PROCEDURES = [
    ("PROC-1", "Payments DB pool exhaustion", "ledger-service", "INC-1042",
     ["Check ledger-service deploys in last 2h", "Roll back ledger-service to last good version",
      "Verify payments-db active connections < 80% of max", "Pin DB_POOL_MAX=50 in config-service"]),
    ("PROC-2", "Session refresh storm", "auth-service", "INC-1077",
     ["Revert session TTL in auth-service config", "Scale session-store read replicas"]),
]

RUNBOOKS = [
    ("RB-payments-db", "payments-db runbook", "payments-db",
     "Max connections 200. Ledger owns 50 by default. Alert at 80% utilisation. Failover is manual: page Payments on-call."),
    ("RB-checkout", "checkout-service runbook", "checkout-service",
     "5xx usually upstream: check payments-api, inventory-service, pricing-service health before restarting."),
    ("RB-auth", "auth-service runbook", "auth-service", "If login latency spikes, check session-store memory and TTL settings."),
]

DOCS = [
    ("DOC-ADR-17", "ADR-17: Ledger owns its connection pool", "Ledger service sizes its own pool; must stay >= 50 at peak."),
    ("DOC-PM-1042", "Postmortem INC-1042", "Pool exhaustion after ORM switch. Action item: pool size must be explicit, not env default."),
    ("DOC-ADR-22", "ADR-22: Event bus is the source of truth for analytics", "All analytics read from event-bus, never from service DBs."),
]
TICKETS = [
    ("PAY-311", "Make ledger pool size explicit", "ledger-service", ["priya", "dev"]),
    ("PAY-402", "Ledger DB client refactor", "ledger-service", ["dev"]),
    ("COM-88", "Checkout retries on payments timeout", "checkout-service", ["lena", "priya"]),
    ("IDN-57", "Session TTL tuning", "auth-service", ["ravi", "emma"]),
    ("GRO-19", "Search mapping migration", "search-service", ["maya", "ali"]),
    ("DAT-40", "Partition analytics by day", "analytics-pipeline", ["yusuf", "chen"]),
    ("PLT-203", "Feature-flag hot reload", "config-service", ["hana", "kenji"]),
]
DECISIONS = [
    ("DEC-1", "Ledger pool must be >= 50 connections", "ledger-service", ["DOC-ADR-17", "DOC-PM-1042"], ["PAY-311"], "aisha"),
    ("DEC-2", "Analytics reads only from event-bus", "analytics-pipeline", ["DOC-ADR-22"], ["DAT-40"], "chen"),
    ("DEC-3", "Checkout retries payments once with idempotency key", "checkout-service", [], ["COM-88"], "sara"),
]


def reset(tenant: str):
    name = GRAPH_PREFIX + tenant
    if name in client().list_graphs():
        graph(tenant).delete()


def seed_acme():
    reset("acme")
    g = graph("acme")
    t = now()
    q = g.query
    for idx in ["Service(name)", "Person(handle)", "Team(name)", "Incident(id)", "Deploy(id)", "Agent(name)",
                "Session(id)", "Procedure(id)", "Fact(key)"]:
        label, prop = idx.rstrip(")").split("(")
        q(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
    q("UNWIND $rows AS r CREATE (:Team {name: r[0], charter: r[1]})", {"rows": [[k, v] for k, v in TEAMS.items()]})
    q("""UNWIND $rows AS r MATCH (t:Team {name: r[2]})
         CREATE (p:Person {name: r[0], handle: r[1], role: r[3]})-[:MEMBER_OF]->(t)""", {"rows": PEOPLE})
    q("""UNWIND $rows AS r MATCH (t:Team {name: r[2]}), (p:Person {handle: r[4]})
         CREATE (s:Service {name: r[0], kind: r[1], tier: r[3]})
         CREATE (t)-[:OWNS]->(s), (p)-[:ON_CALL]->(s)""", {"rows": SERVICES})
    q("""UNWIND $rows AS r MATCH (a:Service {name: r[0]}), (b:Service {name: r[1]})
         CREATE (a)-[:DEPENDS_ON]->(b)""", {"rows": DEPS})
    q("""UNWIND $rows AS r MATCH (s:Service {name: r[1]}), (p:Person {handle: r[3]})
         CREATE (d:Deploy {id: r[1] + '@' + r[2], version: r[2], ts: $t - r[0] * 60, summary: r[4], risky: r[5]})
         CREATE (d)-[:OF]->(s), (p)-[:AUTHORED]->(d)""", {"rows": DEPLOYS, "t": t})
    for iid, title, sev, days, cause, affected, resolver, summary in INCIDENTS:
        q("""MATCH (p:Person {handle: $resolver})
             CREATE (i:Incident {id: $id, title: $title, severity: $sev, ts: $ts, status: 'resolved', summary: $summary})
             CREATE (i)-[:RESOLVED_BY]->(p)
             WITH i UNWIND $affected AS a MATCH (s:Service {name: a}) CREATE (i)-[:AFFECTED]->(s)""",
          {"id": iid, "title": title, "sev": sev, "ts": t - days * 86400, "summary": summary,
           "resolver": resolver, "affected": affected})
        if cause:
            q("MATCH (i:Incident {id: $id}), (d:Deploy {id: $d}) CREATE (i)-[:CAUSED_BY]->(d)",
              {"id": iid, "d": f"{cause[0]}@{cause[1]}"})
    q("""UNWIND $rows AS r MATCH (s:Service {name: r[2]}), (i:Incident {id: r[3]})
         CREATE (p:Procedure {id: r[0], title: r[1], steps: r[4], uses: 1, learned_by: 'human'})
         CREATE (p)-[:FIXES]->(s), (p)-[:LEARNED_FROM]->(i)""", {"rows": PROCEDURES})
    q("""UNWIND $rows AS r MATCH (s:Service {name: r[2]})
         CREATE (:Runbook {id: r[0], title: r[1], body: r[3]})-[:FOR]->(s)""", {"rows": RUNBOOKS})
    q("UNWIND $rows AS r CREATE (:Doc {id: r[0], title: r[1], body: r[2]})", {"rows": DOCS})
    for tid, title, svc, people in TICKETS:
        q("""MATCH (s:Service {name: $svc}) CREATE (k:Ticket {id: $id, title: $title})-[:TOUCHES]->(s)
             WITH k UNWIND $people AS h MATCH (p:Person {handle: h}) CREATE (p)-[:WORKED_ON]->(k)""",
          {"id": tid, "title": title, "svc": svc, "people": people})
    for did, title, svc, docs, tickets, owner in DECISIONS:
        q("""MATCH (s:Service {name: $svc}), (o:Person {handle: $owner})
             CREATE (d:Decision {id: $id, title: $title})-[:ABOUT]->(s), (o)-[:DECIDED]->(d)
             WITH d UNWIND $docs AS x MATCH (doc:Doc {id: x}) CREATE (d)-[:SOURCED_FROM]->(doc)""",
          {"id": did, "title": title, "svc": svc, "docs": docs or ["__none__"], "owner": owner})
        q("""MATCH (d:Decision {id: $id}) UNWIND $tickets AS x MATCH (k:Ticket {id: x})
             CREATE (d)-[:SOURCED_FROM]->(k)""", {"id": did, "tickets": tickets})
    q("MATCH (d:Doc {id: 'DOC-PM-1042'}), (i:Incident {id: 'INC-1042'}) CREATE (d)-[:DOCUMENTS]->(i)")
    q("""UNWIND ['triage', 'investigator', 'comms'] AS a CREATE (:Agent {name: a})""")
    # Live alert that kicks off the demo.
    q("""MATCH (s:Service {name: 'checkout-service'})
         CREATE (:Alert {id: 'ALERT-7731', title: 'checkout-service 5xx rate 18% (SLO 1%)', ts: $t - 300, status: 'firing'})
               -[:FIRED_ON]->(s)""", {"t": t})


def seed_globex():
    reset("globex")
    g = graph("globex")
    g.query("""CREATE (t:Team {name: 'Core'}),
                      (p:Person {name: 'Hank Scorpio', handle: 'hank', role: 'CEO'})-[:MEMBER_OF]->(t),
                      (a:Service {name: 'doomsday-api', kind: 'service', tier: 1}),
                      (b:Service {name: 'volcano-db', kind: 'datastore', tier: 1}),
                      (a)-[:DEPENDS_ON]->(b), (t)-[:OWNS]->(a), (t)-[:OWNS]->(b), (p)-[:ON_CALL]->(a), (p)-[:ON_CALL]->(b)""")
    g.query("UNWIND ['triage', 'investigator', 'comms'] AS a CREATE (:Agent {name: a})")


def main():
    seed_acme()
    seed_globex()
    for t in ("acme", "globex"):
        n = graph(t).query("MATCH (n) RETURN count(n)").result_set[0][0]
        e = graph(t).query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
        print(f"seeded {t}: {n} nodes, {e} edges")


if __name__ == "__main__":
    main()
