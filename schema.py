"""Indexes for the RingBreaker graph. Run via graph/load.py (it calls create_indexes)."""

ID_LABELS = [
    "Account", "UPI", "Phone", "Device", "IP", "Link", "Transaction", "ScamReport",
    "User", "Session", "Message", "Case", "Agent", "Memory", "ActionTaken", "Ring",
    "Analyst", "Team", "Policy", "Decision", "Document", "ScamType",
]

EXTRA_INDEXES = [
    ("UPI", "handle"),
    ("Phone", "number"),
    ("Link", "url"),
    ("Link", "domain"),
    ("Account", "status"),
    ("Memory", "kind"),
    ("Policy", "topic"),
]

# Relationship types that describe shared infrastructure (used by ring detection)
INFRA_RELS = ["USED_DEVICE", "LOGGED_IN_FROM", "REGISTERED_PHONE"]
# Every relationship an evidence path is allowed to use in the fraud layer
FRAUD_RELS = ["LINKED_TO", "USED_DEVICE", "LOGGED_IN_FROM", "REGISTERED_PHONE", "PAID", "PROMOTED_BY", "MENTIONS"]


def create_indexes(g) -> list[str]:
    done = []
    for label in ID_LABELS:
        _safe(g, f"CREATE INDEX FOR (n:{label}) ON (n.id)")
        done.append(f"{label}.id")
    for label, prop in EXTRA_INDEXES:
        _safe(g, f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
        done.append(f"{label}.{prop}")
    _safe(g, "CALL db.idx.fulltext.createNodeIndex('Document', 'title', 'text')")
    done.append("fulltext Document(title,text)")
    return done


def _safe(g, q):
    try:
        g.query(q)
    except Exception as ex:  # index already exists
        if "already" not in str(ex).lower():
            raise
