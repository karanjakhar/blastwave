# Submission form answers (copy/paste)

**Project title**
Blastwave: on-call agents that work from the graph

**Description** — What problem does your project solve, and who is it for?
When production breaks, the cause is rarely in the service that's alerting. It sits a few dependency hops away, in a deploy someone shipped 40 minutes ago, and the fix lives in the head of whoever handled the same failure months ago. On-call engineers rebuild that picture by hand from dashboards, git logs, Slack and ADRs. LLM agents don't do any better, because they see pieces, not connections, and forget everything between sessions.

Blastwave is a team of three local-LLM agents (triage → investigator → comms) for SRE and on-call teams, and it uses FalkorDB as its context layer. Triage measures the blast radius with multi-hop traversals and PageRank. The investigator follows the dependency chain downstream to find recent risky deploys, matches past incidents, and pulls the fix procedure the team recorded last time. Comms pages the on-call engineer plus the person with real hands-on history, found from tickets, past resolutions and deploys, and posts a status update. Agents hand off through the graph: each one gets only an incident id and reads the shared state back from FalkorDB. When a human resolves the incident, the fix becomes procedural memory, and the next session, run by any agent, picks it up. Each decision is shown with the exact Cypher it ran and the subgraph it relied on, highlighted live in the UI. Everything runs locally on an 8 GB laptop GPU (Qwen3-4B), and the same tools are exposed over MCP.

**Tracks**: ☑ Agents That Act on Connected Data ☑ Agent Memory and Coordination ☑ Company Brain

**Repository**: https://github.com/<you>/blastwave

**Demo video**: <YouTube link>

**Live demo**: (local; complete setup in README: `docker compose up -d falkordb`, `ollama create blastwave-qwen -f Modelfile`, `uvicorn blastwave.server:app`)

**How did you use FalkorDB in your project?**
FalkorDB is the agents' entire context layer: data, memory, and coordination.
1. Multi-hop reasoning as tools. Blast radius uses variable-length upstream `DEPENDS_ON*1..6` traversals. Root-cause search goes downstream from the symptom and joins `Deploy` nodes inside a time window, which is how it finds a bad deploy two hops away. Path explanations use `shortestPath`.
2. Graph algorithms as agent tools: `algo.pageRank` for structural criticality, `algo.betweenness` for single points of failure, `algo.labelPropagation` for service clusters.
3. Memory in the graph. Episodic memory is `Session → Episode` chains (NEXT-linked) with `MENTIONS` edges to every entity an agent touched, and chat sessions reload their history from here. Semantic memory is `Fact` nodes. Procedural memory is `Procedure -[:FIXES]-> Service, -[:LEARNED_FROM]-> Incident`, with a usage counter that's reinforced each time a fix is reused.
4. Multi-agent coordination. The open `Incident` node is the shared blackboard (severity, hypothesis, `SUSPECTED_CAUSE`, `PAGED`), and every handoff writes `(:Agent)-[:HANDED_OFF {incident, note}]->(:Agent)`. Agents are deliberately given only an id, so they have to read state from the graph.
5. Company brain: who owns, who's on call, who actually knows a system (ranked from `WORKED_ON | RESOLVED_BY | AUTHORED` evidence), and decision tracing `Decision -[:SOURCED_FROM]-> Doc | Ticket`.
6. Multi-tenancy: one graph per tenant in a single instance (`bw_acme`, `bw_globex`), tested for isolation.
7. Explainability. The LLM never writes Cypher. It picks among 19 typed, parameterized Cypher tools, and each tool returns rows plus the Cypher it ran plus the subgraph it touched, which the UI highlights. Because traversals take milliseconds, a guardrail can re-run any required check the small model skipped.
Read-only tools go through `GRAPH.RO_QUERY`.

**Blog link**: <link to BLOG.md published on dev.to / Hashnode>

**What did you like about FalkorDB?**
Variable-length traversals and `shortestPath` are fast enough to put in every step of an agent loop. Our full three-agent run issues dozens of queries, and graph time is negligible next to LLM time. Multigraph made per-tenant isolation a one-line decision, with no extra deployments. Built-in algorithms (`algo.pageRank`, `algo.betweenness`, `algo.labelPropagation`, `algo.WCC`) meant we could hand the agents tools like "find single points of failure" without writing any graph code. It's openCypher, so the queries read naturally, `GRAPH.RO_QUERY` gives a clean read-only path for agent tools, and the Docker image plus Browser had us exploring data within minutes.

**What improvements would you like to see in FalkorDB?**
- `shortestPath` only works in WITH/RETURN, not in a MATCH pattern. That surprised us coming from Cypher elsewhere, and the error message could point to the supported form.
- Algorithm signatures differ: `pageRank(label, relType)` takes positional arguments while `betweenness`/`WCC`/`labelPropagation` take a config map. Consistent config maps, and documented YIELD columns for every procedure, would help.
- Weighted / time-decayed variants of PageRank and path algorithms, so agents can weight edges by criticality or recency.
- A first-class "agent memory" recipe in the docs (episodic/semantic/procedural schemas, TTL/decay of old episodes), and native TTL on nodes would make memory pruning easy.
- Python client: a helper to convert results (Node/Edge/Path) to plain dicts/JSON. Every agent integration ends up writing one.
