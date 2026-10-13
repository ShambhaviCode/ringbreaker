"""Run the three demo scenarios from the command line.

    python -m scripts.demo --reset            # reload data, then run all three scenarios
    python -m scripts.demo --scenario 1       # just Track 01
    python -m scripts.demo --scenario 2       # Track 02 (runs scenario 1 first if Priya has no case yet)
    python -m scripts.demo --scenario 3       # Track 03
    python -m scripts.demo --quiet            # hide the tool-by-tool log
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from agents.base import AgentContext  # noqa: E402
from agents.brain import pretty  # noqa: E402
from agents.orchestrator import ask_brain, process_report  # noqa: E402
from graph import tools as T  # noqa: E402
from graph.load import apply_scenario2_feed, load_tenant  # noqa: E402

PRIYA = "USR-PRIYA"
MSG1 = ("Hi, I paid Rs 18,500 as a registration fee for a work-from-home job to hr.jobs2026@fakepay. "
        "Now they have stopped replying. Please help.")
MSG2 = ("It's me again. Today I got a WhatsApp call from +91-90000-41177 offering the same job and asking "
        "for a 'verification fee'. Is this the same people?")
Q3 = ["Why was ACC-5530 frozen and who approved it?",
      "Who on the team knows about job scams?",
      "Why was ACC-7781 frozen?"]

BOLD, DIM, END = "\033[1m", "\033[2m", "\033[0m"


def header(t):
    print(f"\n{BOLD}{'=' * 90}\n{t}\n{'=' * 90}{END}")


def show_trace(trace, quiet=False):
    events = sorted([("tool", e.ts, e) for e in trace.events] + [("note", n["ts"], n) for n in trace.notes],
                    key=lambda x: x[1])
    for kind, _, e in events:
        if kind == "note":
            print(f"  {BOLD}[{e['agent']}]{END} {e['text']}")
        elif not quiet:
            args = json.dumps(e.args, default=str)
            print(f"  {DIM}   mcp> {e.agent} -> {e.tool}({args[:110]}) {e.ms}ms, {len(e.cypher)} cypher{END}")


def scenario1(ctx_tenant, quiet):
    header("SCENARIO 1 (Track 01) - Priya reports a UPI handle; agents uncover the ring and act")
    print(f"Priya: {MSG1}\n")
    out = process_report(PRIYA, MSG1, AgentContext.create(ctx_tenant))
    show_trace(out["trace"], quiet)
    a = out["actions"]
    print(f"\n{BOLD}Result:{END} case {out['case_id']} | ring {a.get('ring_id')} | {len(a.get('frozen', []))} frozen | "
          f"{len(a.get('review', []))} to review | {a.get('alerted')} customers alerted | {out['seconds']}s")
    print(f"\n{BOLD}Reply to Priya:{END} {out['reply']}")
    print(f"\n{BOLD}Why was the collector frozen? (stored evidence path){END}")
    for f in a.get("frozen", []):
        if f["id"] == out["findings"].get("collector"):
            print(f"  {f['id']} risk {f['score']}: {f['evidence_path']}")
    return out


def scenario2(tenant, quiet):
    header("SCENARIO 2 (Track 02) - next day: Priya returns with a new number; memory links it to the same ring")
    st = T.recall_user(PRIYA, tenant=tenant)["result"]
    if not [r for r in st.get("reports", []) if r.get("case")]:
        print("(Priya has no case yet, running scenario 1 first)")
        scenario1(tenant, True)
    feed = apply_scenario2_feed(tenant)
    print(f"Bank feed overnight: {feed}")
    print(f"Priya: {MSG2}\n")
    out = process_report(PRIYA, MSG2, AgentContext.create(tenant))
    show_trace(out["trace"], quiet)
    print(f"\n{BOLD}Reply to Priya:{END} {out['reply']}")
    print(f"\n{BOLD}Handoffs for {out['case_id']} (shared state in the graph):{END}")
    for h in T.get_case_state(out["case_id"], tenant=tenant)["result"]["handoffs"]:
        print(f"  {h['ts']}  {h['from_agent']:>17} -> {h['to_agent']:<17} {h['note'][:80]}")
    return out


def scenario3(tenant, quiet):
    header("SCENARIO 3 (Track 03) - ask the fraud team's company brain")
    for q in Q3:
        print(f"\n{BOLD}Q: {q}{END}")
        out = ask_brain(q, AgentContext.create(tenant))
        show_trace(out["trace"], True)
        print(pretty(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="all", choices=["1", "2", "3", "all"])
    ap.add_argument("--reset", action="store_true", help="reload the synthetic data first")
    ap.add_argument("--tenant", default=config.DEFAULT_TENANT)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    print(f"LLM provider: {config.LLM_PROVIDER} | tool transport: {config.TOOL_TRANSPORT} | graph: "
          f"{config.graph_name(args.tenant)}")
    if args.reset:
        for t in config.TENANTS:
            load_tenant(t)
    if args.scenario in ("1", "all"):
        scenario1(args.tenant, args.quiet)
    if args.scenario in ("2", "all"):
        scenario2(args.tenant, args.quiet)
    if args.scenario in ("3", "all"):
        scenario3(args.tenant, args.quiet)


if __name__ == "__main__":
    main()
