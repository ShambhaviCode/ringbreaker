"""RingBreaker UI.   Run:  streamlit run app/streamlit_app.py"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402

import config  # noqa: E402
from agents.base import AgentContext  # noqa: E402
from agents.mcp_client import get_client  # noqa: E402
from agents.orchestrator import ask_brain, process_report  # noqa: E402
from app.graph_render import legend_html, render  # noqa: E402
from graph import tools as T  # noqa: E402
from graph import views as V  # noqa: E402
from graph.load import apply_scenario2_feed, load_tenant  # noqa: E402
from graph.queries import graph_stats  # noqa: E402

st.set_page_config(page_title="RingBreaker", page_icon=":material/hub:", layout="wide")

BANKS = {"demobank": "DemoBank", "suraksha": "SurakshaBank"}
AGENT_COLORS = {"AGT-intake": "#3E8E85", "AGT-investigator": "#2D3FA6", "AGT-action": "#C8102E",
                "AGT-brain": "#6B4FA0"}
AGENT_NAMES = {"AGT-intake": "Intake", "AGT-investigator": "Investigator", "AGT-action": "Action",
               "AGT-brain": "Brain"}
DAY1 = ("Hi, I paid Rs 18,500 as a registration fee for a work-from-home job to hr.jobs2026@fakepay. "
        "Now they have stopped replying. Please help.")
DAY2 = ("It's me again. Today I got a WhatsApp call from +91-90000-41177 offering the same job and asking for a "
        "'verification fee'. Is this the same people?")
QUESTIONS = ["Why was ACC-5530 frozen and who approved it?", "Who on the team knows about job scams?",
             "Why was ACC-7781 frozen?", "What is our current freeze policy?"]

st.markdown("""
<link href="https://fonts.googleapis.com/css2?family=Hind:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
html, body, [class*="css"], .stMarkdown, .stTextInput, .stButton, .stSelectbox, .stTabs, p, li, label, input, textarea {
  font-family: 'Hind', system-ui, sans-serif !important;
}
h1, h2, h3 { font-family: 'Hind', system-ui, sans-serif !important; letter-spacing: -0.01em; color: #1A2233; }
h1 { font-weight: 700; font-size: 2.1rem; margin-bottom: 0; }
.rb-sub { color: #4A5468; font-size: 1.08rem; margin: 0.1rem 0 1.2rem 0; max-width: 62ch; }
.rb-line { font-size: 1.25rem; line-height: 1.45; color: #1A2233; max-width: 70ch; margin: 0.2rem 0 1rem 0; }
.rb-line b { font-weight: 600; }
.rb-note { border-left: 3px solid #C9CFDA; padding: 0.15rem 0 0.15rem 0.7rem; margin: 0.35rem 0; line-height: 1.4; }
.rb-agent { font-weight: 600; margin-right: 0.35rem; }
.rb-legend { display: flex; flex-wrap: wrap; gap: 0.4rem 1.1rem; font-size: 0.92rem; color: #4A5468; margin: 0.3rem 0 0.6rem; }
.rb-legend-item { display: inline-flex; align-items: center; gap: 0.4rem; }
.rb-step { display: grid; grid-template-columns: 1.6rem 1fr; gap: 0.5rem; align-items: baseline; margin: 0.25rem 0; }
.rb-step-n { color: #2D3FA6; font-weight: 700; text-align: right; font-variant-numeric: tabular-nums; }
.rb-rel { color: #6A7385; font-size: 0.9rem; }
.rb-src { display: inline-block; border: 1px solid #C9CFDA; border-radius: 4px; padding: 0 0.4rem; margin: 0 0.3rem 0.3rem 0;
          font-size: 0.9rem; background: #FFFFFF; }
.rb-col-h { font-weight: 600; font-size: 1.05rem; margin-bottom: 0.3rem; }
.rb-quiet { color: #6A7385; }
[data-testid="stSidebar"] { border-right: 1px solid #DCE1E8; }
</style>
""", unsafe_allow_html=True)


def inr(x) -> str:
    """Indian digit grouping: 576494 -> 5,76,494"""
    n = int(round(x or 0))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if n < 0 else "") + "Rs " + s


# ------------------------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("### RingBreaker")
    tenant = st.selectbox("Bank", list(BANKS), format_func=lambda t: BANKS[t],
                          help="Each bank has its own isolated graph in the same FalkorDB instance.")
    try:
        stats = graph_stats(config.get_graph(tenant))
        st.caption(f"Graph `{config.graph_name(tenant)}`: {stats['nodes']:,} nodes, "
                   f"{stats['relationships']:,} relationships")
    except Exception as ex:  # noqa: BLE001
        st.error(f"Can't reach FalkorDB at {config.FALKORDB_HOST}:{config.FALKORDB_PORT}. Start it, then reload. ({ex})")
        st.stop()
    st.caption(f"Agents reach tools over **{config.TOOL_TRANSPORT.upper()}**. "
               f"LLM: **{config.LLM_PROVIDER}**" + (f" ({config.LLM_MODEL})" if config.LLM_MODEL else "")
               + (" - agents follow their graph playbooks." if config.LLM_PROVIDER == "none" else ""))
    if st.button("Reset demo data", help="Reload both banks from the synthetic dataset"):
        with st.spinner("Reloading both banks"):
            for t in config.TENANTS:
                load_tenant(t, verbose=False)
        st.session_state.clear()
        st.rerun()


def embed(page: str, height: int):
    if hasattr(st, "iframe"):
        st.iframe(page, height=height)
    else:  # older Streamlit
        components.html(page, height=height, scrolling=False)


def ctx_for(t):
    return AgentContext.create(client=get_client(t))


def show_trace(trace, show_tools=True):
    items = sorted([("note", n["ts"], n) for n in trace.notes] + [("tool", e.ts, e) for e in trace.events],
                   key=lambda x: x[1])
    for kind, _, e in items:
        if kind == "note":
            c = AGENT_COLORS.get(e["agent"], "#1A2233")
            st.markdown(f"<div class='rb-note' style='border-left-color:{c}'><span class='rb-agent' "
                        f"style='color:{c}'>{AGENT_NAMES.get(e['agent'], e['agent'])}</span>{e['text']}</div>",
                        unsafe_allow_html=True)
    if show_tools and trace.events:
        with st.expander(f"Tool calls over MCP ({len(trace.events)}) with the Cypher each one ran"):
            for e in trace.events:
                st.markdown(f"**{AGENT_NAMES.get(e.agent, e.agent)}** called `{e.tool}` "
                            f"<span class='rb-quiet'>{e.ms} ms</span>", unsafe_allow_html=True)
                if e.cypher:
                    st.code("\n\n".join(e.cypher[:4]) + ("\n\n..." if len(e.cypher) > 4 else ""), language="cypher")


# ------------------------------------------------------------------------------------------- header
st.markdown("<h1>RingBreaker</h1><p class='rb-sub'>A customer reports one scam payment. Agents follow the bank's graph "
            "to the whole ring behind it, freeze what the policy allows, and keep the evidence for every step.</p>",
            unsafe_allow_html=True)

tab_report, tab_case, tab_memory, tab_brain, tab_compare = st.tabs(
    ["Report a scam", "Investigation", "Memory", "Ask the fraud team", "Before and after"])

# ------------------------------------------------------------------------------------------- 1. report
with tab_report:
    left, right = st.columns([5, 6], gap="large")
    with left:
        users = T.find_user("Priya", tenant=tenant)["result"]["users"]
        who = st.radio("Customer", ["Priya S. (existing customer)", "New customer"], horizontal=True,
                       label_visibility="collapsed")
        if who.startswith("Priya") and users:
            user_id = users[0]["id"]
        elif who.startswith("Priya"):
            st.info("Priya is a DemoBank customer. Switch the bank in the sidebar.")
            user_id = None
        else:
            name = st.text_input("Customer name", value="Ravi K.")
            user_id = None
            if "new_user" not in st.session_state or st.session_state.get("new_user_name") != name:
                st.session_state.new_user = None
            st.session_state.new_user_name = name

        b1, b2 = st.columns(2)
        preset = None
        if b1.button("Day 1: Priya reports a UPI ID", width="stretch", disabled=not users):
            preset = DAY1
        if b2.button("Day 2: Priya returns", width="stretch", disabled=not users):
            feed = apply_scenario2_feed(tenant)
            if feed.get("applied"):
                st.toast(f"Overnight feed: new account {feed['account']} opened with {feed['phone']}")
            preset = DAY2
        typed = st.chat_input("Describe what happened, with the UPI ID, number or link")
        message = preset or typed

        if message:
            if not user_id and who == "New customer":
                if not st.session_state.new_user:
                    st.session_state.new_user = T.register_user(st.session_state.new_user_name,
                                                                tenant=tenant)["result"]["user_id"]
                user_id = st.session_state.new_user
            if preset and users:
                user_id = users[0]["id"]
            if user_id:
                with st.spinner("Agents are working the case"):
                    out = process_report(user_id, message, ctx_for(tenant))
                st.session_state.last = {"message": message, **out}
                if out.get("case_id"):
                    st.session_state.case_id = out["case_id"]

        last = st.session_state.get("last")
        if last:
            with st.chat_message("user"):
                st.write(last["message"])
            with st.chat_message("assistant", avatar=":material/shield:"):
                st.write(last["reply"])
            if last.get("case_id"):
                st.caption(f"Case {last['case_id']} handled in {last.get('seconds')} s. "
                           f"Open the Investigation tab to see the ring.")
        else:
            st.markdown("<p class='rb-quiet'>Pick a scenario or type a report. Everything the agents learn is "
                        "written to the graph.</p>", unsafe_allow_html=True)
    with right:
        st.markdown("<div class='rb-col-h'>What the agents did</div>", unsafe_allow_html=True)
        if st.session_state.get("last"):
            show_trace(st.session_state.last["trace"])
        else:
            st.markdown("<p class='rb-quiet'>The Intake, Investigator and Action agents will report each step here, "
                        "with every MCP tool call and its Cypher.</p>", unsafe_allow_html=True)

# ------------------------------------------------------------------------------------------- 2. investigation
with tab_case:
    cases = V.list_cases(tenant)
    if not cases:
        st.markdown("<p class='rb-quiet'>No investigated cases yet. Report a scam in the first tab.</p>",
                    unsafe_allow_html=True)
    else:
        ids = [c["case_id"] for c in cases]
        default = ids.index(st.session_state.case_id) if st.session_state.get("case_id") in ids else 0
        cid = st.selectbox("Case", ids, index=default, format_func=lambda i: next(
            f"{c['case_id']}  {c['reporter'] or ''}  reported {c['reported'] or ''}" for c in cases if c["case_id"] == i))
        view = V.case_view(tenant, cid)
        accts = [n for n in view["nodes"] if n.get("label") == "Account" and n.get("role") in ("member", "collector")]
        frozen = [n for n in accts if n.get("status") == "frozen"]
        review = [n for n in accts if n.get("action") == "review" and n.get("status") != "frozen"]
        collector = next((n["id"] for n in accts if n.get("role") == "collector"), "-")
        reviewer = review[0].get("reviewer") if review else None
        st.markdown(
            f"<p class='rb-line'><b>{view['reported']}</b> leads to ring <b>{view['ring_id']}</b>: {len(accts)} accounts, "
            f"{len(frozen)} frozen" + (f", {len(review)} with {reviewer} for review" if review else "") +
            f". {view['victims']} customers paid {inr(view['stolen'])} into it; the money ends at "
            f"<b>{collector}</b>.</p>", unsafe_allow_html=True)

        g_col, why_col = st.columns([7, 4], gap="large")
        explainable = [a for a in view["paths"]]
        with why_col:
            st.markdown("<div class='rb-col-h'>Why was this account acted on?</div>", unsafe_allow_html=True)
            pick = st.selectbox("Account", explainable, label_visibility="collapsed",
                                index=explainable.index(collector) if collector in explainable else 0) \
                if explainable else None
            if pick:
                p = view["paths"][pick]
                act = next((a for a in view["actions"] if pick in a["targets"] and a["type"] in ("freeze", "review")), {})
                st.markdown(f"{'Frozen' if act.get('type') == 'freeze' else 'Sent to review'} under "
                            f"**{act.get('policy')}** with risk **{act.get('risk_score')}**. The path from what the "
                            f"customer reported:", unsafe_allow_html=False)
                steps = []
                for i, name in enumerate(p["names"]):
                    rel = f" <span class='rb-rel'>{p['rels'][i].replace('_', ' ').lower()}</span>" \
                        if i < len(p["rels"]) else ""
                    steps.append(f"<div class='rb-step'><span class='rb-step-n'>{i + 1}</span>"
                                 f"<span>{name}{rel}</span></div>")
                st.markdown("".join(steps), unsafe_allow_html=True)
                rf = V.risk_factors(tenant, pick)
                if rf.get("factors"):
                    st.markdown(f"**Risk {rf['score']}**, made of:")
                    st.dataframe([{"Points": f["points"], "Why": f["factor"]} for f in rf["factors"]],
                                 hide_index=True, width="stretch")
        with g_col:
            st.markdown(legend_html(), unsafe_allow_html=True)
            embed(render(view, highlight=pick), 640)

        comp = next((a for a in view["actions"] if a["type"] == "complaint"), None)
        if comp:
            with st.expander("Cyber-crime complaint draft (for an analyst to submit)"):
                st.text(comp["text"])

# ------------------------------------------------------------------------------------------- 3. memory
with tab_memory:
    people = V.users_with_history(tenant)
    if not people:
        st.markdown("<p class='rb-quiet'>No conversations yet.</p>", unsafe_allow_html=True)
    else:
        pids = [p["id"] for p in people]
        uid = st.selectbox("Customer", pids, format_func=lambda i: next(p["name"] for p in people if p["id"] == i),
                           index=pids.index("USR-PRIYA") if "USR-PRIYA" in pids else 0)
        rec = T.recall_user(uid, tenant=tenant)["result"]
        c1, c2 = st.columns([6, 5], gap="large")
        with c1:
            st.markdown("<div class='rb-col-h'>Conversations, oldest first</div>", unsafe_allow_html=True)
            for s in sorted(rec["sessions"], key=lambda s: s["started_at"]):
                st.caption(f"Session {s['session']} on {s['started_at'][:16].replace('T', ' ')}")
                for m in s["messages"]:
                    with st.chat_message("user" if m["role"] == "user" else "assistant",
                                         avatar=None if m["role"] == "user" else ":material/shield:"):
                        st.write(m["text"])
        with c2:
            st.markdown("<div class='rb-col-h'>What the agents remember about this customer</div>",
                        unsafe_allow_html=True)
            for m in V.memories_about(tenant, uid):
                st.markdown(f"<div class='rb-note' style='border-left-color:{AGENT_COLORS.get(m['agent'])}'>"
                            f"<span class='rb-agent' style='color:{AGENT_COLORS.get(m['agent'])}'>"
                            f"{AGENT_NAMES.get(m['agent'])}, {m['kind']}</span>{m['content']}</div>",
                            unsafe_allow_html=True)
            cases_u = [r for r in rec["reports"] if r.get("case")]
            if cases_u:
                cid = st.selectbox("Handoffs for case", [r["case"] for r in cases_u][::-1])
                hs = T.get_case_state(cid, tenant=tenant)["result"]["handoffs"]
                st.dataframe([{"When": h["ts"][11:19], "From": AGENT_NAMES.get(h["from_agent"]),
                               "To": AGENT_NAMES.get(h["to_agent"]), "Note": h["note"]} for h in hs],
                             hide_index=True, width="stretch")
                ring = T.get_case_state(cid, tenant=tenant)["result"]["rings"]
                if ring:
                    infra = V.memories_about(tenant, "D17")
                    if infra:
                        st.markdown("<div class='rb-col-h'>Shared knowledge about device D17</div>",
                                    unsafe_allow_html=True)
                        for m in infra:
                            st.markdown(f"<div class='rb-note'>{m['content']}</div>", unsafe_allow_html=True)
        st.markdown("<div class='rb-col-h'>How the job-scam playbook changed</div>", unsafe_allow_html=True)
        st.dataframe([{"Version": p["version"], "Steps": " > ".join(p["content"]["steps"]),
                       "Learned from": p.get("learned_from") or "starting playbook", "Note": p["content"].get("note")}
                      for p in V.playbook_versions(tenant, "job_scam")], hide_index=True, width="stretch")

# ------------------------------------------------------------------------------------------- 4. brain
with tab_brain:
    st.markdown("<p class='rb-quiet'>Questions about people, cases, decisions and policies. Analysts' decisions and "
                "the agents' own freezes are in the same graph.</p>", unsafe_allow_html=True)
    cols = st.columns(len(QUESTIONS))
    asked = None
    for i, q in enumerate(QUESTIONS):
        if cols[i].button(q, width="stretch", key=f"q{i}"):
            asked = q
    typed_q = st.text_input("Your question", placeholder="Who approved the freeze on ACC-5530?")
    question = asked or typed_q
    if question:
        with st.spinner("Searching the graph"):
            out = ask_brain(question, ctx_for(tenant))
        st.markdown(f"### {question}")
        st.markdown(out["answer"])
        if out.get("sources"):
            st.markdown("Sources: " + "".join(f"<span class='rb-src'>{s}</span>" for s in out["sources"]),
                        unsafe_allow_html=True)
        ev = out["trace"].merged_evidence()
        if ev["nodes"]:
            view = {"nodes": [{**n, "role": None} for n in ev["nodes"]], "edges": ev["edges"], "paths": {}}
            embed(render(view, height=380), 400)
        show_trace(out["trace"])

# ------------------------------------------------------------------------------------------- 5. compare
with tab_compare:
    cases = V.list_cases(tenant)
    if not cases:
        st.markdown("<p class='rb-quiet'>Run a report first.</p>", unsafe_allow_html=True)
    else:
        cid = st.selectbox("Case to compare", [c["case_id"] for c in cases], key="cmp")
        view = V.case_view(tenant, cid)
        look = T.lookup_entity(view["reported"] or "", tenant=tenant)["result"]
        hist = V.ring_history(tenant, view["ring_id"]) if view["ring_id"] else {"reports": 0}
        accts = [n for n in view["nodes"] if n.get("label") == "Account" and n.get("role") in ("member", "collector")]
        a, b = st.columns(2, gap="large")
        with a:
            st.markdown("<div class='rb-col-h'>A lookup on the reported value</div>", unsafe_allow_html=True)
            st.markdown(f"- Reported: **{view['reported']}**\n"
                        f"- Account behind it: **{', '.join(x['id'] for x in look.get('accounts', [])) or 'none'}**\n"
                        f"- Earlier reports on this exact value: **{max(0, len(look.get('earlier_reports', [])) - 1)}**\n"
                        f"- Action possible: block one account, while the money has already moved on")
        with b:
            st.markdown("<div class='rb-col-h'>RingBreaker, following the graph</div>", unsafe_allow_html=True)
            frozen_n = sum(1 for n in accts if n.get("status") == "frozen")
            st.markdown(f"- Ring **{view['ring_id']}** of **{len(accts)}** accounts, linked by shared devices and IPs\n"
                        f"- **{hist['reports']}** earlier scam reports touched this ring without being connected"
                        + (f" (first on {str(hist['first_report'])[:10]})" if hist.get("first_report") else "") + "\n"
                        f"- **{view['victims']}** customers paid **{inr(view['stolen'])}** into it\n"
                        f"- Collector found by PageRank; **{frozen_n}** accounts frozen, each with its evidence path")
