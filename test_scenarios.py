"""End-to-end checks of the three demo scenarios, through the real MCP server."""
import json

from agents.base import AgentContext
from agents.orchestrator import ask_brain, process_report
from graph import tools as T
from graph.load import apply_scenario2_feed

MSG1 = "I paid Rs 18,500 registration fee for a work from home job to hr.jobs2026@fakepay, now no reply."
MSG2 = "Got a WhatsApp call from +91-90000-41177 offering the same job again. Same people?"


def test_scenario1_ring_found_and_actioned(truth):
    out = process_report("USR-PRIYA", MSG1, AgentContext.create("demobank", "mcp"))
    f, a = out["findings"], out["actions"]
    r1 = truth["R1"]
    # the agent found exactly the planted ring, and the right collector
    assert sorted(f["members"]) == sorted(r1["members"])
    assert f["collector"] == r1["collector"] == "ACC-7781"
    # 12 frozen, the low-activity recruit goes to human review
    assert len(a["frozen"]) == 12
    assert [x["id"] for x in a["review"]] == ["ACC-6619"]
    assert a["policy"] == "P-09"
    # the collector's freeze stores the 3-hop path via shared device D17
    col = next(x for x in a["frozen"] if x["id"] == "ACC-7781")
    assert "D17" in col["evidence_path"] and "hr.jobs2026@fakepay" in col["evidence_path"]
    st = T.get_case_state(out["case_id"])["result"]
    assert st["case"]["status"] == "actioned"
    freeze = next(x for x in st["actions"] if x["type"] == "freeze" and "ACC-7781" in x["targets"])
    assert freeze["evidence_path"]["ids"][0] == "UPI-JOBS"
    assert [h["to_agent"] for h in st["handoffs"]] == ["AGT-investigator", "AGT-action", "AGT-intake", "AGT-brain"]
    # every tool call went through MCP and carried the Cypher it ran
    assert all(ev.cypher for ev in out["trace"].events)


def test_scenario2_memory_links_new_number_to_same_ring():
    feed = apply_scenario2_feed("demobank")
    assert feed["applied"]
    before = T.recall_user("USR-PRIYA")["result"]
    first_ring = next(r["ring"] for r in before["reports"] if r.get("mentions") == "UPI-JOBS")
    out = process_report("USR-PRIYA", MSG2, AgentContext.create("demobank", "mcp"))
    f = out["findings"]
    assert f["ring_id"] == first_ring and f["already_known"] and f["new_members"] == ["ACC-9104"]
    assert any("D17" in m["content"] for m in f.get("known_memories", []))  # semantic memory hit
    assert [x["id"] for x in out["actions"]["frozen"]] == ["ACC-9104"]
    assert "Welcome back" in out["reply"] and "same scam group" in out["reply"]
    after = T.recall_user("USR-PRIYA")["result"]
    assert len(after["sessions"]) >= 2  # persistent conversation history
    pb = T.get_playbook("job_scam")["result"]
    assert pb["version"] >= 2 and "get_memories" in pb["content"]["steps"]  # procedural memory improved


def test_scenario3_company_brain_traces_decision():
    out = ask_brain("Why was ACC-5530 frozen and who approved it?", AgentContext.create("demobank", "mcp"))
    ans = out["answer"]
    for must in ["Arjun M.", "Meera K.", "P-07", "P-09", "TKT-4471", "C-112"]:
        assert must in ans, must
    out = ask_brain("Who on the team knows about KYC scams?", AgentContext.create("demobank", "mcp"))
    assert "Meera K." in out["answer"] or "Arjun M." in out["answer"]


def test_agent_decisions_are_in_company_brain():
    out = ask_brain("Why was ACC-7781 frozen?", AgentContext.create("demobank", "local"))
    assert "P-09" in out["answer"] and "agent" in out["answer"]


def test_tenants_are_isolated():
    assert T.recall_user("USR-PRIYA", tenant="suraksha")["result"]["known"] is False
    assert T.lookup_entity("hr.jobs2026@fakepay", tenant="suraksha")["result"]["found"] is False
    assert T.lookup_entity("hr.jobs2026@fakepay", tenant="demobank")["result"]["found"] is True


def test_freeze_guardrail_refuses_low_risk():
    uid = T.register_user("Guardrail Test")["result"]["user_id"]
    case = T.file_report(uid, "ACC-2207", "job_scam", "test")["result"]["case_id"]
    T.risk_score(["ACC-2207"])
    res = T.freeze_accounts(["ACC-2207"], case, "test")["result"]
    assert res["frozen"] == [] and "required by P-09" in res["refused"][0]["why"]
    json.dumps(res)
