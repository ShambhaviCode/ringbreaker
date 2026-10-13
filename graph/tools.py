"""RingBreaker graph tools.

Every tool is a plain Python function that talks to FalkorDB and returns:
    {"tool": name, "result": {...}, "evidence": {"nodes": [...], "edges": [...]}, "cypher": [...]}

`evidence` is what the UI draws and what gets stored with every action; `cypher` is the exact
queries that produced the result. The MCP server (mcp_server/server.py) exposes these same
functions as MCP tools; agents can also import them directly (TOOL_TRANSPORT=local).
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

import config
from graph import queries as Q
from graph.schema import INFRA_RELS

AGENTS = {"AGT-intake", "AGT-investigator", "AGT-action", "AGT-brain"}


# =========================================================================== plumbing

def now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now():%m%d}-{uuid.uuid4().hex[:5].upper()}"


class Run:
    """One tool call: its own graph connection, the Cypher it ran, and the evidence it found."""

    def __init__(self, tool: str, tenant: str | None):
        self.tool = tool
        self.tenant = tenant or config.DEFAULT_TENANT
        self.g = config.get_graph(self.tenant)
        self.cypher: list[str] = []
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple, dict] = {}

    def q(self, cypher: str, params: dict | None = None) -> list[dict]:
        self.cypher.append(" ".join(cypher.split()))
        res = self.g.query(cypher, params or {})
        cols = [h[1] for h in res.header] if res.header else []
        return [{c: _plain(v) for c, v in zip(cols, row)} for row in res.result_set]

    def node(self, nid, label=None, name=None, **extra):
        if not nid:
            return
        cur = self.nodes.setdefault(nid, {"id": nid, "label": label or Q.label_for(nid) or "Node", "name": name or nid})
        if name and cur["name"] == nid:
            cur["name"] = name
        cur.update({k: v for k, v in extra.items() if v is not None})

    def edge(self, src, dst, rel, **props):
        if src and dst:
            self.edges.setdefault((src, dst, rel), {"src": src, "dst": dst, "type": rel, **props})

    def path(self, ids, labels, names, rels):
        for i, nid in enumerate(ids):
            self.node(nid, labels[i] if labels else None, names[i] if names else None)
        for i, rel in enumerate(rels):
            self.edge(ids[i], ids[i + 1], rel)

    def out(self, result) -> dict:
        return {"tool": self.tool, "tenant": self.tenant, "result": result,
                "evidence": {"nodes": list(self.nodes.values()), "edges": list(self.edges.values())},
                "cypher": self.cypher}


def _plain(v):
    if hasattr(v, "properties") and hasattr(v, "labels"):
        return {**v.properties, "_label": v.labels[0] if v.labels else None}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v


def normalize_value(value: str) -> tuple[str, str]:
    """Guess what kind of thing a victim reported, and normalise it."""
    v = value.strip().strip(".,;'\"")
    digits = re.sub(r"\D", "", v)
    if "@" in v and not v.startswith("http"):
        return "UPI", v.lower()
    if v.startswith("http") or re.search(r"\.[a-z]{2,}(/|$)", v):
        return "Link", v
    if len(digits) >= 10 and not v.upper().startswith(("ACC", "TXN")):
        d = digits[-10:]
        return "Phone", f"+91-{d[:5]}-{d[5:]}"
    if Q.label_for(v.upper()):
        return Q.label_for(v.upper()), v.upper()
    return "Unknown", v


def _memories(r: Run, node_id: str) -> list[dict]:
    rows = r.q(Q.anchor(Q.MEMORIES_ABOUT, "x", node_id), {"id": node_id})
    for m in rows:
        r.node(m["id"], "Memory", (m["content"] or "")[:60])
        r.edge(m["id"], node_id, "ABOUT")
    return rows


# =========================================================================== Track 01: investigate

def lookup_entity(value: str, tenant: str = "") -> dict:
    """Find a reported UPI handle, phone number, link or account in the graph, the accounts behind it,
    earlier scam reports that mention it, and anything agents already remember about it."""
    r = Run("lookup_entity", tenant)
    kind, v = normalize_value(value)
    rows = []
    queries = {"UPI": Q.LOOKUP_UPI, "Phone": Q.LOOKUP_PHONE, "Link": Q.LOOKUP_LINK, "Account": Q.LOOKUP_ACCOUNT}
    order = [kind] if kind in queries else ["UPI", "Phone", "Link", "Account"]
    for k in order:
        rows = r.q(queries[k], {"v": v})
        if rows:
            break
    if not rows:
        return r.out({"found": False, "searched_for": v, "kind": kind,
                      "message": f"No {kind if kind != 'Unknown' else 'entity'} matching {v!r} in the graph."})
    ent = {"id": rows[0]["entity_id"], "label": rows[0]["entity_label"], "name": rows[0]["entity_name"]}
    r.node(ent["id"], ent["label"], ent["name"])
    accounts = []
    for row in rows:
        if not row.get("account_id"):
            continue
        accounts.append({"id": row["account_id"], "holder": row["holder"], "status": row["status"],
                         "opened_at": row["opened_at"]})
        r.node(row["account_id"], "Account", row["holder"], status=row["status"])
        if row["rel"] == "LINKED_TO":
            r.edge(ent["id"], row["account_id"], "LINKED_TO")
        elif row["rel"] == "REGISTERED_PHONE":
            r.edge(row["account_id"], ent["id"], "REGISTERED_PHONE")
        elif row["rel"] == "PROMOTED_BY":
            r.node(row["via_phone"], "Phone")
            r.edge(ent["id"], row["via_phone"], "PROMOTED_BY")
            r.edge(row["account_id"], row["via_phone"], "REGISTERED_PHONE")
    reports = r.q(Q.anchor(Q.REPORTS_MENTIONING, "x", ent["id"]), {"id": ent["id"]})
    for rep in reports:
        r.node(rep["report_id"], "ScamReport", f"report {rep['report_id']}")
        r.edge(rep["report_id"], ent["id"], "MENTIONS")
    memories = _memories(r, ent["id"])
    for a in accounts:
        memories += _memories(r, a["id"])
    return r.out({"found": True, "entity": ent, "accounts": accounts, "earlier_reports": reports,
                  "memories": memories})


def expand_neighbors(node_id: str, hops: int = 2, rel_types: list[str] | None = None, tenant: str = "") -> dict:
    """Multi-hop expansion from a node (1-4 hops) over infrastructure links (UPI, phone, device, IP, link).
    Returns every account reached, how many hops away, and through what."""
    r = Run("expand_neighbors", tenant)
    label = Q.label_for(node_id)
    if not label:
        return r.out({"error": f"unknown node id {node_id}"})
    rels = Q.rel_union(rel_types or ["LINKED_TO", "PROMOTED_BY"] + INFRA_RELS)
    rows = r.q(Q.expand_query(label, Q.hops(hops, 1, 4), rels), {"id": node_id})
    reached: dict[str, dict] = {}
    for row in rows:
        r.path(row["ids"], row["labels"], row["names"], row["rels"])
        nid, lab = row["end_id"], row["end_label"]
        d = len(row["rels"])
        if nid not in reached or reached[nid]["hops"] > d:
            reached[nid] = {"id": nid, "label": lab, "hops": d,
                            "via": " -> ".join(f"{n}" for n in row["names"])}
    accounts = sorted([x for x in reached.values() if x["label"] == "Account"], key=lambda x: x["hops"])
    infra = sorted([x for x in reached.values() if x["label"] in ("Device", "IP", "Phone")], key=lambda x: x["hops"])
    counts = {}
    for x in reached.values():
        counts[x["label"]] = counts.get(x["label"], 0) + 1
    return r.out({"start": node_id, "hops": hops, "counts": counts, "accounts": accounts[:60],
                  "infrastructure": infra[:40]})


def find_shared_infrastructure(account_ids: list[str], tenant: str = "") -> dict:
    """Devices, IPs and phones that these accounts share with OTHER accounts.
    Infrastructure shared by 3+ accounts is a strong mule-ring signal."""
    r = Run("find_shared_infrastructure", tenant)
    rows = r.q(Q.SHARED_INFRA, {"ids": account_ids})
    shared = []
    for row in rows:
        r.node(row["infra_id"], row["infra_label"])
        for a in set(row["from_accounts"]) | set(row["other_accounts"]):
            r.node(a, "Account")
            r.edge(a, row["infra_id"], row["rel"])
        n = len(set(row["from_accounts"]) | set(row["other_accounts"]))
        shared.append({"id": row["infra_id"], "type": row["infra_label"], "accounts_sharing": n,
                       "accounts": sorted(set(row["from_accounts"]) | set(row["other_accounts"])),
                       "suspicious": n >= 3})
    return r.out({"shared": shared, "suspicious_count": sum(s["suspicious"] for s in shared)})


def _seed_accounts(r: Run, seed: str) -> list[str]:
    label = Q.label_for(seed)
    if label == "Account":
        return [seed]
    q = {
        "UPI": "MATCH (:UPI {id: $id})-[:LINKED_TO]->(a:Account) RETURN a.id AS id",
        "Phone": "MATCH (a:Account)-[:REGISTERED_PHONE]->(:Phone {id: $id}) RETURN a.id AS id",
        "Link": "MATCH (:Link {id: $id})-[:PROMOTED_BY]->(:Phone)<-[:REGISTERED_PHONE]-(a:Account) RETURN a.id AS id",
        "Device": "MATCH (a:Account)-[:USED_DEVICE]->(:Device {id: $id}) RETURN a.id AS id",
        "IP": "MATCH (a:Account)-[:LOGGED_IN_FROM]->(:IP {id: $id}) RETURN a.id AS id",
    }.get(label)
    return [x["id"] for x in r.q(q, {"id": seed})] if q else []


def detect_ring(seed_node_id: str, case_id: str = "", scam_type: str = "", min_size: int = 4,
                agent: str = "AGT-investigator", tenant: str = "") -> dict:
    """Find the scam ring around a seed (account, UPI, phone, link or device) using connected components
    (algo.WCC) over shared infrastructure, cross-checked with money communities (algo.labelPropagation).
    Saves the ring as a Ring node (or grows an existing ring) and links it to the case."""
    r = Run("detect_ring", tenant)
    seeds = _seed_accounts(r, seed_node_id)
    if not seeds:
        return r.out({"ring": None, "message": f"No account behind {seed_node_id}."})
    comps = [x["componentId"] for x in r.q(Q.WCC_SEED_COMPONENTS, {"seeds": seeds})]
    rows = r.q(Q.WCC_INFRA, {"components": comps, "seeds": seeds})
    accounts = sorted({x["id"] for x in rows if x["label"] == "Account"})
    infra = sorted({x["id"] for x in rows if x["label"] != "Account"})
    if len(accounts) < min_size:
        for a in accounts:
            r.node(a, "Account")
        return r.out({"ring": None, "component_accounts": accounts,
                      "message": f"Only {len(accounts)} account(s) share infrastructure with the seed; "
                                 f"looks like normal sharing (household), not a ring."})

    for e in r.q(Q.COMPONENT_EDGES, {"ids": accounts, "infra": infra}):
        r.node(e["src"], "Account")
        r.node(e["dst"])
        r.edge(e["src"], e["dst"], e["rel"])
    flows = r.q(Q.RING_FLOWS, {"ids": accounts})
    for f in flows:
        r.edge(f["src"], f["dst"], "PAID", amount=f["total"])

    # community detection on the money graph as a second, independent signal
    comm = r.q(Q.LABEL_PROPAGATION_MONEY, {"ids": accounts})
    by_comm: dict = {}
    for c in comm:
        by_comm[c["communityId"]] = by_comm.get(c["communityId"], 0) + 1
    agreement = round(max(by_comm.values()) / len(accounts), 2) if by_comm else 0

    existing = r.q(Q.EXISTING_RINGS, {"ids": accounts})
    # deterministic id (lowest account number in the ring) so two concurrent investigations MERGE the same ring
    ring_id = existing[0]["ring_id"] if existing else f"RING-{min(accounts).split('-', 1)[1]}"
    before = []
    if existing:
        before = [m["id"] for m in r.q(Q.RING_MEMBERS, {"ring_id": ring_id})]
    r.q(Q.UPSERT_RING, {"ring_id": ring_id, "ids": accounts, "ts": now(), "scam_type": scam_type or "unknown",
                        "agent": agent})
    if case_id:
        r.q(Q.LINK_CASE_RING, {"case_id": case_id, "ring_id": ring_id})
    members = r.q(Q.RING_MEMBERS, {"ring_id": ring_id})
    for m in members:
        r.node(m["id"], "Account", m["holder"], status=m["status"])
        r.edge(m["id"], ring_id, "MEMBER_OF")
    r.node(ring_id, "Ring", f"Ring {ring_id}")
    shared_counts = {}
    for e in r.edges.values():
        if e["type"] in INFRA_RELS:
            shared_counts[e["dst"]] = shared_counts.get(e["dst"], 0) + 1
    key_infra = sorted([{"id": k, "accounts": v} for k, v in shared_counts.items() if v >= 2],
                       key=lambda x: -x["accounts"])
    return r.out({
        "ring_id": ring_id, "size": len(members), "already_known": bool(existing),
        "new_members": sorted(set(m["id"] for m in members) - set(before)) if existing else [],
        "members": [{"id": m["id"], "status": m["status"]} for m in members],
        "seed_accounts": seeds, "shared_infrastructure": key_infra,
        "internal_money_flows": len(flows), "internal_money_total": sum(f["total"] for f in flows),
        "money_community_agreement": agreement,
        "method": "algo.WCC over Account/Device/IP/Phone + algo.labelPropagation over PAID",
    })


def rank_ring_members(ring_id: str, tenant: str = "") -> dict:
    """PageRank over the money graph (algo.pageRank on Account/PAID) inside a ring. The top account is the
    collector (ringleader). Also labels fronts (take victim money) and layers (pass it on)."""
    r = Run("rank_ring_members", tenant)
    members = {m["id"]: m for m in r.q(Q.RING_MEMBERS, {"ring_id": ring_id})}
    if not members:
        return r.out({"error": f"ring {ring_id} not found"})
    ids = list(members)
    pr = {x["id"]: x["score"] for x in r.q(Q.PAGERANK_PAID, {"ids": ids})}
    flows = r.q(Q.RING_FLOWS, {"ids": ids})
    inflow = r.q(Q.RING_INFLOW, {"ids": ids})
    victim_in = {}
    for v in inflow:
        victim_in[v["paid_to"]] = victim_in.get(v["paid_to"], 0) + v["total"]
    ring_in = {}
    for f in flows:
        ring_in[f["dst"]] = ring_in.get(f["dst"], 0) + f["total"]
        r.edge(f["src"], f["dst"], "PAID", amount=f["total"])
    ranked = sorted(ids, key=lambda i: -pr.get(i, 0))
    out = []
    for i, aid in enumerate(ranked):
        role = "collector" if i == 0 else ("front" if victim_in.get(aid) else "layer")
        out.append({"id": aid, "pagerank": round(pr.get(aid, 0), 5), "role": role,
                    "money_from_victims": victim_in.get(aid, 0), "money_from_ring": ring_in.get(aid, 0),
                    "status": members[aid]["status"]})
        r.node(aid, "Account", members[aid]["holder"], role=role, status=members[aid]["status"])
    return r.out({"ring_id": ring_id, "collector": ranked[0], "ranked": out,
                  "victim_accounts": len({v["victim_account"] for v in inflow}),
                  "total_from_victims": sum(v["total"] for v in inflow),
                  "method": "algo.pageRank('Account','PAID')"})


def money_trail(from_id: str, to_id: str, tenant: str = "") -> dict:
    """Shortest money path between two accounts over PAID edges (algo.SPpaths), with the amount on each hop.
    from_id may also be a UPI/phone id; it is resolved to its account."""
    r = Run("money_trail", tenant)
    src = from_id if Q.label_for(from_id) == "Account" else (_seed_accounts(r, from_id) or [None])[0]
    if not src:
        return r.out({"error": f"no account behind {from_id}"})
    rows = r.q(Q.MONEY_TRAIL, {"src": src, "dst": to_id})
    if not rows:
        return r.out({"found": False, "message": f"No money path from {src} to {to_id} within 6 hops."})
    ids, amounts = rows[0]["ids"], rows[0]["amounts"]
    hops = []
    for i, amt in enumerate(amounts):
        r.node(ids[i], "Account")
        r.node(ids[i + 1], "Account")
        r.edge(ids[i], ids[i + 1], "PAID", amount=amt)
        hops.append({"from": ids[i], "to": ids[i + 1], "amount": amt})
    return r.out({"found": True, "path": ids, "hops": hops, "hop_count": len(hops),
                  "method": "algo.SPpaths over PAID"})


def risk_score(account_ids: list[str], tenant: str = "") -> dict:
    """Explainable 0-100 fraud risk score from graph features: ring membership, shared devices/IPs,
    frozen neighbours, scam reports, fan-in, money from ring members, pass-through behaviour, account age,
    and being the ring's PageRank collector. Saves the score and its factors on each account."""
    r = Run("risk_score", tenant)
    rows = r.q(Q.RISK_FEATURES, {"ids": account_ids})
    collectors = set()
    for ring_id in {x["ring_id"] for x in rows if x["ring_id"]}:
        ids = [m["id"] for m in r.q(Q.RING_MEMBERS, {"ring_id": ring_id})]
        top = r.q(Q.PAGERANK_PAID, {"ids": ids})
        if top:
            collectors.add(top[0]["id"])
    today = datetime.now()
    scored, writes = [], []
    for x in rows:
        f = []
        if x["in_ring"]:
            f.append((f"member of detected ring {x['ring_id']}", 25))
        if x["sharing_accounts"]:
            f.append((f"shares devices/IPs with {x['sharing_accounts']} other account(s)",
                      min(20, 5 * x["sharing_accounts"])))
        if x["frozen_neighbours"]:
            f.append((f"shares devices/IPs with {x['frozen_neighbours']} frozen account(s)",
                      min(15, 5 * x["frozen_neighbours"])))
        if x["reports"]:
            f.append((f"mentioned (directly or via its UPI/phone/link) in {x['reports']} scam report(s)",
                      min(25, 8 * x["reports"])))
        if x["distinct_senders"] >= 5:
            f.append((f"receives money from {x['distinct_senders']} different senders", min(10, x["distinct_senders"])))
        if x["ring_senders"]:
            f.append((f"receives money from {x['ring_senders']} ring member(s)", min(15, 5 * x["ring_senders"])))
        inflow, outflow = x["inflow"] or 0, x["outflow"] or 0
        if inflow > 5000 and 0.7 <= outflow / inflow <= 1.05:
            f.append((f"passes on {outflow / inflow:.0%} of incoming money (layering)", 10))
        try:
            age = (today - datetime.fromisoformat(x["opened_at"])).days
            if age < 120:
                f.append((f"account only {max(age, 0)} days old", 5))
        except (TypeError, ValueError):
            pass
        if x["id"] in collectors:
            f.append(("top PageRank in its ring: where the ring's money ends up (collector)", 10))
        score = min(100, sum(p for _, p in f))
        factors = [{"factor": t, "points": p} for t, p in f]
        scored.append({"id": x["id"], "score": score, "status": x["status"], "factors": factors})
        writes.append({"id": x["id"], "score": score, "factors": json.dumps(factors), "ts": now()})
        r.node(x["id"], "Account", risk_score=score)
    if writes:
        r.q(Q.SET_RISK, {"rows": writes})
    scored.sort(key=lambda s: -s["score"])
    return r.out({"scores": scored})


# =========================================================================== Track 01: act

def _policy(r: Run, topic: str) -> dict:
    rows = r.q(Q.CURRENT_POLICY, {"topic": topic})
    if not rows:
        return {}
    p = dict(rows[0]["p"])
    p["replaces"] = rows[0]["replaces"]
    r.node(p["id"], "Policy", f"{p['code']} {p['title']}")
    return p


def _case_anchor(r: Run, case_id: str) -> tuple[dict, str | None]:
    st = r.q(Q.CASE_STATE, {"case_id": case_id})
    if not st:
        return {}, None
    s = st[0]
    anchor = next((m["id"] for m in s["mentions"] if m.get("id") and m["label"] in ("UPI", "Phone", "Link")), None)
    anchor = anchor or next((m["id"] for m in s["mentions"] if m.get("id")), None)
    return s, anchor


def _evidence_path(r: Run, anchor: str | None, account: str) -> dict:
    if not anchor:
        return {}
    rows = r.q(Q.evidence_path_query(Q.label_for(anchor)), {"src": anchor, "dst": account})
    if not rows:
        return {}
    p = rows[0]
    r.path(p["ids"], p["labels"], p["names"], p["rels"])
    text = " -> ".join(
        f"{p['names'][i]}" + (f" -[{p['rels'][i]}]" if i < len(p["rels"]) else "") for i in range(len(p["ids"])))
    return {"ids": p["ids"], "labels": p["labels"], "names": p["names"], "rels": p["rels"], "text": text}


def freeze_accounts(account_ids: list[str], case_id: str, reason: str, agent: str = "AGT-action",
                    tenant: str = "") -> dict:
    """Freeze accounts (simulated) for a case. Guardrail: only accounts whose stored risk score meets the
    CURRENT freeze policy threshold are frozen; others are refused. Each freeze stores its evidence path
    (from the reported entity to the account), the Cypher, the policy and a Decision node."""
    r = Run("freeze_accounts", tenant)
    pol = _policy(r, "freeze")
    th, review_th = pol.get("freeze_threshold", 80), pol.get("review_threshold", 80)
    _, anchor = _case_anchor(r, case_id)
    scores = {x["id"]: x for x in r.q(Q.FREEZE_CANDIDATES, {"ids": account_ids})}
    frozen, refused = [], []
    for aid in account_ids:
        s = scores.get(aid)
        if not s:
            refused.append({"id": aid, "why": "not found"})
            continue
        if s["status"] == "frozen":
            refused.append({"id": aid, "why": "already frozen"})
            continue
        if s["pending_review"]:
            refused.append({"id": aid, "why": f"pending human review by {s['pending_review']}; a person decides"})
            continue
        score = s["score"] or 0
        if score < th:
            band = "review" if score >= review_th else "below threshold"
            refused.append({"id": aid, "score": score, "why": f"score {score} < {th} required by {pol.get('code')}",
                            "suggest": "flag_for_review" if band == "review" else "no action"})
            continue
        path = _evidence_path(r, anchor, aid)
        act_id, dec_id = new_id("ACT"), new_id("DEC")
        r.q(Q.FREEZE_ONE, {
            "acc": aid, "case_id": case_id, "policy": pol["code"], "agent": agent, "ts": now(),
            "act_id": act_id, "dec_id": dec_id, "reason": reason, "score": score,
            "path_json": json.dumps(path), "cypher": r.cypher[-1] if path else ""})
        r.node(aid, "Account", status="frozen")
        frozen.append({"id": aid, "score": score, "action_id": act_id, "decision_id": dec_id,
                       "evidence_path": path.get("text", "")})
    return r.out({"policy": pol.get("code"), "freeze_threshold": th, "review_threshold": review_th,
                  "frozen": frozen, "refused": refused})


def flag_for_review(account_ids: list[str], case_id: str, reason: str, agent: str = "AGT-action",
                    tenant: str = "") -> dict:
    """Send medium-risk accounts to human review. The reviewer is picked from the graph: an analyst who is
    EXPERT_IN the case's scam type and has the fewest open reviews."""
    r = Run("flag_for_review", tenant)
    pol = _policy(r, "freeze")
    state, anchor = _case_anchor(r, case_id)
    st = (state.get("case") or {}).get("scam_type") or "job_scam"
    rev = r.q(Q.PICK_REVIEWER, {"scam_type": st})
    if not rev:
        return r.out({"error": f"no analyst knows {st}"})
    rev = rev[0]
    r.node(rev["id"], "Analyst", rev["name"])
    out = []
    for aid in account_ids:
        score = (r.q("MATCH (a:Account {id: $id}) RETURN a.risk_score AS s", {"id": aid}) or [{"s": 0}])[0]["s"]
        path = _evidence_path(r, anchor, aid)
        act_id = new_id("ACT")
        r.q(Q.REVIEW_ONE, {"acc": aid, "case_id": case_id, "analyst": rev["id"], "agent": agent,
                           "policy": pol.get("code", "P-09"), "ts": now(), "act_id": act_id, "reason": reason,
                           "score": score, "path_json": json.dumps(path), "cypher": r.cypher[-1] if path else ""})
        r.edge(act_id, rev["id"], "ASSIGNED_TO")
        out.append({"id": aid, "score": score, "action_id": act_id, "assigned_to": rev["name"],
                    "evidence_path": path.get("text", "")})
    return r.out({"reviewer": rev, "scam_type": st, "flagged": out, "policy": pol.get("code")})


def send_alert(case_id: str, message: str = "", agent: str = "AGT-action", tenant: str = "") -> dict:
    """Warn every other customer who paid an account in this case's ring (found by following PAID edges into
    the ring), under the current alert policy. The reporter of the case is not re-alerted."""
    r = Run("send_alert", tenant)
    pol = _policy(r, "alert")
    state, _ = _case_anchor(r, case_id)
    rings = state.get("rings") or []
    if not rings:
        return r.out({"error": "case has no detected ring yet"})
    ids = [m["id"] for m in r.q(Q.RING_MEMBERS, {"ring_id": rings[0]})]
    victims = r.q(Q.RING_INFLOW, {"ids": ids})
    users = sorted({v["user_id"] for v in victims if v["user_id"] and v["user_id"] != state.get("user_id")})
    for v in victims:
        if v["user_id"] in users:
            r.node(v["user_id"], "User", v["holder"])
            r.node(v["victim_account"], "Account", v["holder"])
            r.edge(v["user_id"], v["victim_account"], "HOLDS")
            r.edge(v["victim_account"], v["paid_to"], "PAID", amount=v["total"])
    message = message or ("Safety alert from your bank: an account you paid recently has been frozen as part of a "
                          "scam ring. Do not send more money or share OTPs. If you were asked to pay a fee for a "
                          "job, KYC update, loan or investment, reply to this message to report it.")
    act_id = new_id("ACT")
    r.q(Q.CREATE_ALERT, {"case_id": case_id, "agent": agent, "policy": pol.get("code", "P-03"), "act_id": act_id,
                         "ts": now(), "message": message, "user_ids": users, "cypher": r.cypher[-1]})
    unregistered = len({v["victim_account"] for v in victims if not v["user_id"]})
    return r.out({"action_id": act_id, "policy": pol.get("code"), "alerted_users": len(users), "user_ids": users,
                  "victim_accounts_without_app_user": unregistered, "message": message})


def draft_complaint(case_id: str, agent: str = "AGT-action", tenant: str = "") -> dict:
    """Write a cyber-crime complaint draft for the case, built only from graph facts: the report, the ring,
    frozen accounts, the money traced and the evidence paths. Stored as an ActionTaken for an analyst to submit."""
    r = Run("draft_complaint", tenant)
    pol = _policy(r, "complaint")
    state, _ = _case_anchor(r, case_id)
    if not state:
        return r.out({"error": f"case {case_id} not found"})
    rings = state.get("rings") or []
    actions = r.q(Q.CASE_ACTIONS, {"case_id": case_id})
    frozen = [a for a in actions if a["type"] == "freeze"]
    lines = [f"CYBER-CRIME COMPLAINT DRAFT - case {case_id} ({now()[:10]})",
             f"Complainant: {state.get('user_name')} ({state.get('user_id')}); report {state.get('report')}.",
             f"Reported: {state.get('reported_value') or ', '.join(m['id'] for m in state['mentions'] if m.get('id'))}.",
             f"Description: {state.get('description')}"]
    if rings:
        ids = [m["id"] for m in r.q(Q.RING_MEMBERS, {"ring_id": rings[0]})]
        inflow = r.q(Q.RING_INFLOW, {"ids": ids})
        lines.append(f"Linked ring {rings[0]}: {len(ids)} accounts; {len({v['victim_account'] for v in inflow})} "
                     f"victim accounts paid Rs {sum(v['total'] for v in inflow):,.0f} into it.")
    lines.append(f"Accounts frozen under {frozen[0]['policy'] if frozen else '-'}: "
                 + (", ".join(t for a in frozen for t in a["targets"]) or "none"))
    for a in frozen[:20]:
        p = json.loads(a["evidence_path"] or "{}")
        if p.get("text"):
            lines.append(f"  - {a['targets'][0]} (risk {a['risk_score']}): {p['text']}")
    lines.append("Evidence paths and queries are stored in the bank's graph (policy P-11). Submit after analyst review.")
    text = "\n".join(lines)
    act_id = new_id("ACT")
    r.q(Q.CREATE_COMPLAINT, {"case_id": case_id, "agent": agent, "policy": pol.get("code", "P-05"),
                             "act_id": act_id, "ts": now(), "text": text, "cypher": " | ".join(r.cypher[:4])})
    return r.out({"action_id": act_id, "policy": pol.get("code"), "text": text})


# =========================================================================== Track 02: memory + coordination

def find_user(query: str, tenant: str = "") -> dict:
    """Find a customer by user id or name."""
    r = Run("find_user", tenant)
    rows = r.q(Q.FIND_USER, {"q": query.strip()})
    for u in rows:
        r.node(u["id"], "User", u["name"])
    return r.out({"users": rows})


def register_user(name: str, language: str = "en", tenant: str = "") -> dict:
    """Create a customer profile for someone reporting for the first time."""
    r = Run("register_user", tenant)
    uid = "USR-" + uuid.uuid4().hex[:6].upper()
    r.q(Q.CREATE_USER, {"uid": uid, "name": name, "language": language, "ts": now()})
    r.node(uid, "User", name)
    return r.out({"user_id": uid, "name": name})


def recall_user(user_id: str, tenant: str = "") -> dict:
    """Everything the bank's agents remember about a customer: accounts, earlier reports and their cases and
    rings, past conversation sessions (persistent history) and episodic memories."""
    r = Run("recall_user", tenant)
    rows = r.q(Q.RECALL_USER, {"uid": user_id})
    if not rows or not rows[0]["id"]:
        return r.out({"known": False, "user_id": user_id})
    u = rows[0]
    r.node(u["id"], "User", u["name"])
    reports = [x for x in u["reports"] if x.get("report")]
    for x in reports:
        r.node(x["report"], "ScamReport")
        r.edge(u["id"], x["report"], "FILED")
        if x.get("case"):
            r.node(x["case"], "Case")
            r.edge(x["report"], x["case"], "OPENED")
        if x.get("ring"):
            r.node(x["ring"], "Ring")
            r.edge(x["case"], x["ring"], "PART_OF_RING")
    sessions = r.q(Q.USER_SESSIONS, {"uid": user_id, "limit": 5})
    for s in sessions:
        s["messages"] = [m for m in s["messages"] if m.get("text")][-8:]
        r.node(s["session"], "Session")
        r.edge(u["id"], s["session"], "HAD_SESSION")
    memories = _memories(r, user_id)
    return r.out({"known": True, "user": {"id": u["id"], "name": u["name"], "language": u["language"]},
                  "accounts": u["accounts"], "reports": reports, "sessions": sessions, "memories": memories})


def open_session(user_id: str, channel: str = "app-chat", tenant: str = "") -> dict:
    """Start a new conversation session for a customer (stored in the graph, so history persists)."""
    r = Run("open_session", tenant)
    sid = new_id("SES")
    r.q(Q.OPEN_SESSION, {"uid": user_id, "sid": sid, "ts": now(), "channel": channel})
    return r.out({"session_id": sid})


def log_message(session_id: str, role: str, text: str, tenant: str = "") -> dict:
    """Append one message (role = user | intake | system) to a session."""
    r = Run("log_message", tenant)
    mid = new_id("MSG")
    r.q(Q.LOG_MESSAGE, {"sid": session_id, "mid": mid, "role": role, "text": text, "ts": now()})
    return r.out({"message_id": mid})


def file_report(user_id: str, reported_value: str, scam_type: str, description: str, session_id: str = "",
                tenant: str = "") -> dict:
    """Create a ScamReport from a customer and open a Case for it. The reported UPI/phone/link is linked with
    MENTIONS (an unknown phone number is added to the graph so future reports connect to it)."""
    r = Run("file_report", tenant)
    kind, v = normalize_value(reported_value)
    rid, cid = new_id("RPT"), new_id("C")
    r.q(Q.FILE_REPORT, {"uid": user_id, "rid": rid, "cid": cid, "ts": now(), "scam_type": scam_type,
                        "description": description, "value": v})
    target = None
    lookup = {"UPI": "MATCH (x:UPI {handle: $v}) RETURN x.id AS id",
              "Phone": "MATCH (x:Phone {number: $v}) RETURN x.id AS id",
              "Link": "MATCH (x:Link) WHERE x.url = $v OR x.domain = $v OR x.url CONTAINS $v RETURN x.id AS id",
              "Account": "MATCH (x:Account {id: $v}) RETURN x.id AS id"}.get(kind)
    if lookup:
        found = r.q(lookup, {"v": v})
        target = found[0]["id"] if found else None
    if not target and kind == "Phone":
        target = r.q(Q.CREATE_PHONE_IF_MISSING, {"number": v, "pid": new_id("PH"), "ts": now()})[0]["p.id"]
    if target:
        r.q(Q.anchor(Q.REPORT_MENTIONS, "x", target), {"rid": rid, "target": target})
        r.edge(rid, target, "MENTIONS")
    if session_id:
        r.q(Q.LINK_SESSION_CASE, {"sid": session_id, "cid": cid})
    r.node(user_id, "User")
    r.node(rid, "ScamReport")
    r.node(cid, "Case")
    r.edge(user_id, rid, "FILED")
    r.edge(rid, cid, "OPENED")
    return r.out({"report_id": rid, "case_id": cid, "mentions": target, "kind": kind, "value": v,
                  "known_entity": bool(target)})


def write_memory(agent_id: str, kind: str, content: str, about_node_id: str = "", confidence: float = 0.8,
                 tenant: str = "") -> dict:
    """Store a memory (episodic | semantic | procedural) learned by an agent, attached to the node it is about.
    Memories are shared: every agent can read them back with get_memories / recall_user / lookup_entity."""
    r = Run("write_memory", tenant)
    if kind not in ("episodic", "semantic", "procedural"):
        return r.out({"error": "kind must be episodic, semantic or procedural"})
    mid = new_id("MEM")
    q = Q.anchor(Q.WRITE_MEMORY, "x", about_node_id) if about_node_id else Q.WRITE_MEMORY
    r.q(q, {"agent": agent_id, "mid": mid, "kind": kind, "content": content, "ts": now(),
            "confidence": confidence, "about": about_node_id})
    r.node(mid, "Memory", content[:60])
    r.edge(agent_id, mid, "LEARNED")
    r.edge(mid, about_node_id, "ABOUT")
    return r.out({"memory_id": mid})


def get_memories(node_id: str, tenant: str = "") -> dict:
    """What agents have learned about a node (device, account, user, ring...)."""
    r = Run("get_memories", tenant)
    return r.out({"node_id": node_id, "memories": _memories(r, node_id)})


def get_playbook(scam_type: str, tenant: str = "") -> dict:
    """Procedural memory: the latest investigation playbook (ordered tool steps) for a scam type."""
    r = Run("get_playbook", tenant)
    rows = r.q(Q.GET_PLAYBOOK, {"scam_type": scam_type})
    if not rows:
        rows = r.q(Q.GET_PLAYBOOK, {"scam_type": "job_scam"})
    p = rows[0]
    p["content"] = json.loads(p["content"])
    r.node(p["id"], "Memory", f"playbook {scam_type} v{p['version']}")
    return r.out(p)


def update_playbook(scam_type: str, steps: list[str], note: str, case_id: str = "", confidence: float = 0.7,
                    agent_id: str = "AGT-investigator", tenant: str = "") -> dict:
    """Save a new version of a playbook after an investigation (procedural memory that improves over time)."""
    r = Run("update_playbook", tenant)
    cur = r.q(Q.GET_PLAYBOOK, {"scam_type": scam_type})
    if not cur:
        return r.out({"error": f"no playbook for {scam_type}"})
    mid = f"MEM-PLAY-{scam_type}-{uuid.uuid4().hex[:4].upper()}"
    row = r.q(Q.UPDATE_PLAYBOOK, {"old_id": cur[0]["id"], "agent": agent_id, "scam_type": scam_type, "mid": mid,
                                  "content": json.dumps({"steps": steps, "note": note}),
                                  "confidence": min(0.99, confidence), "ts": now(), "case_id": case_id})
    r.node(mid, "Memory", f"playbook {scam_type}")
    r.edge(mid, cur[0]["id"], "UPDATES")
    return r.out({"memory_id": mid, "version": row[0]["m.version"], "previous": cur[0]["id"]})


def handoff(case_id: str, from_agent: str, to_agent: str, note: str, tenant: str = "") -> dict:
    """Hand a case from one agent to another. The handoff is stored in the graph, so the next agent reads
    the case state from the graph, not from the previous agent's memory."""
    r = Run("handoff", tenant)
    if from_agent not in AGENTS or to_agent not in AGENTS:
        return r.out({"error": f"agents must be one of {sorted(AGENTS)}"})
    hid = new_id("HO")
    r.q(Q.HANDOFF, {"case_id": case_id, "from_agent": from_agent, "to_agent": to_agent, "note": note,
                    "hid": hid, "ts": now()})
    r.edge(from_agent, to_agent, "HANDED_OFF")
    return r.out({"handoff_id": hid, "case_id": case_id, "to": to_agent})


def update_case(case_id: str, status: str = "", summary: str = "", scam_type: str = "", tenant: str = "") -> dict:
    """Update a case's status (open | investigating | actioned | closed), summary or scam type."""
    r = Run("update_case", tenant)
    rows = r.q(Q.UPDATE_CASE, {"case_id": case_id, "status": status or None, "summary": summary or None,
                               "scam_type": scam_type or None, "ts": now()})
    return r.out({"case_id": case_id, "status": rows[0]["c.status"] if rows else None})


def get_case_state(case_id: str, tenant: str = "") -> dict:
    """The shared state of a case: report, reporter, what it mentions, linked ring, actions taken, and every
    handoff between agents."""
    r = Run("get_case_state", tenant)
    st = r.q(Q.CASE_STATE, {"case_id": case_id})
    if not st:
        return r.out({"error": f"case {case_id} not found"})
    s = st[0]
    case = {k: v for k, v in (s["case"] or {}).items() if k != "_label"}
    actions = r.q(Q.CASE_ACTIONS, {"case_id": case_id})
    for a in actions:
        a["evidence_path"] = json.loads(a["evidence_path"]) if a.get("evidence_path") else {}
    handoffs = r.q(Q.CASE_HANDOFFS, {"case_id": case_id})
    r.node(case_id, "Case")
    for ring in s["rings"]:
        r.node(ring, "Ring")
        r.edge(case_id, ring, "PART_OF_RING")
    return r.out({"case": case, "report": s["report"], "description": s["description"],
                  "reported_value": s["reported_value"], "user_id": s["user_id"], "user_name": s["user_name"],
                  "mentions": [m for m in s["mentions"] if m.get("id")], "rings": s["rings"], "owner": s["owner"],
                  "actions": actions, "handoffs": handoffs})


# =========================================================================== Track 03: company brain

def who_owns(case_id: str, tenant: str = "") -> dict:
    """Who owns a case: the analyst (OWNS) and the agent it is currently assigned to."""
    r = Run("who_owns", tenant)
    analysts = r.q(Q.WHO_OWNS_CASE, {"q": case_id})
    agent = r.q(Q.WHO_OWNS_CASE_AGENT, {"q": case_id})
    for a in analysts:
        r.node(a["id"], "Analyst", a["name"])
        r.edge(a["id"], case_id, "OWNS")
    r.node(case_id, "Case")
    return r.out({"case_id": case_id, "analysts": analysts, "assigned_agent": agent[0]["id"] if agent else None})


def who_knows(scam_type: str, tenant: str = "") -> dict:
    """Which analysts know a scam type (EXPERT_IN), ranked by cases owned and decisions made."""
    r = Run("who_knows", tenant)
    rows = r.q(Q.WHO_KNOWS, {"scam_type": scam_type})
    for a in rows:
        r.node(a["id"], "Analyst", a["name"])
        r.node(f"ST-{scam_type}", "ScamType", scam_type)
        r.edge(a["id"], f"ST-{scam_type}", "EXPERT_IN")
    return r.out({"scam_type": scam_type, "analysts": rows})


def trace_decision(decision_or_account_or_case_id: str, tenant: str = "") -> dict:
    """Trace a decision back to its source: who made it, who approved it, under which policy (and whether
    that policy has since been superseded), and the tickets/chats/actions it came from.
    Accepts a decision id, an account id or a case id."""
    r = Run("trace_decision", tenant)
    q = decision_or_account_or_case_id.strip().upper()
    rows = r.q(Q.TRACE_DECISION, {"q": q})
    for d in rows:
        r.node(d["decision"], "Decision", f"{d['outcome']} {d['account'] or ''}".strip())
        if d["account"]:
            r.node(d["account"], "Account")
            r.edge(d["decision"], d["account"], "TARGETS")
        if d["case_id"]:
            r.node(d["case_id"], "Case")
            r.edge(d["decision"], d["case_id"], "ABOUT")
        if d["maker_id"]:
            r.node(d["maker_id"], d["maker_type"], d["made_by"])
            r.edge(d["decision"], d["maker_id"], "MADE_BY")
        if d["policy"]:
            r.node(d["policy"], "Policy", f"{d['policy']} {d['policy_title']}")
            r.edge(d["decision"], d["policy"], "UNDER_POLICY")
            for n in d["superseded_by"]:
                r.node(n, "Policy")
                r.edge(n, d["policy"], "SUPERSEDES")
        d["sources"] = [s for s in d["sources"] if s.get("id")]
        for s in d["sources"]:
            r.node(s["id"], s["type"], s.get("title"))
            r.edge(d["decision"], s["id"], "SOURCED_FROM")
            if s.get("text") and len(s["text"]) > 400:
                s["text"] = s["text"][:400] + "..."
    for d in rows:
        if d["approver_id"]:
            r.node(d["approver_id"], "Analyst", d["approved_by"])
            r.edge(d["decision"], d["approver_id"], "APPROVED_BY")
    return r.out({"query": q, "decisions": rows, "found": bool(rows)})


def current_policy(topic: str, tenant: str = "") -> dict:
    """The policy in force for a topic (freeze | alert | complaint | kyc | retention), following SUPERSEDES
    so answers stay current, plus the full version history."""
    r = Run("current_policy", tenant)
    pol = _policy(r, topic)
    history = r.q(Q.POLICY_HISTORY, {"topic": topic})
    for h in history:
        r.node(h["code"], "Policy", f"{h['code']} {h['title']}")
        if h["superseded_by"]:
            r.edge(h["superseded_by"], h["code"], "SUPERSEDES")
    return r.out({"topic": topic, "current": {k: v for k, v in pol.items() if k != "_label"}, "history": history})


def search_documents(query: str, limit: int = 5, tenant: str = "") -> dict:
    """Full-text search over the bank's documents, tickets and chat logs. Returns sources with links."""
    r = Run("search_documents", tenant)
    words = [w for w in re.findall(r"[A-Za-z0-9-]+", query) if len(w) > 2]
    rows = r.q(Q.SEARCH_DOCS, {"q": " ".join(words) or query, "limit": int(limit)}) if words else []
    for d in rows:
        r.node(d["id"], "Document", d["title"])
        d["text"] = (d["text"] or "")[:400]
    return r.out({"query": query, "documents": rows})


def recent_activity(limit: int = 15, tenant: str = "") -> dict:
    """The latest actions taken by agents (freezes, reviews, alerts, complaints)."""
    r = Run("recent_activity", tenant)
    return r.out({"actions": r.q(Q.RECENT_ACTIVITY, {"limit": int(limit)})})


# =========================================================================== registry

TOOLS = {f.__name__: f for f in [
    # Track 01
    lookup_entity, expand_neighbors, find_shared_infrastructure, detect_ring, rank_ring_members, money_trail,
    risk_score, freeze_accounts, flag_for_review, send_alert, draft_complaint,
    # Track 02
    find_user, register_user, recall_user, open_session, log_message, file_report, write_memory, get_memories,
    get_playbook, update_playbook, handoff, update_case, get_case_state,
    # Track 03
    who_owns, who_knows, trace_decision, current_policy, search_documents, recent_activity,
]}

TRACK = {
    **{n: "01" for n in ["lookup_entity", "expand_neighbors", "find_shared_infrastructure", "detect_ring",
                         "rank_ring_members", "money_trail", "risk_score", "freeze_accounts", "flag_for_review",
                         "send_alert", "draft_complaint"]},
    **{n: "02" for n in ["find_user", "register_user", "recall_user", "open_session", "log_message", "file_report",
                         "write_memory", "get_memories", "get_playbook", "update_playbook", "handoff",
                         "update_case", "get_case_state"]},
    **{n: "03" for n in ["who_owns", "who_knows", "trace_decision", "current_policy", "search_documents",
                         "recent_activity"]},
}
