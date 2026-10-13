"""Investigator agent (Track 01 core).

Reads the case from the graph, loads its playbook (procedural memory), then investigates:
lookup -> expand -> shared infrastructure -> memories -> detect ring -> rank (PageRank) -> money trail -> risk.

With an LLM configured, the model chooses the tools itself from the MCP tool list (the playbook is its guide).
Without one, the playbook steps run in order. Either way, every fact comes from a graph tool, and the agent
writes what it learned back to shared memory before handing the case to the Action agent.
"""
from __future__ import annotations

import json

from agents.base import GROUNDING_RULE, Agent

INVESTIGATION_TOOLS = ["lookup_entity", "expand_neighbors", "find_shared_infrastructure", "get_memories",
                       "detect_ring", "rank_ring_members", "money_trail", "risk_score", "get_case_state"]

SYSTEM = f"""You are the Investigator agent of a bank's fraud team. You investigate one scam report at a time
using graph tools over FalkorDB (accounts, UPI handles, phones, devices, IPs, links, money flows, memories).
{GROUNDING_RULE}
Goal: find the scam ring behind the reported entity, its collector, the money trail and the risk of every member.
You MUST call detect_ring with the case_id, then rank_ring_members and risk_score on all ring members.
Check get_memories on suspicious shared devices: other agents may already know them.
Finish with a short case summary (5-8 lines) citing ids: ring id and size, how the reported entity connects to the
ring (the path), the collector, money traced, and risk scores. Do not take actions; the Action agent does that."""


class InvestigatorAgent(Agent):
    agent_id = "AGT-investigator"

    def run(self, case_id: str) -> dict:
        state = self.tool("get_case_state", case_id=case_id)
        scam_type = (state.get("case") or {}).get("scam_type") or "job_scam"
        value = state.get("reported_value") or ""
        self.tool("update_case", case_id=case_id, status="investigating")
        playbook = self.tool("get_playbook", scam_type=scam_type)
        steps = playbook["content"]["steps"]
        self.say(f"Playbook v{playbook['version']} for {scam_type}: {' -> '.join(steps)}")

        if self.ctx.llm.available:
            facts = self._run_llm(case_id, value, scam_type, playbook)
        else:
            facts = self._run_playbook(case_id, value, scam_type, steps)

        facts = self._ensure_core(case_id, facts, scam_type)
        self._learn(case_id, scam_type, facts, playbook)
        summary = facts.get("summary") or self._summary(facts)
        self.tool("update_case", case_id=case_id, summary=summary[:1500])
        self.tool("handoff", case_id=case_id, from_agent=self.agent_id, to_agent="AGT-action",
                  note=f"Ring {facts.get('ring_id')} ({facts.get('size', 0)} accounts), collector "
                       f"{facts.get('collector')}. Scores saved. Please act per current freeze policy.")
        self.say(summary)
        return {**facts, "summary": summary}

    # ------------------------------------------------------------------ playbook mode (no LLM)
    def _run_playbook(self, case_id: str, value: str, scam_type: str, steps: list[str]) -> dict:
        f: dict = {"case_id": case_id}
        for step in steps:
            if step == "lookup_entity":
                r = self.tool("lookup_entity", value=value)
                if not r.get("found"):
                    self.say(f"{value} is not in the graph yet; nothing to follow.")
                    return f
                f["entity"] = r["entity"]
                f["accounts"] = [a["id"] for a in r["accounts"]]
                f["earlier_reports"] = len(r["earlier_reports"])
                f["known_memories"] = r["memories"]
                self.say(f"{r['entity']['name']} -> accounts {', '.join(f['accounts']) or 'none'}; "
                         f"{len(r['earlier_reports'])} report(s) mention it.")
            elif step == "expand_neighbors" and f.get("entity"):
                r = self.tool("expand_neighbors", node_id=f["entity"]["id"], hops=3)
                f["reached_accounts"] = len(r.get("accounts", []))
                self.say(f"3-hop expansion reaches {f['reached_accounts']} accounts via {r.get('counts')}.")
            elif step == "find_shared_infrastructure" and f.get("accounts"):
                r = self.tool("find_shared_infrastructure", account_ids=f["accounts"])
                f["suspicious_infra"] = [s for s in r["shared"] if s["suspicious"]]
                if f["suspicious_infra"]:
                    top = f["suspicious_infra"][0]
                    self.say(f"{top['type']} {top['id']} is shared by {top['accounts_sharing']} accounts.")
            elif step == "get_memories" and f.get("suspicious_infra"):
                # learned step: check what any agent already knows about the shared infrastructure
                for s in f["suspicious_infra"][:3]:
                    mem = self.tool("get_memories", node_id=s["id"])["memories"]
                    if mem:
                        f.setdefault("known_memories", []).extend(mem)
                        self.say(f"Memory hit on {s['id']}: {mem[0]['content']}")
            elif step == "detect_ring" and f.get("entity"):
                r = self.tool("detect_ring", seed_node_id=f["entity"]["id"], case_id=case_id, scam_type=scam_type)
                if not r.get("ring_id"):
                    self.say(r.get("message", "No ring found."))
                    return f
                f.update(ring_id=r["ring_id"], size=r["size"], already_known=r["already_known"],
                         new_members=r["new_members"], members=[m["id"] for m in r["members"]],
                         shared_infrastructure=r["shared_infrastructure"], agreement=r["money_community_agreement"])
                msg = (f"Ring {r['ring_id']}: {r['size']} accounts sharing "
                       f"{', '.join(x['id'] for x in r['shared_infrastructure'][:3])}")
                if r["already_known"]:
                    msg += f" (known ring; new member(s): {', '.join(r['new_members']) or 'none'})"
                self.say(msg + ".")
            elif step == "rank_ring_members" and f.get("ring_id"):
                r = self.tool("rank_ring_members", ring_id=f["ring_id"])
                f.update(collector=r["collector"], victims=r["victim_accounts"], stolen=r["total_from_victims"],
                         roles={x["id"]: x["role"] for x in r["ranked"]})
                self.say(f"PageRank collector: {r['collector']}. {r['victim_accounts']} victim accounts paid "
                         f"Rs {r['total_from_victims']:,.0f} into the ring.")
            elif step == "money_trail" and f.get("collector") and f.get("accounts"):
                src = f["accounts"][0]
                if src != f["collector"]:
                    r = self.tool("money_trail", from_id=src, to_id=f["collector"])
                    if r.get("found"):
                        f["money_path"] = r["path"]
                        self.say(f"Money trail {' -> '.join(r['path'])} ({r['hop_count']} hops).")
            elif step == "risk_score" and f.get("members"):
                r = self.tool("risk_score", account_ids=f["members"])
                f["scores"] = {s["id"]: s["score"] for s in r["scores"]}
                self.say("Risk scores: " + ", ".join(f"{k}={v}" for k, v in list(f["scores"].items())[:6]) + " ...")
        return f

    # ------------------------------------------------------------------ LLM mode
    def _run_llm(self, case_id: str, value: str, scam_type: str, playbook: dict) -> dict:
        tools = [t for t in self.ctx.client.list_tools() if t["name"] in INVESTIGATION_TOOLS]
        user = json.dumps({"case_id": case_id, "reported_value": value, "scam_type": scam_type,
                           "playbook_steps": playbook["content"]["steps"], "playbook_note": playbook["content"].get("note")})

        def call(name, args):
            if name not in INVESTIGATION_TOOLS:
                return {"result": {"error": f"{name} is not available to the investigator"}}
            if name == "detect_ring":
                args.setdefault("case_id", case_id)
                args.setdefault("scam_type", scam_type)
            return self.ctx.client.call(name, args, agent=self.agent_id, trace=self.ctx.trace)

        text = self.ctx.llm.run_tools(SYSTEM, user, tools, call, on_text=lambda t: self.say(t))
        return {"case_id": case_id, "summary": text}

    def _ensure_core(self, case_id: str, f: dict, scam_type: str) -> dict:
        """Whatever the LLM did, make sure the ring, ranking and scores exist in the graph (guardrail)."""
        state = self.tool("get_case_state", case_id=case_id)
        if not f.get("ring_id"):
            if state.get("rings"):
                f["ring_id"] = state["rings"][0]
            elif state.get("mentions"):
                r = self.tool("detect_ring", seed_node_id=state["mentions"][0]["id"], case_id=case_id,
                              scam_type=scam_type)
                if r.get("ring_id"):
                    f.update(ring_id=r["ring_id"], size=r["size"], already_known=r["already_known"],
                             new_members=r["new_members"], shared_infrastructure=r["shared_infrastructure"])
        if f.get("ring_id") and not f.get("collector"):
            r = self.tool("rank_ring_members", ring_id=f["ring_id"])
            f.update(collector=r["collector"], victims=r["victim_accounts"], stolen=r["total_from_victims"],
                     members=[x["id"] for x in r["ranked"]], size=len(r["ranked"]))
        if f.get("ring_id") and not f.get("scores"):
            r = self.tool("risk_score", account_ids=f.get("members") or [])
            f["scores"] = {s["id"]: s["score"] for s in r["scores"]}
        if f.get("ring_id") and not f.get("shared_infrastructure"):
            f["shared_infrastructure"] = []
        return f

    # ------------------------------------------------------------------ memory writes
    def _learn(self, case_id: str, scam_type: str, f: dict, playbook: dict):
        if not f.get("ring_id"):
            return
        known = " ".join(m.get("content", "") for m in f.get("known_memories", []))
        for infra in (f.get("shared_infrastructure") or [])[:2]:
            if f["ring_id"] in known and infra["id"] in known:
                continue
            existing = self.tool("get_memories", node_id=infra["id"])["memories"]
            if any(f["ring_id"] in m["content"] for m in existing):
                continue
            self.tool("write_memory", agent_id=self.agent_id, kind="semantic", about_node_id=infra["id"],
                      content=f"{infra['id']} is shared by {infra['accounts']} accounts of ring {f['ring_id']} "
                              f"({scam_type}), found in case {case_id}. Treat new accounts on it as ring members.",
                      confidence=0.9)
        if f.get("collector"):
            existing = self.tool("get_memories", node_id=f["collector"])["memories"]
            if not any(f["ring_id"] in m["content"] for m in existing):
                self.tool("write_memory", agent_id=self.agent_id, kind="semantic", about_node_id=f["collector"],
                          content=f"{f['collector']} is the collector (top PageRank) of ring {f['ring_id']}.",
                          confidence=0.85)
        # procedural memory: after a successful investigation, make the memory check explicit in the playbook
        steps = playbook["content"]["steps"]
        if "get_memories" not in steps:
            new_steps = list(steps)
            i = new_steps.index("find_shared_infrastructure") + 1 if "find_shared_infrastructure" in new_steps else 1
            new_steps.insert(i, "get_memories")
            hub = (f.get("shared_infrastructure") or [{}])[0].get("id", "a shared device")
            self.tool("update_playbook", scam_type=scam_type, steps=new_steps, case_id=case_id,
                      confidence=float(playbook.get("confidence") or 0.6) + 0.15,
                      note=f"Learned in {case_id}: {hub} linked the reported entity to ring {f['ring_id']}. "
                           f"Check memories on shared devices right after finding them: a known ring device "
                           f"identifies the ring immediately.")
            self.say("Playbook updated with what worked (procedural memory).")

    @staticmethod
    def _summary(f: dict) -> str:
        if not f.get("ring_id"):
            return f"No scam ring found for case {f.get('case_id')}."
        flagged = sum(1 for v in (f.get("scores") or {}).values() if v >= 50)
        lines = [f"Ring {f['ring_id']}: {f.get('size')} accounts linked by shared "
                 f"{', '.join(x['id'] for x in (f.get('shared_infrastructure') or [])[:3]) or 'infrastructure'}.",
                 f"Reported {f.get('entity', {}).get('name', '?')} -> {', '.join(f.get('accounts', []))}."]
        if f.get("already_known"):
            lines.append(f"This ring was already known; new member(s): {', '.join(f.get('new_members') or []) or 'none'}.")
        if f.get("collector"):
            lines.append(f"Collector (PageRank): {f['collector']}. {f.get('victims', 0)} victim accounts paid "
                         f"Rs {f.get('stolen', 0):,.0f} into the ring.")
        if f.get("money_path"):
            lines.append(f"Money trail: {' -> '.join(f['money_path'])}.")
        lines.append(f"{flagged} of {len(f.get('scores') or {})} members scored 50+ risk.")
        return " ".join(lines)
