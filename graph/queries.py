"""All Cypher used by RingBreaker lives here, as named constants and small helpers.

FalkorDB notes (verified against FalkorDB 4.18 with CALL dbms.procedures()):
- Algorithms available: algo.pageRank, algo.WCC, algo.labelPropagation, algo.betweenness,
  algo.SPpaths, algo.SSpaths, algo.BFS, algo.MSF.
- Undirected shortestPath() is NOT supported, so evidence paths use
  algo.SPpaths({..., relDirection: 'both'}) instead.
- Variable-length bounds can't be parameters, so hop counts are formatted in as validated ints.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- helpers

DISPLAY = "coalesce(n.handle, n.number, n.holder_name, n.url, n.title, n.name, n.code, n.id)"

PREFIX_LABELS = [
    ("ACC-", "Account"), ("UPI-", "UPI"), ("PH-", "Phone"), ("IP-", "IP"), ("LNK-", "Link"),
    ("TXN-", "Transaction"), ("RPT-", "ScamReport"), ("USR-", "User"), ("SES-", "Session"),
    ("MSG-", "Message"), ("C-", "Case"), ("AGT-", "Agent"), ("MEM-", "Memory"), ("ACT-", "ActionTaken"),
    ("RING-", "Ring"), ("ANL-", "Analyst"), ("TEAM-", "Team"), ("P-", "Policy"), ("DEC-", "Decision"),
    ("TKT-", "Document"), ("CHAT-", "Document"), ("DOC-", "Document"), ("ST-", "ScamType"), ("HO-", "Handoff"),
    ("D", "Device"),
]


def label_for(node_id: str) -> str | None:
    for prefix, label in PREFIX_LABELS:
        if node_id.startswith(prefix):
            return label
    return None


def hops(n, lo=1, hi=6) -> int:
    n = int(n)
    if not lo <= n <= hi:
        raise ValueError(f"hops must be between {lo} and {hi}")
    return n


def anchor(query: str, var: str, node_id: str | None) -> str:
    """Turn `(var {id: $...})` into `(var:Label {id: $...})` so FalkorDB uses the id index."""
    label = label_for(node_id or "")
    if not label:
        return query
    return query.replace(f"({var} {{id: $", f"({var}:{label} {{id: $")


def rel_union(rel_types: list[str]) -> str:
    for r in rel_types:
        if not r.replace("_", "").isalnum():
            raise ValueError(f"bad relationship type {r!r}")
    return "|".join(rel_types)


# --------------------------------------------------------------------------- loading / projection

REFRESH_PAID = """
MATCH (a:Account)-[:SENT]->(t:Transaction)-[:TO]->(b:Account)
WITH a, b, sum(t.amount) AS total, count(t) AS n, max(t.ts) AS last_ts
MERGE (a)-[p:PAID]->(b)
SET p.total = total, p.count = n, p.last_ts = last_ts
"""


def refresh_paid_projection(g):
    """PAID is a projection of Account-SENT->Transaction-TO->Account, so graph algorithms
    (PageRank, shortest paths) can run on direct account-to-account money edges."""
    g.query(REFRESH_PAID)


def graph_stats(g) -> dict:
    rows = g.query("MATCH (n) RETURN labels(n)[0] AS l, count(n) AS c").result_set
    rels = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    return {"nodes": sum(c for _, c in rows), "relationships": rels}


# --------------------------------------------------------------------------- Track 01: lookup

LOOKUP_UPI = """
MATCH (u:UPI {handle: $v})-[r:LINKED_TO]->(a:Account)
RETURN u.id AS entity_id, 'UPI' AS entity_label, u.handle AS entity_name,
       a.id AS account_id, a.holder_name AS holder, a.status AS status, a.opened_at AS opened_at, 'LINKED_TO' AS rel
"""
LOOKUP_PHONE = """
MATCH (p:Phone {number: $v})
OPTIONAL MATCH (a:Account)-[:REGISTERED_PHONE]->(p)
RETURN p.id AS entity_id, 'Phone' AS entity_label, p.number AS entity_name,
       a.id AS account_id, a.holder_name AS holder, a.status AS status, a.opened_at AS opened_at, 'REGISTERED_PHONE' AS rel
"""
LOOKUP_LINK = """
MATCH (l:Link) WHERE l.url = $v OR l.domain = $v OR l.url CONTAINS $v
OPTIONAL MATCH (l)-[:PROMOTED_BY]->(p:Phone)<-[:REGISTERED_PHONE]-(a:Account)
RETURN l.id AS entity_id, 'Link' AS entity_label, l.url AS entity_name,
       a.id AS account_id, a.holder_name AS holder, a.status AS status, a.opened_at AS opened_at, 'PROMOTED_BY' AS rel,
       p.id AS via_phone
"""
LOOKUP_ACCOUNT = """
MATCH (a:Account {id: $v})
RETURN a.id AS entity_id, 'Account' AS entity_label, a.holder_name AS entity_name,
       a.id AS account_id, a.holder_name AS holder, a.status AS status, a.opened_at AS opened_at, 'SELF' AS rel
"""
REPORTS_MENTIONING = """
MATCH (r:ScamReport)-[:MENTIONS]->(x {id: $id})
RETURN r.id AS report_id, r.ts AS ts, r.scam_type AS scam_type, r.status AS status
ORDER BY r.ts
"""
MEMORIES_ABOUT = """
MATCH (m:Memory)-[:ABOUT]->(x {id: $id})
OPTIONAL MATCH (ag:Agent)-[:LEARNED]->(m)
RETURN m.id AS id, m.kind AS kind, m.content AS content, m.ts AS ts, ag.id AS agent
ORDER BY m.ts DESC LIMIT 10
"""

# --------------------------------------------------------------------------- Track 01: expansion


def expand_query(label: str, n_hops: int, rels: str) -> str:
    return f"""
MATCH p = (s:{label} {{id: $id}})-[:{rels}*1..{n_hops}]-(x)
WITH p, x LIMIT 400
RETURN [n IN nodes(p) | n.id] AS ids,
       [n IN nodes(p) | labels(n)[0]] AS labels,
       [n IN nodes(p) | {DISPLAY}] AS names,
       [r IN relationships(p) | type(r)] AS rels,
       x.id AS end_id, labels(x)[0] AS end_label
"""


SHARED_INFRA = """
UNWIND $ids AS aid
MATCH (a:Account {id: aid})-[:USED_DEVICE|LOGGED_IN_FROM|REGISTERED_PHONE]->(x)
      <-[r:USED_DEVICE|LOGGED_IN_FROM|REGISTERED_PHONE]-(o:Account)
WHERE o.id <> aid
RETURN x.id AS infra_id, labels(x)[0] AS infra_label, type(r) AS rel,
       collect(DISTINCT aid) AS from_accounts, collect(DISTINCT o.id) AS other_accounts
ORDER BY size(other_accounts) DESC
"""

# --------------------------------------------------------------------------- Track 01: ring detection

WCC_INFRA = """
CALL algo.WCC({nodeLabels: ['Account', 'Device', 'IP', 'Phone'],
               relationshipTypes: ['USED_DEVICE', 'LOGGED_IN_FROM', 'REGISTERED_PHONE']})
YIELD node, componentId
WITH node, componentId
WHERE componentId IN $components OR node.id IN $seeds
RETURN node.id AS id, labels(node)[0] AS label, componentId
"""
WCC_SEED_COMPONENTS = """
CALL algo.WCC({nodeLabels: ['Account', 'Device', 'IP', 'Phone'],
               relationshipTypes: ['USED_DEVICE', 'LOGGED_IN_FROM', 'REGISTERED_PHONE']})
YIELD node, componentId
WITH node, componentId WHERE node.id IN $seeds
RETURN DISTINCT componentId
"""
LABEL_PROPAGATION_MONEY = """
CALL algo.labelPropagation({nodeLabels: ['Account'], relationshipTypes: ['PAID'], maxIterations: 20})
YIELD node, communityId
WITH node, communityId WHERE node.id IN $ids
RETURN node.id AS id, communityId
"""
COMPONENT_EDGES = """
MATCH (a:Account)-[r:USED_DEVICE|LOGGED_IN_FROM|REGISTERED_PHONE]->(x)
WHERE a.id IN $ids AND x.id IN $infra
RETURN a.id AS src, x.id AS dst, type(r) AS rel
"""
EXISTING_RINGS = """
MATCH (a:Account)-[:MEMBER_OF]->(r:Ring)
WHERE a.id IN $ids
RETURN r.id AS ring_id, r.detected_at AS detected_at, count(a) AS overlap
ORDER BY detected_at
"""
UPSERT_RING = """
MERGE (r:Ring {id: $ring_id})
ON CREATE SET r.detected_at = $ts, r.created_at = $ts, r.scam_type = $scam_type, r.detected_by = $agent
SET r.updated_at = $ts
WITH r
UNWIND $ids AS aid
MATCH (a:Account {id: aid})
MERGE (a)-[m:MEMBER_OF]->(r)
ON CREATE SET m.since = $ts
WITH DISTINCT r
MATCH (x:Account)-[:MEMBER_OF]->(r)
WITH r, count(x) AS size
SET r.size = size
RETURN r.id, r.size
"""
LINK_CASE_RING = """
MATCH (c:Case {id: $case_id}), (r:Ring {id: $ring_id})
MERGE (c)-[:PART_OF_RING]->(r)
"""
RING_MEMBERS = """
MATCH (a:Account)-[:MEMBER_OF]->(r:Ring {id: $ring_id})
RETURN a.id AS id, a.holder_name AS holder, a.status AS status, a.opened_at AS opened_at, a.risk_score AS risk_score
"""
RING_INFO = "MATCH (r:Ring {id: $ring_id}) RETURN r"

# --------------------------------------------------------------------------- Track 01: ranking + money

PAGERANK_PAID = """
CALL algo.pageRank('Account', 'PAID')
YIELD node, score
WITH node, score WHERE node.id IN $ids
RETURN node.id AS id, score
ORDER BY score DESC
"""
RING_FLOWS = """
MATCH (a:Account)-[p:PAID]->(b:Account)
WHERE a.id IN $ids AND b.id IN $ids
RETURN a.id AS src, b.id AS dst, p.total AS total, p.count AS n
"""
RING_INFLOW = """
MATCH (v:Account)-[p:PAID]->(m:Account)
WHERE m.id IN $ids AND NOT v.id IN $ids
OPTIONAL MATCH (u:User)-[:HOLDS]->(v)
RETURN v.id AS victim_account, v.holder_name AS holder, u.id AS user_id, m.id AS paid_to,
       p.total AS total, p.count AS n
ORDER BY total DESC
"""
MONEY_TRAIL = """
MATCH (s:Account {id: $src}), (t:Account {id: $dst})
CALL algo.SPpaths({sourceNode: s, targetNode: t, relTypes: ['PAID'], maxLen: 6, pathCount: 1})
YIELD path
RETURN [n IN nodes(path) | n.id] AS ids, [r IN relationships(path) | r.total] AS amounts
"""
EVIDENCE_PATH = """
MATCH (s {id: $src}), (t:Account {id: $dst})
CALL algo.SPpaths({sourceNode: s, targetNode: t,
                   relTypes: ['LINKED_TO', 'USED_DEVICE', 'LOGGED_IN_FROM', 'REGISTERED_PHONE', 'PROMOTED_BY', 'PAID'],
                   relDirection: 'both', maxLen: 8, pathCount: 1})
YIELD path
RETURN [n IN nodes(path) | n.id] AS ids, [n IN nodes(path) | labels(n)[0]] AS labels,
       [n IN nodes(path) | coalesce(n.handle, n.number, n.holder_name, n.url, n.id)] AS names,
       [r IN relationships(path) | type(r)] AS rels
"""


def evidence_path_query(src_label: str | None) -> str:
    """Anchor the source node on its label so FalkorDB uses the id index."""
    if src_label:
        return EVIDENCE_PATH.replace("(s {id: $src})", f"(s:{src_label} {{id: $src}})")
    return EVIDENCE_PATH


# --------------------------------------------------------------------------- Track 01: risk

RISK_FEATURES = """
UNWIND $ids AS aid
MATCH (a:Account {id: aid})
OPTIONAL MATCH (a)-[:MEMBER_OF]->(ring:Ring)
WITH a, count(ring) > 0 AS in_ring, collect(ring.id)[0] AS ring_id
OPTIONAL MATCH (a)-[:USED_DEVICE|LOGGED_IN_FROM]->(x)<-[:USED_DEVICE|LOGGED_IN_FROM]-(o:Account)
WHERE o <> a
WITH a, in_ring, ring_id, count(DISTINCT o) AS sharing_accounts,
     count(DISTINCT CASE WHEN o.status = 'frozen' THEN o END) AS frozen_neighbours
OPTIONAL MATCH (a)<-[:LINKED_TO]-(u:UPI)<-[:MENTIONS]-(r1:ScamReport)
WITH a, in_ring, ring_id, sharing_accounts, frozen_neighbours, count(DISTINCT r1) AS rep_upi
OPTIONAL MATCH (a)-[:REGISTERED_PHONE]->(:Phone)<-[:MENTIONS]-(r2:ScamReport)
WITH a, in_ring, ring_id, sharing_accounts, frozen_neighbours, rep_upi, count(DISTINCT r2) AS rep_phone
OPTIONAL MATCH (a)-[:REGISTERED_PHONE]->(:Phone)<-[:PROMOTED_BY]-(:Link)<-[:MENTIONS]-(r3:ScamReport)
WITH a, in_ring, ring_id, sharing_accounts, frozen_neighbours, rep_upi + rep_phone + count(DISTINCT r3) AS reports
OPTIONAL MATCH (s:Account)-[pin:PAID]->(a)
WITH a, in_ring, ring_id, sharing_accounts, frozen_neighbours, reports,
     count(DISTINCT s) AS distinct_senders, sum(pin.total) AS inflow,
     count(DISTINCT CASE WHEN (s)-[:MEMBER_OF]->(:Ring {id: ring_id}) THEN s END) AS ring_senders
OPTIONAL MATCH (a)-[pout:PAID]->(:Account)
RETURN a.id AS id, a.opened_at AS opened_at, a.status AS status, in_ring, ring_id, sharing_accounts,
       frozen_neighbours, reports, distinct_senders, ring_senders, inflow, sum(pout.total) AS outflow
"""
SET_RISK = """
UNWIND $rows AS r
MATCH (a:Account {id: r.id})
SET a.risk_score = r.score, a.risk_factors = r.factors, a.risk_scored_at = r.ts
"""

# --------------------------------------------------------------------------- Track 01: actions

CURRENT_POLICY = """
MATCH (p:Policy {topic: $topic})
WHERE p.superseded_by = '' OR p.superseded_by IS NULL
OPTIONAL MATCH (p)-[:SUPERSEDES*1..5]->(old:Policy)
RETURN p, collect(old.code) AS replaces
ORDER BY p.effective_from DESC LIMIT 1
"""
FREEZE_ONE = """
MATCH (a:Account {id: $acc}), (c:Case {id: $case_id}), (pol:Policy {code: $policy}), (ag:Agent {id: $agent})
SET a.status = 'frozen', a.frozen_at = $ts
CREATE (act:ActionTaken {id: $act_id, type: 'freeze', ts: $ts, created_at: $ts, reason: $reason,
                         risk_score: $score, evidence_path: $path_json, cypher: $cypher, policy: $policy})
CREATE (c)-[:RESULTED_IN]->(act)-[:TARGETS]->(a)
CREATE (act)-[:UNDER_POLICY]->(pol)
CREATE (act)-[:TAKEN_BY]->(ag)
CREATE (d:Decision {id: $dec_id, ts: $ts, created_at: $ts, outcome: 'freeze', reason: $reason,
                    approval: 'automatic under ' + $policy})
CREATE (d)-[:ABOUT]->(c), (d)-[:TARGETS]->(a), (d)-[:MADE_BY]->(ag), (d)-[:UNDER_POLICY]->(pol),
       (d)-[:SOURCED_FROM]->(act)
RETURN act.id
"""
REVIEW_ONE = """
MATCH (a:Account {id: $acc}), (c:Case {id: $case_id}), (an:Analyst {id: $analyst}), (ag:Agent {id: $agent}),
      (pol:Policy {code: $policy})
CREATE (act:ActionTaken {id: $act_id, type: 'review', ts: $ts, created_at: $ts, reason: $reason,
                         risk_score: $score, evidence_path: $path_json, cypher: $cypher, policy: $policy})
CREATE (c)-[:RESULTED_IN]->(act)-[:TARGETS]->(a)
CREATE (act)-[:ASSIGNED_TO]->(an), (act)-[:TAKEN_BY]->(ag), (act)-[:UNDER_POLICY]->(pol)
RETURN act.id
"""
PICK_REVIEWER = """
MATCH (an:Analyst)-[:EXPERT_IN]->(:ScamType {name: $scam_type})
OPTIONAL MATCH (an)<-[:ASSIGNED_TO]-(open:ActionTaken {type: 'review'})
RETURN an.id AS id, an.name AS name, an.role AS role, count(open) AS open_reviews
ORDER BY open_reviews ASC, an.id ASC LIMIT 1
"""
CREATE_ALERT = """
MATCH (c:Case {id: $case_id}), (ag:Agent {id: $agent}), (pol:Policy {code: $policy})
CREATE (act:ActionTaken {id: $act_id, type: 'alert', ts: $ts, created_at: $ts, message: $message,
                         recipients: size($user_ids), cypher: $cypher, policy: $policy})
CREATE (c)-[:RESULTED_IN]->(act), (act)-[:TAKEN_BY]->(ag), (act)-[:UNDER_POLICY]->(pol)
WITH act
UNWIND $user_ids AS uid
MATCH (u:User {id: uid})
CREATE (act)-[:TARGETS]->(u)
RETURN count(u)
"""
CREATE_COMPLAINT = """
MATCH (c:Case {id: $case_id}), (ag:Agent {id: $agent}), (pol:Policy {code: $policy})
CREATE (act:ActionTaken {id: $act_id, type: 'complaint', ts: $ts, created_at: $ts, text: $text, cypher: $cypher,
                         policy: $policy, status: 'draft - awaiting analyst submission'})
CREATE (c)-[:RESULTED_IN]->(act), (act)-[:TAKEN_BY]->(ag), (act)-[:UNDER_POLICY]->(pol)
RETURN act.id
"""
CASE_ACTIONS = """
MATCH (c:Case {id: $case_id})-[:RESULTED_IN]->(act:ActionTaken)
OPTIONAL MATCH (act)-[:TARGETS]->(t)
OPTIONAL MATCH (act)-[:ASSIGNED_TO]->(an:Analyst)
RETURN act.id AS id, act.type AS type, act.ts AS ts, act.reason AS reason, act.risk_score AS risk_score,
       act.evidence_path AS evidence_path, act.policy AS policy, collect(t.id) AS targets, an.name AS assigned_to,
       act.text AS text, act.message AS message
ORDER BY act.ts
"""

# --------------------------------------------------------------------------- Track 02: memory + coordination

RECALL_USER = """
MATCH (u:User {id: $uid})
OPTIONAL MATCH (u)-[:HOLDS]->(acc:Account)
OPTIONAL MATCH (u)-[:FILED]->(r:ScamReport)
OPTIONAL MATCH (r)-[:OPENED]->(c:Case)
OPTIONAL MATCH (c)-[:PART_OF_RING]->(ring:Ring)
OPTIONAL MATCH (r)-[:MENTIONS]->(m)
RETURN u.id AS id, u.name AS name, u.language AS language, collect(DISTINCT acc.id) AS accounts,
       collect(DISTINCT {report: r.id, ts: r.ts, scam_type: r.scam_type, mentions: m.id, case: c.id,
                         case_status: c.status, ring: ring.id}) AS reports
"""
FIND_USER = """
MATCH (u:User) WHERE u.id = $q OR toLower(u.name) = toLower($q) OR toLower(u.name) STARTS WITH toLower($q)
RETURN u.id AS id, u.name AS name LIMIT 5
"""
USER_SESSIONS = """
MATCH (u:User {id: $uid})-[:HAD_SESSION]->(s:Session)
OPTIONAL MATCH (s)-[:CONTAINS]->(m:Message)
WITH s, m ORDER BY m.ts
RETURN s.id AS session, s.started_at AS started_at, s.ended_at AS ended_at,
       collect({role: m.role, text: m.text, ts: m.ts}) AS messages
ORDER BY started_at DESC LIMIT $limit
"""
OPEN_SESSION = """
MATCH (u:User {id: $uid})
CREATE (s:Session {id: $sid, started_at: $ts, created_at: $ts, channel: $channel})
CREATE (u)-[:HAD_SESSION]->(s)
RETURN s.id
"""
CREATE_USER = """
MERGE (u:User {id: $uid})
ON CREATE SET u.name = $name, u.language = $language, u.created_at = $ts
RETURN u.id
"""
LOG_MESSAGE = """
MATCH (s:Session {id: $sid})
CREATE (m:Message {id: $mid, role: $role, text: $text, ts: $ts, created_at: $ts})
CREATE (s)-[:CONTAINS]->(m)
SET s.ended_at = $ts
RETURN m.id
"""
FILE_REPORT = """
MATCH (u:User {id: $uid}), (ag:Agent {id: 'AGT-intake'})
CREATE (r:ScamReport {id: $rid, ts: $ts, created_at: $ts, scam_type: $scam_type, description: $description,
                      reporter_id: $uid, status: 'case_opened', reported_value: $value})
CREATE (u)-[:FILED]->(r)
CREATE (c:Case {id: $cid, status: 'open', opened_at: $ts, created_at: $ts, scam_type: $scam_type,
                summary: 'Opened from report ' + $rid})
CREATE (r)-[:OPENED]->(c)
CREATE (c)-[:ASSIGNED_TO {ts: $ts}]->(ag)
SET c.assignee = 'AGT-intake'
RETURN r.id, c.id
"""
REPORT_MENTIONS = """
MATCH (r:ScamReport {id: $rid}), (x {id: $target})
MERGE (r)-[:MENTIONS]->(x)
"""
CREATE_PHONE_IF_MISSING = """
MERGE (p:Phone {number: $number})
ON CREATE SET p.id = $pid, p.created_at = $ts, p.first_seen_in_report = true
RETURN p.id
"""
LINK_SESSION_CASE = """
MATCH (s:Session {id: $sid}), (c:Case {id: $cid})
MERGE (s)-[:RAISED]->(c)
"""
WRITE_MEMORY = """
MATCH (ag:Agent {id: $agent})
CREATE (m:Memory {id: $mid, kind: $kind, content: $content, ts: $ts, created_at: $ts, confidence: $confidence})
CREATE (ag)-[:LEARNED]->(m)
WITH m
OPTIONAL MATCH (x {id: $about})
FOREACH (_ IN CASE WHEN x IS NULL THEN [] ELSE [1] END | CREATE (m)-[:ABOUT]->(x))
RETURN m.id
"""
GET_PLAYBOOK = """
MATCH (m:Memory {kind: 'procedural', scam_type: $scam_type})
WHERE NOT ()-[:UPDATES]->(m)
RETURN m.id AS id, m.version AS version, m.content AS content, m.confidence AS confidence, m.ts AS ts
ORDER BY m.version DESC LIMIT 1
"""
UPDATE_PLAYBOOK = """
MATCH (old:Memory {id: $old_id}), (ag:Agent {id: $agent}), (st:ScamType {name: $scam_type})
CREATE (m:Memory {id: $mid, kind: 'procedural', scam_type: $scam_type, version: old.version + 1,
                  content: $content, confidence: $confidence, ts: $ts, created_at: $ts, learned_from: $case_id})
CREATE (m)-[:UPDATES]->(old), (ag)-[:LEARNED]->(m), (m)-[:ABOUT]->(st)
RETURN m.id, m.version
"""
HANDOFF = """
MATCH (c:Case {id: $case_id}), (f:Agent {id: $from_agent}), (t:Agent {id: $to_agent})
CREATE (h:Handoff {id: $hid, ts: $ts, created_at: $ts, from_agent: $from_agent, to_agent: $to_agent, note: $note})
CREATE (c)-[:HAS_HANDOFF]->(h)
CREATE (f)-[:HANDED_OFF {ts: $ts, note: $note, case_id: $case_id}]->(t)
MERGE (c)-[a:ASSIGNED_TO]->(t)
SET a.ts = $ts, c.assignee = $to_agent
RETURN h.id
"""
UPDATE_CASE = """
MATCH (c:Case {id: $case_id})
SET c.status = coalesce($status, c.status), c.summary = coalesce($summary, c.summary), c.updated_at = $ts,
    c.scam_type = coalesce($scam_type, c.scam_type)
RETURN c.id, c.status
"""
CASE_STATE = """
MATCH (c:Case {id: $case_id})
OPTIONAL MATCH (r:ScamReport)-[:OPENED]->(c)
OPTIONAL MATCH (u:User)-[:FILED]->(r)
OPTIONAL MATCH (r)-[:MENTIONS]->(m)
OPTIONAL MATCH (c)-[:PART_OF_RING]->(ring:Ring)
OPTIONAL MATCH (an:Analyst)-[:OWNS]->(c)
RETURN c AS case, r.id AS report, r.description AS description, r.reported_value AS reported_value,
       u.id AS user_id, u.name AS user_name,
       collect(DISTINCT {id: m.id, label: labels(m)[0]}) AS mentions, collect(DISTINCT ring.id) AS rings,
       an.name AS owner
"""
CASE_HANDOFFS = """
MATCH (c:Case {id: $case_id})-[:HAS_HANDOFF]->(h:Handoff)
RETURN h.ts AS ts, h.from_agent AS from_agent, h.to_agent AS to_agent, h.note AS note
ORDER BY h.ts
"""
USER_CASES = """
MATCH (u:User {id: $uid})-[:FILED]->(:ScamReport)-[:OPENED]->(c:Case)
RETURN c.id AS id ORDER BY c.opened_at
"""

# --------------------------------------------------------------------------- Track 03: company brain

WHO_OWNS_CASE = """
MATCH (an:Analyst)-[:OWNS]->(c:Case {id: $q})
OPTIONAL MATCH (an)-[:MEMBER_OF]->(t:Team)
RETURN an.id AS id, an.name AS name, an.role AS role, t.name AS team, c.id AS case_id
"""
WHO_OWNS_CASE_AGENT = """
MATCH (c:Case {id: $q})-[a:ASSIGNED_TO]->(ag:Agent)
RETURN ag.id AS id, a.ts AS ts ORDER BY a.ts DESC LIMIT 1
"""
WHO_KNOWS = """
MATCH (an:Analyst)-[:EXPERT_IN]->(st:ScamType {name: $scam_type})
OPTIONAL MATCH (an)-[:MEMBER_OF]->(t:Team)
OPTIONAL MATCH (an)-[:OWNS]->(c:Case {scam_type: $scam_type})
OPTIONAL MATCH (d:Decision)-[:MADE_BY]->(an)
RETURN an.id AS id, an.name AS name, an.role AS role, t.name AS team, count(DISTINCT c) AS cases_owned,
       count(DISTINCT d) AS decisions_made
ORDER BY cases_owned DESC, decisions_made DESC
"""
TRACE_DECISION = """
MATCH (d:Decision)
WHERE d.id = $q OR (d)-[:TARGETS]->(:Account {id: $q}) OR (d)-[:ABOUT]->(:Case {id: $q})
OPTIONAL MATCH (d)-[:TARGETS]->(acc:Account)
OPTIONAL MATCH (d)-[:ABOUT]->(c:Case)
OPTIONAL MATCH (d)-[:MADE_BY]->(maker)
OPTIONAL MATCH (d)-[:APPROVED_BY]->(appr:Analyst)
OPTIONAL MATCH (d)-[:UNDER_POLICY]->(pol:Policy)
OPTIONAL MATCH (newer:Policy)-[:SUPERSEDES*1..5]->(pol)
OPTIONAL MATCH (d)-[:SOURCED_FROM]->(src)
RETURN d.id AS decision, d.ts AS ts, d.outcome AS outcome, d.reason AS reason, d.approval AS approval,
       acc.id AS account, c.id AS case_id, maker.id AS maker_id, coalesce(maker.name, maker.id) AS made_by,
       labels(maker)[0] AS maker_type, appr.name AS approved_by, pol.code AS policy, pol.title AS policy_title,
       collect(DISTINCT newer.code) AS superseded_by,
       collect(DISTINCT {id: src.id, type: labels(src)[0], title: coalesce(src.title, src.type),
                         url: src.url, text: coalesce(src.text, src.reason)}) AS sources
ORDER BY ts DESC LIMIT 10
"""
POLICY_HISTORY = """
MATCH (p:Policy {topic: $topic})
RETURN p.code AS code, p.title AS title, p.effective_from AS effective_from, p.superseded_by AS superseded_by,
       p.text AS text, p.freeze_threshold AS freeze_threshold, p.review_threshold AS review_threshold
ORDER BY effective_from
"""
SEARCH_DOCS = """
CALL db.idx.fulltext.queryNodes('Document', $q) YIELD node, score
RETURN node.id AS id, node.title AS title, node.source AS source, node.url AS url, node.text AS text,
       node.ts AS ts, score
ORDER BY score DESC LIMIT $limit
"""
RECENT_ACTIVITY = """
MATCH (act:ActionTaken)
OPTIONAL MATCH (act)-[:TAKEN_BY]->(ag:Agent)
OPTIONAL MATCH (c:Case)-[:RESULTED_IN]->(act)
RETURN act.id AS id, act.type AS type, act.ts AS ts, ag.id AS agent, c.id AS case_id
ORDER BY act.ts DESC LIMIT $limit
"""
