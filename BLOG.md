# The root cause is two hops away: building on-call agents on a graph

*How I built Blastwave for FalkorDB Graph Hacks: three local-LLM agents that triage, investigate and page, using FalkorDB as their context, memory and shared state.*

## 3 a.m., again

The alert says `checkout-service` is returning 5xx errors at 18%. Nothing changed in checkout. You open dashboards, scroll Slack, check deploy logs for five services, and twenty minutes later find it: `ledger-service` shipped a DB client refactor 38 minutes ago and quietly dropped its connection pool from 50 to 10. Checkout calls payments-api, and payments-api calls ledger, so the failure surfaced two hops from where it started.

What makes it worse is that **this already happened in March**. Priya on the Payments team fixed it. There's a postmortem and an ADR that says "ledger pool must stay ≥ 50". Nobody found any of it during the incident, because the relevant facts weren't in any single record. They were spread across the links between records.

That's the problem with putting an LLM agent on top of an incident: it sees pieces, not connections, and it forgets everything once the session ends.

## Put the context in a graph

Each question an on-call engineer asks is a traversal:

| Question | Graph pattern |
|---|---|
| What breaks if this goes down? | `(:Service)-[:DEPENDS_ON*1..6]->(x)` |
| What changed near the symptom? | `(symptom)-[:DEPENDS_ON*0..5]->(:Service)<-[:OF]-(:Deploy)` in a time window |
| Has this happened before? | `(:Incident)-[:AFFECTED]->(…neighbourhood…)` |
| How did we fix it? | `(:Procedure)-[:FIXES]->(:Service)` |
| Who actually knows this system? | `(:Person)-[:WORKED_ON|RESOLVED_BY|AUTHORED]-()-[...]->(:Service)` |
| Why is it configured like this? | `(:Decision)-[:SOURCED_FROM]->(:Doc|:Ticket)` |

So I modelled a small company in FalkorDB: teams, people, 29 services and datastores, dependencies, deploys, incidents, runbooks, tickets, ADRs and decisions. Then I gave a team of agents tools that each run one of those traversals.

## Three agents, one blackboard

Blastwave runs a pipeline of three agents:

1. **Triage** sizes the blast radius (upstream traversal plus PageRank), checks memory, and sets severity.
2. **Investigator** walks *downstream* from the symptom looking for recent risky deploys, matches past incidents, and pulls the recorded fix procedure.
3. **Comms** works out who to page (on-call, plus the strongest expert by evidence), pages them, and posts a status update.

The design choice I'm happiest with is that **agents don't pass findings to each other in prompts.** Each agent gets only an incident id. It writes its conclusions to the shared `Incident` node (`severity`, `hypothesis`, `-[:SUSPECTED_CAUSE]->(:Deploy)`, `-[:PAGED]->(:Person)`) and records a `(:Agent)-[:HANDED_OFF {note}]->(:Agent)` edge. The next agent calls `incident_state` and reads everything back from FalkorDB.

This sounds like ceremony, but it gives you three things for free:
- **Any agent can resume any incident**, in this session or next week, because state never lived in a prompt.
- **The handoff chain is queryable**: `MATCH (a)-[h:HANDED_OFF {incident:$id}]->(b) RETURN a, b, h.note`.
- **Humans see the same state** the agents see.

## Memory that compounds

Memory lives in the same graph, in three kinds:

- **Episodic**: every agent turn becomes an `Episode`, NEXT-chained inside its `Session` and linked with `MENTIONS` to every service, person or incident it touched. Ask "what do we remember about ledger-service?" and you get episodes from *every* past session in a single hop.
- **Semantic**: `Fact` nodes the chat agent stores when you tell it something.
- **Procedural**: when a human resolves an incident, the fix becomes a `Procedure -[:FIXES]-> Service -[:LEARNED_FROM]-> Incident`, or, if one already exists, its `uses` counter goes up. Next time, the investigator finds it via `procedures_for`, which also searches downstream dependencies.

Because memory is attached to the same nodes as the operational data, recall doesn't need embeddings or a separate vector store. It's just graph neighbourhood.

## Small model, honest guardrails

I wanted it to run fully local: Qwen3-4B on an 8 GB laptop GPU. Small models are good at choosing a tool and bad at writing correct Cypher against a schema they've never seen, so:

- **The LLM never writes Cypher.** It picks from 19 typed tools, each a parameterized query.
- **Each tool returns evidence**: the rows, the exact Cypher and params, and the subgraph it touched. The UI highlights that subgraph live, and you can click any step to replay it.
- **Guardrails**: each agent declares the checks it *must* run. If the model skips one (4B models do), the runtime runs it with arguments taken from the graph, feeds the result back, and labels that step `guardrail` in the trace. You can always tell what the model did from what the system enforced.

Graph queries take milliseconds, so this costs essentially nothing. A full three-agent run issues dozens of queries, and FalkorDB time doesn't register next to LLM time.

## Things I learned about FalkorDB

- `shortestPath` goes in `WITH`/`RETURN`, not in `MATCH`: `WITH shortestPath((a)-[:DEPENDS_ON*1..8]->(b)) AS p`.
- Algorithms are built in and very useful as agent tools: `algo.pageRank('Service','DEPENDS_ON')`, `algo.betweenness({...})` (single points of failure), `algo.labelPropagation({...})`, `algo.WCC`.
- Multigraph made multi-tenancy trivial: `bw_acme` and `bw_globex` sit in one instance with full isolation.
- `GRAPH.RO_QUERY` (`graph.ro_query` in Python) is a clean way to guarantee read-only agent tools.

## Try it

Everything is local: `docker compose up -d falkordb`, `ollama create blastwave-qwen -f Modelfile`, `python -m blastwave.seed`, `uvicorn blastwave.server:app`. Then click **Run triage → investigate → comms** and watch the graph light up from checkout to ledger to the deploy that caused it, and to the person who knows how to fix it.

Repo: https://github.com/karanjakhar/blastwave · Demo: <YouTube link>
