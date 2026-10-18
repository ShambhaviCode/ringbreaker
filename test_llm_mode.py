"""LLM mode, with a scripted (mock) model: the model picks MCP tools itself; guardrails still apply."""
from agents.base import AgentContext
from agents.investigator import InvestigatorAgent
from agents.llm import LLM
from graph import tools as T


def test_llm_investigator_uses_mcp_tools_and_guardrail_fills_gaps():
    uid = T.register_user("Mock Test User")["result"]["user_id"]
    case = T.file_report(uid, "https://instaloan-app.example/get", "loan_app_scam",
                         "loan app fee")["result"]["case_id"]
    script = [
        {"tool": "lookup_entity", "args": {"value": "https://instaloan-app.example/get"}},
        {"tool": "find_shared_infrastructure", "args": {"account_ids": []}},
        {"tool": "detect_ring", "args": {"seed_node_id": "LNK-9038"}},  # case_id is injected by the agent
        {"tool": "freeze_accounts", "args": {"account_ids": ["ACC-5043"], "case_id": case, "reason": "x"}},
        {"text": "Ring found around LNK-9038."},
    ]
    ctx = AgentContext.create("demobank", "mcp", llm=LLM("mock", script=script))
    out = InvestigatorAgent(ctx).run(case)
    tools_called = [e.tool for e in ctx.trace.events]
    assert "detect_ring" in tools_called
    assert "freeze_accounts" not in tools_called  # investigator cannot act; tool was refused
    # guardrail completed what the model skipped
    assert out["collector"] == "ACC-5043" and out["scores"]
    assert T.get_case_state(case)["result"]["rings"] == [out["ring_id"]]
