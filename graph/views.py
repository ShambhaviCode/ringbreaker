"""Read-only views for the UI: assemble everything needed to draw a case (ring, infrastructure, money, victims,
and the stored evidence paths of each action) straight from the graph."""
from __future__ import annotations

import json

import config
from graph import queries as Q

CASES_WITH_RINGS = """
MATCH (r:ScamReport)-[:OPENED]->(c:Case)-[:PART_OF_RING]->(ring:Ring)
OPTIONAL MATCH (u:User)-[:FILED]->(r)
RETURN c.id AS case_id, c.opened_at AS opened_at, c.status AS status, c.scam_type AS scam_type,
       ring.id AS ring_id, u.name AS reporter, r.reported_value AS reported
ORDER BY opened_at DESC LIMIT 50
"""
USERS_WITH_HISTORY = """
MATCH (u:User)-[:HAD_SESSION]->(:Session)
RETURN DISTINCT u.id AS id, u.name AS name ORDER BY name
"""
USER_MEMORIES = """
MATCH (ag:Agent)-[:LEARNED]->(m:Memory)-[:ABOUT]->(x {id: $id})
RETURN m.id AS id, m.kind AS kind, m.content AS content, m.ts AS ts, ag.id AS agent ORDER BY m.ts
"""
PLAYBOOK_VERSIONS = """
MATCH (m:Memory {kind: 'procedural', scam_type: $scam_type})
RETURN m.id AS id, m.version AS version, m.content AS content, m.confidence AS confidence, m.ts AS ts,
       m.learned_from AS learned_from
ORDER BY m.version
"""
RING_SEEN_BEFORE = """
MATCH (m:Account)-[:MEMBER_OF]->(:Ring {id: $ring_id})
WITH collect(m) AS members
UNWIND members AS m
OPTIONAL MATCH (rep:ScamReport)-[:MENTIONS]->(x)
WHERE NOT (rep)-[:OPENED]->(:Case)
  AND (x = m OR (x)-[:LINKED_TO]->(m) OR (m)-[:REGISTERED_PHONE]->(x)
   OR (x)-[:PROMOTED_BY]->(:Phone)<-[:REGISTERED_PHONE]-(m))
RETURN count(DISTINCT rep) AS reports, min(rep.ts) AS first_report
"""


def _rows(g, q, params=None):
    res = g.query(q, params or {})
    cols = [h[1] for h in res.header] if res.header else []
    return [dict(zip(cols, r)) for r in res.result_set]


def list_cases(tenant):
    return _rows(config.get_graph(tenant), CASES_WITH_RINGS)


def users_with_history(tenant):
    return _rows(config.get_graph(tenant), USERS_WITH_HISTORY)


def memories_about(tenant, node_id):
    return _rows(config.get_graph(tenant), Q.anchor(USER_MEMORIES, "x", node_id), {"id": node_id})


def playbook_versions(tenant, scam_type):
    rows = _rows(config.get_graph(tenant), PLAYBOOK_VERSIONS, {"scam_type": scam_type})
    for r in rows:
        r["content"] = json.loads(r["content"])
    return rows


def ring_history(tenant, ring_id):
    rows = _rows(config.get_graph(tenant), RING_SEEN_BEFORE, {"ring_id": ring_id})
    return rows[0] if rows else {"reports": 0, "first_report": None}


def case_view(tenant: str, case_id: str, max_victims: int = 25) -> dict:
    """Nodes and edges for drawing one case, with roles, statuses and stored evidence paths."""
    g = config.get_graph(tenant)
    st = _rows(g, Q.CASE_STATE, {"case_id": case_id})
    if not st:
        return {}
    s = st[0]
    rings = s["rings"]
    nodes, edges = {}, {}

    def node(nid, label, name=None, **kw):
        cur = nodes.setdefault(nid, {"id": nid, "label": label, "name": name or nid})
        cur.update({k: v for k, v in kw.items() if v is not None})

    def edge(a, b, t, **kw):
        if (b, a, t) in edges:  # evidence paths walk edges in either direction; draw each edge once
            return
        edges.setdefault((a, b, t), {"src": a, "dst": b, "type": t, **kw})

    anchor = next((m["id"] for m in s["mentions"] if m.get("id")), None)
    if anchor:
        node(anchor, Q.label_for(anchor) or "Node", s["reported_value"], role="reported")
    members, ring_id = [], rings[0] if rings else None
    if ring_id:
        members = _rows(g, Q.RING_MEMBERS, {"ring_id": ring_id})
        ids = [m["id"] for m in members]
        pr = {x["id"]: x["score"] for x in _rows(g, Q.PAGERANK_PAID, {"ids": ids})}
        collector = max(pr, key=pr.get) if pr else None
        for m in members:
            node(m["id"], "Account", f"{m['id']}", holder=m["holder"], status=m["status"],
                 risk=m.get("risk_score"), role="collector" if m["id"] == collector else "member")
        infra_rows = _rows(g, """
            MATCH (a:Account)-[r:USED_DEVICE|LOGGED_IN_FROM|REGISTERED_PHONE]->(x)
            WHERE a.id IN $ids
            OPTIONAL MATCH (x)<-[:USED_DEVICE|LOGGED_IN_FROM|REGISTERED_PHONE]-(o:Account)
            WITH a, r, x, count(DISTINCT o) AS users
            WHERE users >= 2
            RETURN a.id AS a, x.id AS x, labels(x)[0] AS label, type(r) AS rel, users""", {"ids": ids})
        for r in infra_rows:
            node(r["x"], r["label"], r["x"], users=r["users"])
            edge(r["a"], r["x"], r["rel"])
        for f in _rows(g, Q.RING_FLOWS, {"ids": ids}):
            edge(f["src"], f["dst"], "PAID", amount=f["total"])
        inflow = _rows(g, Q.RING_INFLOW, {"ids": ids})
        for v in inflow[:max_victims]:
            node(v["victim_account"], "Account", v["victim_account"], holder=v["holder"], role="victim")
            edge(v["victim_account"], v["paid_to"], "PAID", amount=v["total"])
        upi_rows = _rows(g, "MATCH (u:UPI)-[:LINKED_TO]->(a:Account) WHERE a.id IN $ids AND u.handle CONTAINS '@fakepay' "
                            "RETURN u.id AS u, u.handle AS h, a.id AS a", {"ids": ids})
        for u in upi_rows:
            node(u["u"], "UPI", u["h"])
            edge(u["u"], u["a"], "LINKED_TO")
    else:
        inflow = []

    actions = _rows(g, Q.CASE_ACTIONS, {"case_id": case_id})
    paths = {}
    for a in actions:
        p = json.loads(a["evidence_path"]) if a.get("evidence_path") else {}
        for t in a["targets"]:
            if a["type"] in ("freeze", "review"):
                if t in nodes:
                    nodes[t]["action"] = a["type"]
                    if a["type"] == "review":
                        nodes[t]["reviewer"] = a["assigned_to"]
                if p.get("ids"):
                    paths[t] = p
                    for i, nid in enumerate(p["ids"]):
                        node(nid, p["labels"][i], p["names"][i])
                    for i, rel in enumerate(p["rels"]):
                        edge(p["ids"][i], p["ids"][i + 1], rel)
    # actions from other cases may have frozen members earlier: keep status from the graph
    return {
        "case": s["case"].properties if hasattr(s["case"], "properties") else s["case"],
        "user": {"id": s["user_id"], "name": s["user_name"]}, "reported": s["reported_value"], "anchor": anchor,
        "ring_id": ring_id, "nodes": list(nodes.values()), "edges": list(edges.values()), "paths": paths,
        "actions": actions, "stolen": sum(v["total"] for v in inflow),
        "victims": len({v["victim_account"] for v in inflow}),
    }


def risk_factors(tenant: str, account_id: str) -> dict:
    rows = _rows(config.get_graph(tenant), "MATCH (a:Account {id: $id}) RETURN a.risk_score AS score, "
                                           "a.risk_factors AS f, a.status AS status, a.holder_name AS holder",
                 {"id": account_id})
    if not rows:
        return {}
    r = rows[0]
    r["factors"] = json.loads(r["f"]) if r.get("f") else []
    return r
