# Demo video: script and recording checklist (about 3 minutes)

## Before recording
- [ ] `./run.sh up`, then click **Reset demo data** in the sidebar (or `./run.sh seed`). Every run starts clean.
- [ ] Browser at 1440 px wide, zoom 100%, close other tabs and notifications.
- [ ] Do one practice run of all three chapters, then reset again.
- [ ] Record the screen at 1080p with your voice. Speak slowly. Keep the mouse still while talking.

## 0:00-0:20 The problem (title card or the Before and after tab)
"When someone reports a scam, the bank blocks one account. The money has already moved on, and other reports about the same gang sit unconnected. RingBreaker gives agents a FalkorDB graph so they can follow the connections."

## 0:20-1:50 Chapter 1, Track 01: agents that act on connected data
1. Report a scam tab. Click **Day 1: Priya reports a UPI ID**.
2. Point at the agent log: playbook loaded, D17 shared by 7 accounts, ring of 13, PageRank collector ACC-7781, money trail, risk scores.
3. Open the MCP tool-calls expander for a second: "every step is an MCP tool call with its Cypher."
4. Investigation tab. The ring is on the graph; ACC-7781 is selected; the indigo thread shows UPI to ACC-3302 to D17 to ACC-7781.
5. Read the risk breakdown and "Frozen under P-09". Point at ACC-6619 (dashed): "medium risk, so a human decides: Arjun M., who knows job scams."

## 1:50-2:35 Chapter 2, Track 02: memory and coordination
1. Back to Report a scam. Click **Day 2: Priya returns** (a toast shows the new account from the overnight feed).
2. Point at: "Recalled Priya S.", "Playbook v2", "Memory hit on D17", "known ring; new member ACC-9104".
3. Memory tab: Priya's two conversations, what the agents remember, the handoff table, the playbook versions.
4. Switch the bank to SurakshaBank in the sidebar: "separate graph per bank, Priya isn't here." Switch back.

## 2:35-3:05 Chapter 3, Track 03: company brain
1. Ask the fraud team tab. Click **Why was ACC-5530 frozen and who approved it?**
2. Point at Arjun M., Meera K., P-07 superseded by P-09, TKT-4471, and the sources.
3. Click **Why was ACC-7781 frozen?**: "the agents' own decisions are in the same graph."

## 3:05-3:15 Close
"One report in, the whole ring out, with evidence for every action. Built on FalkorDB."
