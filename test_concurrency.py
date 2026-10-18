"""High-concurrency check: five victims report four different rings at the same time.
Two of them hit the same ring at the same moment. Each report runs the full Intake -> Investigator -> Action pipeline in its own thread with its own MCP session;
all coordination happens through the graph."""
from concurrent.futures import ThreadPoolExecutor

from agents.base import AgentContext
from agents.mcp_client import MCPToolClient
from agents.orchestrator import process_report
from graph import tools as T

REPORTS = [
    ("Farah S.", "Got SMS to update KYC on https://kyc-update-demobank.example/verify, money gone", "R2"),
    ("Bilal R.", "SMS said update KYC at https://kyc-update-demobank.example/verify or account blocked", "R2"),
    ("Chitra N.", "Joined crypto trading group https://cryptogrow-club.example/join, cannot withdraw", "R3"),
    ("Dev P.", "Called customer care from https://care-helpdesk-24x7.example/ticket, sent money for refund", "R4"),
    ("Esha M.", "Instant loan app https://instaloan-app.example/get took a processing fee", "R5"),
]


def run_one(item):
    name, msg, ring_key = item
    client = MCPToolClient("demobank")  # one MCP session per agent team
    try:
        uid = T.register_user(name)["result"]["user_id"]
        out = process_report(uid, msg, AgentContext.create(client=client))
        return ring_key, out
    finally:
        client.close()


def test_five_cases_concurrently(truth):
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(run_one, REPORTS))
    rings = {}
    for ring_key, out in results:
        f = out["findings"]
        assert f.get("collector") == truth[ring_key]["collector"], (ring_key, f.get("collector"))
        assert set(f["members"]) == set(truth[ring_key]["members"]), ring_key
        st = T.get_case_state(out["case_id"])["result"]
        assert st["case"]["status"] == "actioned"
        assert len(st["handoffs"]) == 4
        rings[ring_key] = f["ring_id"]
    # two reports about the same ring at the same moment share ONE ring; four rings in total, no cross-talk
    ids = [out["findings"]["ring_id"] for _, out in results]
    assert ids[0] == ids[1]
    assert len(set(ids)) == 4
