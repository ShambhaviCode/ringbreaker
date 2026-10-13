# One scam report, a whole scam ring: building RingBreaker on FalkorDB

*Draft for the Graph Hacks best-blog-post prize. Replace the bracketed bits with your own experience before publishing.*

## The report that usually goes nowhere

Priya pays Rs 18,500 to `hr.jobs2026@fakepay` as a "registration fee" for a work-from-home job. The job never existed. She reports it to her bank.

What normally happens next is a lookup. The bank finds the account behind that UPI ID and blocks it. But the money left that account within the hour. It went through two more accounts and landed with a collector. Nine other people had already reported the same group, each about a different UPI ID, phone number or link, and nobody connected them.

The connection was there all along. It just wasn't in any one row of any one table. It was in the relationships: mule accounts registered on the same phone, logging in from the same device, money flowing from victims to fronts to layers to one account at the end.

That's a graph problem. So I [we] built RingBreaker: a small team of AI agents that use a FalkorDB graph as their context.

## Why a graph, specifically

Every question the agents need to answer is about connections:

- Which accounts share a device with the account behind this UPI ID?
- Which group of accounts is held together by shared devices, IPs and phones?
- Where does the money end up?
- Has any agent seen this device before?
- Which policy applies today, and which one did it replace?

In FalkorDB each of these is one query or one built-in algorithm, fast enough to run on every step of an agent loop. We used `algo.WCC` to find the ring, `algo.labelPropagation` as an independent check on the money graph, `algo.pageRank` to name the collector and `algo.SPpaths` for money trails and evidence paths.

One thing we learned the hard way [keep or cut]: FalkorDB doesn't support undirected `shortestPath()`. `algo.SPpaths` with `relDirection: 'both'` did exactly what we needed for "how is the reported UPI connected to this account?"

## The agents

Four agents, and they never pass state to each other directly. Everything goes through the graph.

**Intake** talks to the customer. First it recalls them: past sessions, reports, cases, what other agents wrote about them. Then it files the report, opens a case and writes a handoff.

**Investigator** reads the case from the graph and loads its playbook for that scam type. The playbook is procedural memory stored in the graph. With an LLM configured, the model picks tools from our MCP server; without one, the playbook steps run in order. Either way every fact comes from a tool, and every tool returns its result, the nodes and edges it touched, and the Cypher it ran.

**Action** reads the current freeze policy from the graph and acts on it: freeze above the threshold, send the middle band to a human analyst who knows that scam type, alert the other victims, draft the cyber-crime complaint. The freeze tool itself refuses anything below the threshold. An LLM can't talk its way into freezing an account.

**Brain** answers the fraud team's questions about people, cases, decisions and policies, with sources.

## Memory that changes behaviour

The part I'm [we're] proudest of is that memory isn't decoration. It changes what happens next.

After the first case, the Investigator writes a semantic memory ("D17 is shared by 7 accounts of ring RING-2229") and updates the job-scam playbook to check memories on shared devices right after finding them.

The next day a new account opens on device D17 and calls Priya with a new number. Intake recognises her. The Investigator's improved playbook checks D17's memories, finds the ring, and adds the new account to it. Because D17 is now shared with frozen accounts, the new account's risk score crosses the policy line, and it's frozen with its evidence path stored. Priya's reply starts with "Welcome back".

## Explainable by construction

Every frozen account has an evidence path: the exact chain of nodes and relationships from what the customer reported to that account. In the UI it's drawn as a thick indigo thread through the graph, with the risk score broken into named factors next to it. The same decision is a `Decision` node in the graph, so when someone asks the Brain "why was ACC-7781 frozen?", the answer cites the agent, the policy and the stored action.

## What I'd [we'd] do next

- Real-time feeds from transaction streams, with the ring detection running incrementally.
- Feedback from analysts' review decisions into the risk weights.
- A shared, privacy-preserving graph across banks: rings rarely stay in one bank.

## Try it

[link to repo] · [link to demo video]. Everything runs locally with Docker and one `./run.sh all`. No API key needed; add one to let an LLM drive the tool selection.
