"""Action agent (Track 01).

Acts on what the graph shows, under the policy the graph says is current:
  - freezes ring members whose risk score meets the current freeze threshold (P-09: 70)
  - sends medium-risk members (P-09: 50-69) to human review, assigned to an analyst who knows the scam type
  - alerts other customers who paid the ring
  - drafts the cyber-crime complaint
Every action stores its evidence path and Cypher. Actions are policy-driven on purpose: an LLM never decides
on its own to freeze an account; the freeze tool refuses anything below the policy threshold.
"""
from __future__ import annotations

from agents.base import Agent


class ActionAgent(Agent):
    agent_id = "AGT-action"

    def run(self, case_id: str) -> dict:
        state = self.tool("get_case_state", case_id=case_id)
        rings = state.get("rings") or []
        if not rings:
            self.say("No ring linked to this case; nothing to act on.")
            self.tool("handoff", case_id=case_id, from_agent=self.agent_id, to_agent="AGT-intake",
                      note="No ring found. Tell the customer we are monitoring the reported entity.")
            return {"frozen": [], "review": [], "alerted": 0}
        ring_id = rings[0]
        policy = self.tool("current_policy", topic="freeze")["current"]
        self.say(f"Current freeze policy {policy['code']}: freeze at risk >= {policy.get('freeze_threshold')}, "
                 f"review from {policy.get('review_threshold')} (replaces {', '.join(policy.get('replaces') or []) or '-'}).")

        ranked = self.tool("rank_ring_members", ring_id=ring_id)["ranked"]
        active = [m["id"] for m in ranked if m["status"] != "frozen"]
        if active:
            self.tool("risk_score", account_ids=active)  # fresh scores: frozen neighbours may have changed them
        res = self.tool("freeze_accounts", account_ids=active, case_id=case_id,
                        reason=f"Member of scam ring {ring_id} (case {case_id}); risk score meets {policy['code']}.")
        frozen = res.get("frozen", [])
        to_review = [x["id"] for x in res.get("refused", []) if x.get("suggest") == "flag_for_review"]
        for x in res.get("refused", []):
            if "pending human review" in x.get("why", ""):
                self.say(f"Left {x['id']} alone: {x['why']}.")
        review = []
        if to_review:
            review = self.tool("flag_for_review", account_ids=to_review, case_id=case_id,
                               reason=f"Ring {ring_id} member with medium risk; needs a human decision.")["flagged"]
        for fz in frozen[:3]:
            self.say(f"Froze {fz['id']} (risk {fz['score']}): {fz['evidence_path']}")
        if len(frozen) > 3:
            self.say(f"... and {len(frozen) - 3} more frozen.")
        for rv in review:
            self.say(f"Sent {rv['id']} (risk {rv['score']}) to human review: {rv['assigned_to']}")

        alerted = 0
        if frozen:
            alert = self.tool("send_alert", case_id=case_id)
            alerted = alert.get("alerted_users", 0)
            self.say(f"Alerted {alerted} other customers who paid this ring (policy {alert.get('policy')}).")
        complaint = self.tool("draft_complaint", case_id=case_id)
        self.say(f"Complaint draft {complaint.get('action_id')} ready for analyst submission.")

        self.tool("update_case", case_id=case_id, status="actioned")
        if state.get("user_id"):
            self.tool("write_memory", agent_id=self.agent_id, kind="episodic", about_node_id=state["user_id"],
                      content=f"{state.get('user_name')} reported {state.get('reported_value')} in case {case_id}: "
                              f"linked to ring {ring_id}; {len(frozen)} account(s) frozen, {len(review)} sent to review.",
                      confidence=1.0)
        self.tool("handoff", case_id=case_id, from_agent=self.agent_id, to_agent="AGT-intake",
                  note=f"Actioned: {len(frozen)} frozen, {len(review)} in review, {alerted} customers alerted, "
                       f"complaint drafted. Please update the customer.")
        return {"ring_id": ring_id, "policy": policy["code"], "frozen": frozen, "review": review,
                "alerted": alerted, "complaint_id": complaint.get("action_id"), "complaint": complaint.get("text")}
