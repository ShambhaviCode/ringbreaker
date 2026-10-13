# RingBreaker — Master Prompt

> Put this file in the root of your project folder. Claude in VS Code reads `CLAUDE.md` automatically.
> For other coding assistants, copy it to `AGENTS.md` too, or paste it as the first message.
> Rename "RingBreaker" freely.

---

## 1. Who you are and what we're doing

You are my senior engineer and hackathon partner. We are building **RingBreaker** for the
**Graph Hacks: Context for AI Agents** hackathon (powered by FalkorDB), Oct 15–18, 2026, deadline Oct 18 11:59 PM IST.

We are entering **all three tracks with one project**, but **Track 01 is the priority**:

| Priority | Track | Prize | Our feature |
|---|---|---|---|
| 1 (must be excellent) | 01 Agents That Act on Connected Data | iPhone 18 Pro | Investigator agent traces scam rings with graph algorithms via MCP and takes actions |
| 2 | 02 Agent Memory and Coordination | PS5 | 3 agents hand off cases through shared graph memory; memory persists across sessions; separate graph per tenant |
| 3 | 03 Company Brain | M6 Mac Mini | Ask questions about the fraud team's work: who decided what, under which policy, with sources |

**One-line pitch:** A team of AI agents that takes a scam report (UPI ID, phone number, or link),
uncovers the whole scam ring on a FalkorDB graph, freezes the mule accounts, and shows the exact
path of evidence behind every decision — while remembering every victim and every case.

---

## 2. Hard rules (follow these on every task)

1. **The graph must make the decisions.** Every agent answer and action must come from a graph query
   or graph algorithm result. Never let the LLM guess facts that aren't in the graph.
2. **Every action is explainable.** Each freeze/alert/report stores the evidence path (node IDs + edges)
   and the Cypher query that produced it. The UI must be able to display that path.
3. **Track 01 first.** Never start Track 02 or 03 work while the Track 01 flow is broken.
4. **Synthetic data only.** No real people, phone numbers, UPI handles, or bank names. Use clearly
   fake handles like `@fakepay`, `@demobank`, and numbers like `+91-90000-XXXXX`.
5. **Verify FalkorDB syntax before relying on it.** FalkorDB's Cypher and algorithm procedure names
   differ from Neo4j. Check the FalkorDB docs and run `CALL dbms.procedures()` to confirm which
   `algo.*` procedures exist (e.g. PageRank, WCC/connected components, label propagation / community
   detection, BFS). If a procedure is missing, implement a fallback with plain Cypher and say so.
6. **Small, working steps.** After each step: run it, show me the output, then commit with a clear
   message. Don't write big untested chunks.
7. **Ask before big changes** to the schema, stack, or folder structure. Otherwise just build.
8. **Keep secrets in `.env`**, never in code. Provide `.env.example`.
9. Explain briefly what you did after each task, in simple English. I'm learning as I go.

---

## 3. Tech stack

- **Graph DB:** FalkorDB in Docker
  `docker run -p 6379:6379 -p 3000:3000 -it --rm falkordb/falkordb:latest`
  (FalkorDB Browser on http://localhost:3000)
- **Language:** Python 3.11+
- **Graph client:** `falkordb` Python package
- **MCP:** our own MCP server built with the official Python `mcp` SDK (FastMCP), exposing graph
  queries and algorithms as tools. Also check whether the official FalkorDB MCP server is useful;
  ours stays the main one because the tools are domain-specific.
- **Agents:** plain Python tool-calling loop that connects to our MCP server as a client
  (no heavy framework unless it clearly saves time).
- **LLM:** configurable in `.env` — support Anthropic API and any OpenAI-compatible endpoint
  (so I can use whatever free/cheap provider I have). One `llm.py` wrapper.
- **UI:** Streamlit + an interactive graph view (pyvis or streamlit-agraph) that highlights evidence paths.
- **Deps:** `requirements.txt`, plus a `Makefile` or `run.sh` with: `up`, `seed`, `mcp`, `app`.

---

## 4. Repo structure

```
ringbreaker/
  CLAUDE.md
  README.md
  .env.example
  requirements.txt
  docker-compose.yml
  data/
    generate.py          # synthetic data generator (seeded, reproducible)
  graph/
    schema.py            # indexes, constraints
    load.py              # loads generated data into FalkorDB
    queries.py           # all Cypher lives here, named functions
  mcp_server/
    server.py            # FastMCP server exposing tools
  agents/
    llm.py               # provider wrapper
    intake.py            # Track 02
    investigator.py      # Track 01 core
    action.py            # Track 01 actions
    brain.py             # Track 03 Q&A
    orchestrator.py      # handoffs between agents
  app/
    streamlit_app.py     # UI
  tests/
  docs/
    architecture.png
    blog_post.md
```

---

## 5. Graph schema

Use one graph per tenant (bank): graph name `bank_<tenant_id>`. Every node has `id` (string, unique) and `created_at`.

### Layer A — Fraud (Track 01)
Nodes:
- `Account {id, holder_name, bank, opened_at, status: active|frozen, risk_score}`
- `UPI {id, handle}`
- `Phone {id, number}`
- `Device {id, fingerprint, os}`
- `IP {id, address, city}`
- `Link {id, url, domain}`
- `Transaction {id, amount, ts, channel}`
- `ScamReport {id, ts, scam_type, description, reporter_id}`

Relationships:
- `(:UPI)-[:LINKED_TO]->(:Account)`
- `(:Account)-[:REGISTERED_PHONE]->(:Phone)`
- `(:Account)-[:USED_DEVICE {last_seen}]->(:Device)`
- `(:Account)-[:LOGGED_IN_FROM {ts}]->(:IP)`
- `(:Account)-[:SENT]->(:Transaction)-[:TO]->(:Account)`
- `(:ScamReport)-[:MENTIONS]->(:UPI|:Phone|:Link|:Account)`
- `(:Link)-[:PROMOTED_BY]->(:Phone)`

### Layer B — Memory (Track 02)
Nodes:
- `User {id, name, language}` (the victim / reporter)
- `Session {id, started_at, ended_at}`
- `Message {id, role, text, ts}`
- `Case {id, status: open|investigating|actioned|closed, opened_at, summary}`
- `Agent {id, name: intake|investigator|action|brain}`
- `Memory {id, kind: episodic|semantic|procedural, content, ts, confidence}`
- `ActionTaken {id, type: freeze|alert|complaint, ts, evidence_path, cypher}`

Relationships:
- `(:User)-[:HAD_SESSION]->(:Session)-[:CONTAINS]->(:Message)`
- `(:User)-[:FILED]->(:ScamReport)`
- `(:ScamReport)-[:OPENED]->(:Case)`
- `(:Case)-[:ASSIGNED_TO {ts}]->(:Agent)`
- `(:Agent)-[:HANDED_OFF {ts, note}]->(:Agent)` (store handoff on the Case too)
- `(:Agent)-[:LEARNED]->(:Memory)-[:ABOUT]->(any node)`
- `(:Case)-[:RESULTED_IN]->(:ActionTaken)-[:TARGETS]->(:Account)`
- `(:Case)-[:PART_OF_RING {ring_id}]` → store ring membership as `(:Account)-[:MEMBER_OF]->(:Ring {id, size, detected_at})`

Memory kinds:
- **episodic:** "User Priya reported UPI X on Oct 15"
- **semantic:** "Device D17 is shared by ring R3"
- **procedural:** "For job-scam reports, check Link → Phone → Account first" (learned investigation playbooks)

### Layer C — Company (Track 03)
Nodes:
- `Analyst {id, name, role}`; `Team {id, name}`
- `Policy {id, code, title, text, effective_from, superseded_by}`
- `Decision {id, ts, outcome, reason}`
- `Document {id, title, source: doc|ticket|chat, url, text, ts}`

Relationships:
- `(:Analyst)-[:MEMBER_OF]->(:Team)`
- `(:Analyst)-[:OWNS]->(:Case)` ; `(:Analyst)-[:EXPERT_IN]->(:ScamType {name})`
- `(:Decision)-[:ABOUT]->(:Case)`, `(:Decision)-[:MADE_BY]->(:Analyst|:Agent)`,
  `(:Decision)-[:APPROVED_BY]->(:Analyst)`, `(:Decision)-[:UNDER_POLICY]->(:Policy)`,
  `(:Decision)-[:SOURCED_FROM]->(:Document)`
- `(:Policy)-[:SUPERSEDES]->(:Policy)` (knowledge kept current)

---

## 6. Synthetic data (`data/generate.py`)

Seeded and reproducible (`--seed 42`). Indian-style fake names. Sizes (configurable):
- ~2,000 normal accounts with normal transactions (background noise)
- **5 planted scam rings**, 6–20 mule accounts each, with realistic patterns:
  - shared devices / IPs across mule accounts
  - fan-in: many victims → mules → one **collector** account (ringleader, should top PageRank)
  - layering: money hops 2–4 accounts before the collector
  - scam types: job scam, KYC update scam, investment/crypto scam, fake customer care, loan app
- ~60 scam reports from victims, some mentioning UPIs, some phones, some links
- Company layer: 8 analysts, 2 teams, 6 policies (one superseded), ~30 past decisions with source documents (tickets/chat snippets)

**Planted demo scenarios (must exist, with fixed IDs so the demo is repeatable):**
1. **Ring R1 (job scam):** reported UPI `hr.jobs2026@fakepay` → 3 hops via shared device `D17` → 12 mules → collector `ACC-7781`.
2. **Returning victim:** user `Priya S.` reported on "Oct 15"; returns "Oct 16" with a new phone number that belongs to the same ring R1.
3. **Company question:** account `ACC-5530` was frozen in past case `C-112` by analyst `Arjun M.`, approved by `Meera K.`, under policy `P-07`, sourced from ticket `TKT-4471`. Policy `P-07` was later superseded by `P-09`.

---

## 7. MCP tools (`mcp_server/server.py`)

Each tool returns JSON with `result`, `evidence` (nodes + edges), and `cypher` used.

Track 01:
- `lookup_entity(value)` → find UPI/phone/link/account by value
- `expand_neighbors(node_id, hops=1..4, rel_types=[...])` → multi-hop neighborhood
- `find_shared_infrastructure(account_ids)` → shared devices/IPs/phones
- `detect_ring(seed_node_id)` → community detection / connected components around a seed
- `rank_ring_members(ring_id)` → PageRank inside the ring, find the collector
- `money_trail(from_id, to_id)` → shortest path through transactions
- `risk_score(account_id)` → score from graph features (shared devices, reports, fan-in)
- `freeze_accounts(account_ids, case_id, reason)` → simulated action; writes `ActionTaken` with evidence path
- `send_alert(user_ids, message)` → warns other victims who paid the same ring
- `draft_complaint(case_id)` → generates a cyber-crime complaint draft from the graph evidence

Track 02:
- `recall_user(user_id)` → past sessions, reports, cases, episodic memories
- `write_memory(agent_id, kind, content, about_node_id)`
- `get_playbook(scam_type)` → procedural memory
- `handoff(case_id, from_agent, to_agent, note)`
- `get_case_state(case_id)`

Track 03:
- `who_owns(case_id | scam_type)`, `who_knows(scam_type)`
- `trace_decision(decision_id | account_id)` → decision → analyst → approver → policy → source doc
- `current_policy(topic)` → follows `SUPERSEDES` to the latest policy
- `search_documents(query)` → keyword search over Document text, returns sources

---

## 8. Agents

- **Intake agent (Track 02):** talks to the victim, calls `recall_user` first, creates the report and case,
  writes episodic memory, hands off to Investigator.
- **Investigator agent (Track 01 core):** uses `get_playbook`, then chooses tools itself via MCP:
  lookup → expand → shared infra → detect ring → rank → money trail. Produces a case summary
  with the evidence path. Writes semantic memory (e.g. "device D17 belongs to ring R1") and updates
  the playbook if a new pattern worked (procedural memory). Hands off to Action.
- **Action agent (Track 01):** freezes accounts above a risk threshold, alerts other victims, drafts
  the complaint. Every action stores the evidence path and cypher.
- **Brain agent (Track 03):** answers questions about cases, owners, decisions, and policies with sources.
- **Orchestrator:** routes messages, logs every handoff to the graph. Agents must handle concurrent
  cases safely (use one graph connection per agent; test with several cases at once).

System prompt rule for every agent: *"Only state facts returned by tools. Cite the node IDs and the path for every claim. If the graph has no answer, say so."*

---

## 9. UI (`app/streamlit_app.py`)

Tabs:
1. **Report a scam** — chat with the Intake agent; live agent activity log (which agent, which tool, which Cypher).
2. **Investigation** — interactive graph; the ring lights up; the evidence path is highlighted; frozen accounts turn red; side panel shows "Why was this frozen?" with the path in plain English.
3. **Memory** — timeline for a user across sessions; handoffs between agents; memories learned.
4. **Ask the fraud team brain** — Q&A with sources (decision → analyst → policy → document).
5. **Before vs after** — plain lookup ("1 reported UPI") vs RingBreaker ("ring of 12 accounts, collector found, ₹X traced").

Tenant switcher in the sidebar (bank A / bank B) to show separate graphs.

---

## 10. Demo script (3 chapters, ~3 minutes total)

1. **Track 01 (90s):** Priya reports `hr.jobs2026@fakepay` → agent traces 3 hops → ring of 12 → collector found by PageRank → accounts frozen → "why" panel shows the path.
2. **Track 02 (45s):** Next day Priya returns with a new number → Intake remembers her → links the new number to the same ring → shows the handoff log between 3 agents.
3. **Track 03 (45s):** "Why was ACC-5530 frozen and who approved it?" → answer with analyst, approver, policy P-07 (now superseded by P-09), and ticket TKT-4471.

---

## 11. Daily plan and definition of done

- **Day 1 (Oct 15):** Docker up, schema, generator, loader. DONE = all three planted scenarios queryable in FalkorDB Browser by hand-written Cypher.
- **Day 2 (Oct 16):** MCP server with Track 01 tools + Investigator + Action agents. DONE = scenario 1 works end to end from the command line, with evidence paths stored.
- **Day 3 (Oct 17):** AM: Intake agent, memory tools, handoffs, cross-session recall (scenario 2). PM: Brain agent (scenario 3). DONE = all three scenarios work from the command line.
- **Day 4 (Oct 18):** Streamlit UI, README with architecture diagram, demo video, blog post draft in `docs/blog_post.md`. Feature freeze at 2 PM. Submit by 10 PM, not 11:59.

**Cut order if behind:** Track 03 extras → Track 02 extras → UI polish. Never cut the Track 01 flow.

---

## 12. Kickoff prompts (paste one at the start of each day)

**Day 1:**
> Read CLAUDE.md fully. Set up the project: repo structure, requirements.txt, .env.example, docker-compose for FalkorDB, and a run script. Then confirm which algo.* procedures my FalkorDB version supports and tell me. Then build data/generate.py with the three planted scenarios, then graph/schema.py and graph/load.py. Run everything and show me Cypher queries proving each scenario exists.

**Day 2:**
> Read CLAUDE.md. Build mcp_server/server.py with all Track 01 tools, each returning result + evidence + cypher. Test each tool alone. Then build agents/llm.py, investigator.py, and action.py, and run scenario 1 end to end from the command line. Show me the agent's tool calls and the stored evidence path.

**Day 3:**
> Read CLAUDE.md. Add the Track 02 memory tools, the Intake agent, the orchestrator with logged handoffs, and make scenario 2 work. Test 5 cases running concurrently. Then add the Track 03 tools and Brain agent and make scenario 3 work. Show me all three scenarios running.

**Day 4:**
> Read CLAUDE.md. Build the Streamlit UI exactly as in section 9, focused on making the evidence path visible. Then write README.md (problem, how the graph drives decisions, architecture diagram, setup in 5 commands, the 3 demo scenarios) and a draft blog post in docs/blog_post.md. Then give me a checklist for recording the demo video.

**When something breaks:**
> Stop. Show me the exact error and the smallest failing piece. Explain the cause in two sentences, fix only that, rerun, and show me it passes.
