"""Brain agent (Track 03): the fraud team's company brain.

Answers questions about how the fraud team works: who owns a case, who knows a scam type, why an account was
frozen and who approved it, which policy applies now (following SUPERSEDES), and what the tickets/chats say.
Every answer lists its sources (decisions, policies, documents, actions) by id.
Human decisions (analysts) and agent decisions (freezes made by the Action agent) live in the same graph,
so the brain can explain both.
"""
from __future__ import annotations

import json
import re

from agents.base import GROUNDING_RULE, Agent
from agents.intake import SCAM_KEYWORDS

BRAIN_TOOLS = ["who_owns", "who_knows", "trace_decision", "current_policy", "search_documents", "get_case_state",
               "get_memories", "recent_activity"]

SYSTEM = f"""You are the fraud team's knowledge assistant (company brain) at a bank. You answer questions about
cases, people, decisions and policies using graph tools. {GROUNDING_RULE}
Always check whether a policy used in a decision has been superseded, and say which policy applies today.
End with a 'Sources:' line listing the ids you used (decisions, policies, tickets/chats, actions)."""

TOPICS = [("freeze", ["freeze", "frozen", "threshold", "block account"]), ("alert", ["alert", "warn", "notify"]),
          ("complaint", ["complaint", "cyber", "police", "fir"]), ("kyc", ["kyc", "re-verif"]),
          ("retention", ["retention", "retain", "keep evidence"])]


class BrainAgent(Agent):
    agent_id = "AGT-brain"

    def ask(self, question: str) -> dict:
        if self.ctx.llm.available:
            tools = [t for t in self.ctx.client.list_tools() if t["name"] in BRAIN_TOOLS]

            def call(name, args):
                if name not in BRAIN_TOOLS:
                    return {"result": {"error": f"{name} is not available to the brain agent"}}
                return self.ctx.client.call(name, args, agent=self.agent_id, trace=self.ctx.trace)

            answer = self.ctx.llm.run_tools(SYSTEM, question, tools, call)
            return {"question": question, "answer": answer, "sources": self._sources()}
        return self._answer_offline(question)

    # ------------------------------------------------------------------ offline routing
    def _answer_offline(self, q: str) -> dict:
        low = q.lower()
        ids = re.findall(r"\b(?:ACC|DEC|C|RING)-[\w-]+\b", q.upper())
        scam_type = next((st for st, words in SCAM_KEYWORDS if any(w in low for w in words)), None)
        topic = next((t for t, words in TOPICS if any(w in low for w in words)), None)
        parts = []

        if scam_type and (re.search(r"\bwho\b.*\b(know|knows|expert|handle|handles|help)\b", low)
                          or any(w in low for w in ["expert", "ask about"])):
            r = self.tool("who_knows", scam_type=scam_type)
            people = r["analysts"]
            parts.append(f"For {scam_type.replace('_', ' ')}, ask " + "; ".join(
                f"{a['name']} ({a['role']}, {a['team']}: owns {a['cases_owned']} such case(s), "
                f"made {a['decisions_made']} decision(s))" for a in people[:3]) + "." if people
                else f"Nobody is recorded as knowing {scam_type}.")

        if ids and any(w in low for w in ["who owns", "owner", "whose"]):
            for cid in [i for i in ids if i.startswith("C-")]:
                r = self.tool("who_owns", case_id=cid)
                owners = ", ".join(f"{a['name']} ({a['role']})" for a in r["analysts"]) or "no analyst"
                parts.append(f"Case {cid} is owned by {owners}; currently assigned to {r['assigned_agent'] or '-'}.")

        if ids and not parts or (ids and any(w in low for w in ["why", "who approved", "decid", "frozen", "freeze"])):
            for i in ids[:2]:
                r = self.tool("trace_decision", decision_or_account_or_case_id=i)
                if not r["found"]:
                    parts.append(f"No recorded decision about {i}.")
                    continue
                d = r["decisions"][0]
                who = d["made_by"] + (f" ({d['maker_type'].lower()})" if d["maker_type"] == "Agent" else "")
                s = (f"{d['account'] or i} was {'frozen' if d['outcome'] == 'freeze' else d['outcome']} on "
                     f"{d['ts'][:10]} in case {d['case_id']}, decided by {who}")
                s += f" and approved by {d['approved_by']}" if d["approved_by"] else f" ({d.get('approval') or 'no approval recorded'})"
                s += f", under policy {d['policy']} ({d['policy_title']})."
                s += f" Reason: {d['reason']}"
                if d["superseded_by"]:
                    s += f" Note: {d['policy']} has since been superseded by {', '.join(d['superseded_by'])}."
                docs = [x for x in d["sources"] if x["type"] == "Document"]
                if docs:
                    s += " Source: " + "; ".join(f"{x['id']} \"{x['title']}\"" for x in docs) + "."
                parts.append(s)
                topic = topic or ("freeze" if d["superseded_by"] else None)

        if topic and (not parts or any(w in low for w in ["policy", "threshold", "rule", "now", "today", "current"])
                      or "superseded" in " ".join(parts)):
            r = self.tool("current_policy", topic=topic)
            c = r["current"]
            if c:
                s = f"Policy in force for '{topic}': {c['code']} {c['title']} (since {c['effective_from']}). {c['text']}"
                if c.get("replaces"):
                    s += f" It replaces {', '.join(c['replaces'])}."
                parts.append(s)

        if not parts:
            r = self.tool("search_documents", query=q)
            docs = r["documents"]
            parts.append("From the team's documents: " + " | ".join(
                f"{d['id']} ({d['source']}) \"{d['title']}\": {d['text'][:180]}" for d in docs[:3])
                if docs else "I couldn't find anything in the graph about that.")
        return {"question": q, "answer": "\n\n".join(parts), "sources": self._sources()}

    def _sources(self) -> list[str]:
        keep = ("Decision", "Policy", "Document", "ActionTaken", "Case", "Analyst")
        seen = []
        for ev in self.ctx.trace.events:
            for n in ev.evidence.get("nodes", []):
                if n.get("label") in keep and n["id"] not in seen:
                    seen.append(n["id"])
        return seen


def pretty(r: dict) -> str:
    return r["answer"] + "\n\nSources: " + ", ".join(r["sources"]) if r.get("sources") else r["answer"]


if __name__ == "__main__":  # quick manual check
    from agents.base import AgentContext

    print(json.dumps(BrainAgent(AgentContext.create()).ask("Why was ACC-5530 frozen and who approved it?"), indent=2))
