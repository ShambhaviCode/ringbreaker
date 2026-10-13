"""Intake agent (Track 02).

Talks to the customer. Before anything else it recalls the customer from the graph (past sessions, reports,
cases, rings, episodic memories), so a returning customer is never treated as a stranger. It logs the
conversation to the graph (persistent history), files the report, opens the case and hands it to the
Investigator. At the end it reads the case state back from the graph and explains the outcome.
"""
from __future__ import annotations

import re

from agents.base import GROUNDING_RULE, Agent

SCAM_KEYWORDS = [
    ("job_scam", ["job", "work from home", "wfh", "registration fee", "hr", "salary", "task", "part time"]),
    ("kyc_scam", ["kyc", "pan", "aadhaar", "account will be blocked", "update details", "verify account"]),
    ("investment_scam", ["invest", "crypto", "trading", "returns", "stock tips", "profit"]),
    ("fake_customer_care", ["customer care", "helpline", "refund", "support number", "anydesk"]),
    ("loan_app_scam", ["loan", "processing fee", "emi", "instant loan"]),
]

EXTRACT_SYSTEM = f"""Extract from a scam report message. {GROUNDING_RULE}
Return JSON: {{"reported_value": the single UPI handle, phone number, link or account id the customer was asked
to pay or contacted by (copied exactly), "scam_type": one of job_scam|kyc_scam|investment_scam|
fake_customer_care|loan_app_scam, "summary": one sentence}}."""

REPLY_SYSTEM = f"""You are a bank's scam-help assistant writing to a customer who reported a scam. Warm, plain English,
max 120 words, no jargon. {GROUNDING_RULE} Use only the facts given. Never reveal other customers' names or
account holder names. Mention what was done (accounts frozen, money traced, alerts) and next steps
(complaint draft prepared; an analyst will contact them; do not pay anything more)."""


def extract_report(text: str, llm=None) -> dict:
    if llm is not None and llm.available:
        out = llm.complete_json(EXTRACT_SYSTEM, text)
        if out.get("reported_value"):
            return out
    value = ""
    for pat in [r"[\w.\-]+@[\w\-]+", r"https?://\S+", r"[\w\-]+\.example\S*", r"\+?\d[\d\s\-]{9,15}\d",
                r"ACC-\d+"]:
        m = re.search(pat, text, re.I)
        if m:
            value = m.group(0).rstrip(".,;)")
            break
    low = text.lower()
    scam_type = next((st for st, words in SCAM_KEYWORDS if any(w in low for w in words)), "job_scam")
    return {"reported_value": value, "scam_type": scam_type, "summary": text.strip()[:200]}


class IntakeAgent(Agent):
    agent_id = "AGT-intake"

    def start(self, user_id: str, message: str) -> dict:
        memory = self.tool("recall_user", user_id=user_id)
        greeting = ""
        past = [r for r in memory.get("reports", []) if r.get("case")]
        if memory.get("known") and past:
            last = sorted(past, key=lambda r: r.get("ts") or "")[-1]
            greeting = (f"Welcome back, {memory['user']['name'].split()[0]}. I can see your earlier report "
                        f"({last['report']}, case {last['case']}, {(last.get('ts') or '')[:10]})"
                        + (f", which we linked to scam ring {last['ring']}" if last.get("ring") else "") + ".")
            self.say(f"Recalled {memory['user']['name']}: {len(past)} earlier case(s), "
                     f"{len(memory.get('sessions', []))} past session(s), {len(memory.get('memories', []))} memories.")
        elif memory.get("known"):
            self.say(f"Known customer {memory['user']['name']}, first report.")
        session = self.tool("open_session", user_id=user_id)["session_id"]
        self.tool("log_message", session_id=session, role="user", text=message)

        ex = extract_report(message, self.ctx.llm)
        if not ex.get("reported_value"):
            reply = ("Sorry this happened. Please share the UPI ID, phone number or link you were asked to pay "
                     "or were contacted from, so we can trace it.")
            self.tool("log_message", session_id=session, role="intake", text=reply)
            return {"session_id": session, "case_id": None, "reply": reply, "greeting": greeting}

        rep = self.tool("file_report", user_id=user_id, reported_value=ex["reported_value"],
                        scam_type=ex["scam_type"], description=ex.get("summary") or message, session_id=session)
        self.say(f"Filed {rep['report_id']} ({ex['scam_type']}) about {rep['value']}; opened case {rep['case_id']}.")
        self.tool("write_memory", agent_id=self.agent_id, kind="episodic", about_node_id=user_id,
                  content=f"{(memory.get('user') or {}).get('name', user_id)} reported {rep['value']} "
                          f"({ex['scam_type']}) in case {rep['case_id']}.", confidence=1.0)
        prior_rings = sorted({r["ring"] for r in past if r.get("ring")})
        note = f"New {ex['scam_type']} report about {rep['value']}."
        if prior_rings:
            note += f" Customer previously reported ring(s) {', '.join(prior_rings)}; check for a link."
        self.tool("handoff", case_id=rep["case_id"], from_agent=self.agent_id, to_agent="AGT-investigator", note=note)
        return {"session_id": session, "case_id": rep["case_id"], "report_id": rep["report_id"],
                "extracted": ex, "greeting": greeting, "prior_rings": prior_rings}

    def finish(self, user_id: str, session_id: str, case_id: str, prior_rings: list[str], greeting: str = "") -> str:
        st = self.tool("get_case_state", case_id=case_id)
        actions = st.get("actions", [])
        frozen = [t for a in actions if a["type"] == "freeze" for t in a["targets"]]
        review = [t for a in actions if a["type"] == "review" for t in a["targets"]]
        alerts = sum(1 for a in actions if a["type"] == "alert")
        ring = (st.get("rings") or [None])[0]
        same_ring = ring in prior_rings if ring else False
        facts = {"reported": st.get("reported_value"), "case": case_id, "ring": ring,
                 "same_ring_as_before": same_ring, "accounts_frozen": len(frozen), "frozen_ids": frozen,
                 "sent_to_review": len(review), "other_customers_alerted": bool(alerts),
                 "complaint_drafted": any(a["type"] == "complaint" for a in actions)}
        if self.ctx.llm.available:
            reply = self.ctx.llm.complete(REPLY_SYSTEM, str(facts))
        else:
            reply = self._template(facts)
        if greeting:
            reply = greeting + " " + reply
        self.tool("log_message", session_id=session_id, role="intake", text=reply)
        self.tool("handoff", case_id=case_id, from_agent=self.agent_id, to_agent="AGT-brain",
                  note="Customer updated. Case available to the fraud team knowledge base.")
        return reply

    @staticmethod
    def _template(f: dict) -> str:
        if not f["ring"]:
            return (f"Thank you for reporting {f['reported']} (case {f['case']}). We could not link it to a known "
                    f"group yet; we are monitoring it and will contact you. Please don't pay anything more.")
        parts = []
        if f["same_ring_as_before"]:
            parts.append(f"{f['reported']} belongs to the same scam group as your earlier report.")
        else:
            parts.append(f"We traced {f['reported']} to an organised scam group.")
        if f["accounts_frozen"]:
            parts.append(f"We have frozen {f['accounts_frozen']} account(s) used by this group"
                         + (f" ({', '.join(f['frozen_ids'][:3])}{'...' if f['accounts_frozen'] > 3 else ''})." ))
        if f["sent_to_review"]:
            parts.append(f"{f['sent_to_review']} more account(s) are with an analyst for review.")
        if f["other_customers_alerted"]:
            parts.append("Other customers who paid this group have been warned.")
        if f["complaint_drafted"]:
            parts.append("A cyber-crime complaint has been prepared and an analyst will contact you.")
        parts.append(f"Your case number is {f['case']}. Please don't send any more money or share OTPs.")
        return " ".join(parts)
