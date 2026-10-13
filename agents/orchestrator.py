"""Orchestrator: routes a customer report through Intake -> Investigator -> Action -> Intake.

The agents never pass Python state to each other. Each handoff is written to the graph (Handoff node +
HANDED_OFF edge + Case.assignee), and the next agent starts by reading the case state from the graph.
That is what lets several cases run concurrently and lets any agent (or a human) pick a case up later.
"""
from __future__ import annotations

import time

from agents.action import ActionAgent
from agents.base import AgentContext
from agents.brain import BrainAgent
from agents.intake import IntakeAgent
from agents.investigator import InvestigatorAgent


def process_report(user_id: str, message: str, ctx: AgentContext | None = None, tenant: str | None = None) -> dict:
    ctx = ctx or AgentContext.create(tenant)
    t0 = time.time()
    intake = IntakeAgent(ctx)
    started = intake.start(user_id, message)
    case_id = started.get("case_id")
    if not case_id:
        return {"case_id": None, "reply": started["reply"], "trace": ctx.trace, "seconds": time.time() - t0}
    findings = InvestigatorAgent(ctx).run(case_id)
    actions = ActionAgent(ctx).run(case_id)
    reply = intake.finish(user_id, started["session_id"], case_id, started.get("prior_rings", []),
                          started.get("greeting", ""))
    return {"case_id": case_id, "session_id": started["session_id"], "findings": findings, "actions": actions,
            "reply": reply, "trace": ctx.trace, "seconds": round(time.time() - t0, 1)}


def ask_brain(question: str, ctx: AgentContext | None = None, tenant: str | None = None) -> dict:
    ctx = ctx or AgentContext.create(tenant)
    out = BrainAgent(ctx).ask(question)
    out["trace"] = ctx.trace
    return out
