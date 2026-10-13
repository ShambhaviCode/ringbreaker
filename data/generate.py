"""Synthetic data generator for RingBreaker.

Everything here is fake: names, phone numbers (+91-90000-XXXXX), UPI handles
(@fakepay / @demobank), IPs (10.x private range) and domains (.example, a reserved TLD).

Usage:
    python -m data.generate                 # writes data/out/demobank.json and data/out/suraksha.json
    python -m data.generate --seed 42 --normal 2000

The output is a plain JSON file of nodes and edges, plus a ground_truth section
(which accounts really belong to which ring) that is used ONLY by tests,
never by the agents.
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

OUT_DIR = Path(__file__).parent / "out"

FIRST = [
    "Aarav", "Vihaan", "Aditya", "Arjun", "Sai", "Reyansh", "Krishna", "Ishaan", "Rohan", "Kabir",
    "Ananya", "Diya", "Priya", "Aadhya", "Saanvi", "Meera", "Kavya", "Isha", "Riya", "Nisha",
    "Rahul", "Vikram", "Siddharth", "Karthik", "Naveen", "Deepa", "Lakshmi", "Pooja", "Sneha", "Divya",
    "Harsh", "Manoj", "Suresh", "Ramesh", "Farhan", "Imran", "Zoya", "Sana", "Gurpreet", "Harpreet",
    "Tanvi", "Neha", "Anjali", "Shreya", "Varun", "Nikhil", "Akash", "Yash", "Tara", "Gauri",
]
LAST_INITIAL = list("ABCDGHIJKLMNPRSTVY")
CITIES = ["Bengaluru", "Mumbai", "Delhi", "Chennai", "Hyderabad", "Pune", "Kolkata", "Jaipur", "Lucknow", "Kochi"]
OSES = ["Android 15", "Android 14", "Android 13", "iOS 19", "iOS 18"]
CHANNELS = ["UPI", "UPI", "UPI", "IMPS", "NEFT"]

SNAPSHOT = datetime(2026, 10, 14, 9, 0, 0)  # the moment the bank's data export was taken

SCAM_TYPES = ["job_scam", "kyc_scam", "investment_scam", "fake_customer_care", "loan_app_scam"]


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


class Builder:
    """Collects nodes and edges with unique ids."""

    def __init__(self, seed: int, bank: str):
        self.rng = random.Random(seed)
        self.bank = bank
        self.nodes: dict[str, dict[str, dict]] = {}
        self.edges: list[dict] = []
        self.used_ids: set[str] = set()
        self.counters: dict[str, int] = {}

    # ---------- ids ----------
    def reserve(self, *ids: str):
        self.used_ids.update(ids)

    def new_id(self, prefix: str, digits: int = 4, start: int = 1000) -> str:
        while True:
            n = self.rng.randint(start, 10**digits - 1)
            nid = f"{prefix}-{n}" if prefix != "D" else f"D{n}"
            if nid not in self.used_ids:
                self.used_ids.add(nid)
                return nid

    # ---------- primitives ----------
    def node(self, label: str, nid: str, **props) -> str:
        self.used_ids.add(nid)
        props = {"id": nid, "created_at": props.pop("created_at", iso(SNAPSHOT)), **props}
        self.nodes.setdefault(label, {})[nid] = props
        return nid

    def edge(self, src_label, src, rel, dst_label, dst, **props):
        self.edges.append({"src_label": src_label, "src": src, "type": rel,
                           "dst_label": dst_label, "dst": dst, "props": props})

    def name(self) -> str:
        return f"{self.rng.choice(FIRST)} {self.rng.choice(LAST_INITIAL)}."

    def phone(self, nid: str | None = None, number: str | None = None) -> str:
        nid = nid or self.new_id("PH", 5, 10000)
        number = number or f"+91-90000-{self.rng.randint(10000, 99999)}"
        return self.node("Phone", nid, number=number)

    def device(self, nid: str | None = None) -> str:
        nid = nid or self.new_id("D", 5, 10000)
        return self.node("Device", nid, fingerprint=f"fp-{self.rng.getrandbits(40):010x}", os=self.rng.choice(OSES))

    def ip(self, nid: str | None = None) -> str:
        nid = nid or self.new_id("IP", 5, 10000)
        addr = f"10.{self.rng.randint(0, 255)}.{self.rng.randint(0, 255)}.{self.rng.randint(1, 254)}"
        return self.node("IP", nid, address=addr, city=self.rng.choice(CITIES))

    def account(self, nid: str | None = None, holder: str | None = None, opened: datetime | None = None,
                status: str = "active", upi_handle: str | None = None, upi_id: str | None = None,
                phone_id: str | None = None, phone_number: str | None = None) -> str:
        nid = nid or self.new_id("ACC")
        holder = holder or self.name()
        opened = opened or SNAPSHOT - timedelta(days=self.rng.randint(200, 3000))
        self.node("Account", nid, holder_name=holder, bank=self.bank, opened_at=iso(opened),
                  status=status, risk_score=0)
        handle = upi_handle or f"{holder.split()[0].lower()}.{self.rng.randint(100, 9999)}@{self.bank.lower()}"
        u = self.node("UPI", upi_id or self.new_id("UPI", 5, 10000), handle=handle)
        self.edge("UPI", u, "LINKED_TO", "Account", nid)
        p = self.phone(phone_id, phone_number)
        self.edge("Account", nid, "REGISTERED_PHONE", "Phone", p)
        return nid

    def uses_device(self, acc, dev, when: datetime):
        self.edge("Account", acc, "USED_DEVICE", "Device", dev, last_seen=iso(when))

    def logged_in(self, acc, ip, when: datetime):
        self.edge("Account", acc, "LOGGED_IN_FROM", "IP", ip, ts=iso(when))

    def pay(self, src, dst, amount: int, when: datetime):
        tid = f"TXN-{self.counters.setdefault('txn', 100000)}"
        self.counters["txn"] += 1
        self.node("Transaction", tid, amount=amount, ts=iso(when), channel=self.rng.choice(CHANNELS),
                  created_at=iso(when))
        self.edge("Account", src, "SENT", "Transaction", tid)
        self.edge("Transaction", tid, "TO", "Account", dst)

    def doc(self, nid, title, source, text, when: datetime):
        url = f"https://intranet.{self.bank.lower()}.example/{source}/{nid}"
        return self.node("Document", nid, title=title, source=source, url=url, text=text, ts=iso(when),
                         created_at=iso(when))


def rand_time(rng: random.Random, start: datetime, end: datetime) -> datetime:
    return start + timedelta(seconds=rng.randint(0, int((end - start).total_seconds())))


# ---------------------------------------------------------------- normal background

def build_normal(b: Builder, n_normal: int) -> list[str]:
    rng = b.rng
    accounts = []
    for _ in range(n_normal):
        acc = b.account()
        accounts.append(acc)
        when = rand_time(rng, SNAPSHOT - timedelta(days=60), SNAPSHOT)
        b.uses_device(acc, b.device(), when)
        b.logged_in(acc, b.ip(), when)
    # households: ~8% of accounts share a device and IP with one family member (harmless sharing)
    for _ in range(int(n_normal * 0.04)):
        a, c = rng.sample(accounts, 2)
        d = b.device()
        i = b.ip()
        for acc in (a, c):
            when = rand_time(rng, SNAPSHOT - timedelta(days=30), SNAPSHOT)
            b.uses_device(acc, d, when)
            b.logged_in(acc, i, when)
    # merchants: a few accounts receive lots of payments (shops, landlords, fee collectors)
    merchants = rng.sample(accounts, max(5, n_normal // 100))
    for _ in range(n_normal * 3):
        src = rng.choice(accounts)
        dst = rng.choice(merchants) if rng.random() < 0.35 else rng.choice(accounts)
        if src == dst:
            continue
        b.pay(src, dst, rng.choice([150, 300, 499, 850, 1200, 2500, 4000, 7500, 12000]),
              rand_time(rng, SNAPSHOT - timedelta(days=45), SNAPSHOT))
    return accounts


# ---------------------------------------------------------------- scam rings

RING_TEMPLATES = {
    "job_scam": {"domain": "hr-jobs-portal.example", "path": "/apply", "handle": "hr.jobs"},
    "kyc_scam": {"domain": "kyc-update-{bank}.example", "path": "/verify", "handle": "kyc.support"},
    "investment_scam": {"domain": "cryptogrow-club.example", "path": "/join", "handle": "growth.club"},
    "fake_customer_care": {"domain": "care-helpdesk-24x7.example", "path": "/ticket", "handle": "care.desk"},
    "loan_app_scam": {"domain": "instaloan-app.example", "path": "/get", "handle": "instaloan"},
}


def build_ring(b: Builder, normals: list[str], ring_key: str, scam_type: str, n_front: int, n_layer: int,
               n_victims: int, fixed: dict | None = None) -> dict:
    """A scam ring: victims pay front mules -> layer mules -> (second layer) -> collector.
    Ring members share a small pool of devices and IPs, which is what gives them away."""
    rng = b.rng
    fixed = fixed or {}
    tpl = RING_TEMPLATES[scam_type]
    start = SNAPSHOT - timedelta(days=rng.randint(10, 14))
    young = lambda: SNAPSHOT - timedelta(days=rng.randint(20, 90))  # mule accounts are new

    collector = b.account(fixed.get("collector"), opened=young())
    fronts, layers = [], []
    for i in range(n_front):
        kw = {}
        if i == 0 and "front_upi_handle" in fixed:
            kw = {"nid": fixed["front"], "upi_handle": fixed["front_upi_handle"], "upi_id": fixed["front_upi_id"]}
        elif i < 3:
            kw = {"upi_handle": f"{tpl['handle']}{rng.randint(10, 99)}@fakepay"}
        fronts.append(b.account(opened=young(), **kw))
    for _ in range(n_layer):
        layers.append(b.account(opened=young()))
    if fixed.get("low_activity"):  # a recruited mule with very little activity (should go to review)
        layers.append(b.account(fixed["low_activity"], opened=young()))

    # shared infrastructure
    hub = b.device(fixed.get("hub_device"))
    d2, d3 = b.device(fixed.get("device2")), b.device(fixed.get("device3"))
    ip1, ip2 = b.ip(), b.ip()
    half = len(layers) // 2
    for acc in fronts:
        b.uses_device(acc, hub, rand_time(rng, start, SNAPSHOT))
        b.logged_in(acc, ip1, rand_time(rng, start, SNAPSHOT))
    b.uses_device(fronts[-1], d2, rand_time(rng, start, SNAPSHOT))
    for acc in layers[:half]:
        b.uses_device(acc, d2, rand_time(rng, start, SNAPSHOT))
        b.logged_in(acc, ip2, rand_time(rng, start, SNAPSHOT))
    for acc in layers[half:]:
        b.uses_device(acc, d3, rand_time(rng, start, SNAPSHOT))
        b.logged_in(acc, ip2, rand_time(rng, start, SNAPSHOT))
    for acc in layers[:2]:
        b.uses_device(acc, hub, rand_time(rng, start, SNAPSHOT))
    b.uses_device(collector, hub, rand_time(rng, start, SNAPSHOT))
    b.uses_device(collector, d3, rand_time(rng, start, SNAPSHOT))

    # the scam link, promoted by a ring phone
    link_id = fixed.get("link") or b.new_id("LNK", 4, 1000)
    domain = tpl["domain"].format(bank=b.bank.lower())
    b.node("Link", link_id, url=f"https://{domain}{tpl['path']}", domain=domain)
    promo_phone = b.phone()
    b.edge("Link", link_id, "PROMOTED_BY", "Phone", promo_phone)
    b.edge("Account", fronts[min(1, len(fronts) - 1)], "REGISTERED_PHONE", "Phone", promo_phone)

    # money: victims -> fronts -> layer A -> layer B -> collector
    victims = [a for a in rng.sample(normals, n_victims) if a not in fixed.get("exclude_victims", [])]
    if fixed.get("priya_account"):
        victims.append(fixed["priya_account"])
    stolen = 0
    layer_a, layer_b = layers[:half] or layers, layers[half:] or layers
    active_layer_b = [x for x in layer_b if x != fixed.get("low_activity")]
    victim_payments = []
    for v in victims:
        for _ in range(rng.randint(1, 3)):
            amt = rng.choice([1999, 4999, 8500, 12000, 18500, 25000, 49000])
            if v == fixed.get("priya_account"):
                amt = 18500
            front = fixed["front"] if v == fixed.get("priya_account") else rng.choice(fronts)
            t0 = rand_time(rng, start, SNAPSHOT - timedelta(hours=8))
            b.pay(v, front, amt, t0)
            victim_payments.append((v, front, amt))
            stolen += amt
            # layering, minutes to hours later, skimming a commission at each hop
            la = rng.choice(layer_a)
            b.pay(front, la, int(amt * 0.95), t0 + timedelta(minutes=rng.randint(5, 90)))
            lb = rng.choice(active_layer_b) if active_layer_b else la
            b.pay(la, lb, int(amt * 0.9), t0 + timedelta(minutes=rng.randint(100, 240)))
            b.pay(lb, collector, int(amt * 0.85), t0 + timedelta(minutes=rng.randint(250, 480)))
            if v == fixed.get("priya_account"):
                break
    if fixed.get("low_activity"):
        b.pay(fronts[0], fixed["low_activity"], 1200, rand_time(rng, start, SNAPSHOT))

    return {
        "ring_key": ring_key, "scam_type": scam_type, "collector": collector, "fronts": fronts,
        "layers": layers, "members": [collector] + fronts + layers, "hub_device": hub,
        "link": link_id, "promo_phone": promo_phone, "victims": victims, "stolen": stolen,
        "victim_payments": victim_payments,
    }


def build_reports(b: Builder, ring: dict, n_reports: int, exclude_handles: set[str]) -> list[str]:
    """Earlier victim reports about a ring that nobody connected (the bank's backlog)."""
    rng = b.rng
    users = []
    upis_of = {}
    for e in b.edges:
        if e["type"] == "LINKED_TO":
            upis_of.setdefault(e["dst"], []).append(e["src"])
    victims = [v for v in ring["victims"] if v != "ACC-2207"]
    rng.shuffle(victims)
    for i, v in enumerate(victims[:n_reports]):
        uid = f"USR-{v.split('-')[1]}"
        holder = b.nodes["Account"][v]["holder_name"]
        if uid not in b.nodes.get("User", {}):
            b.node("User", uid, name=holder, language=rng.choice(["en", "hi", "kn", "ta"]))
            b.edge("User", uid, "HOLDS", "Account", v)
        users.append(uid)
        when = rand_time(rng, SNAPSHOT - timedelta(days=9), SNAPSHOT - timedelta(hours=10))
        rid = f"RPT-{b.counters.setdefault('rpt', 5000)}"
        b.counters["rpt"] += 1
        kind = rng.choice(["upi", "upi", "link", "phone"])
        if kind == "upi":
            front = rng.choice(ring["fronts"])
            target_label, target = "UPI", upis_of[front][0]
            if b.nodes["UPI"][target]["handle"] in exclude_handles:
                target_label, target = "Link", ring["link"]
                kind = "link"
        elif kind == "link":
            target_label, target = "Link", ring["link"]
        else:
            target_label, target = "Phone", ring["promo_phone"]
        desc = {
            "job_scam": "Paid a 'registration fee' for a work-from-home job, then they stopped replying.",
            "kyc_scam": "Got an SMS that my account will be blocked unless I update KYC through a link.",
            "investment_scam": "Joined a trading group promising 3x returns, cannot withdraw anything.",
            "fake_customer_care": "Called a 'customer care' number from a search ad, they asked me to send money to verify.",
            "loan_app_scam": "Loan app asked for a processing fee and now threatens to call my contacts.",
        }[ring["scam_type"]]
        b.node("ScamReport", rid, ts=iso(when), scam_type=ring["scam_type"], description=desc,
               reporter_id=uid, status="unassigned", created_at=iso(when))
        b.edge("User", uid, "FILED", "ScamReport", rid)
        b.edge("ScamReport", rid, "MENTIONS", target_label, target)
    return users


# ---------------------------------------------------------------- company layer (Track 03)

ANALYSTS = [
    ("ANL-ARJUN", "Arjun M.", "Fraud Investigator", "TEAM-INV", ["job_scam", "kyc_scam"]),
    ("ANL-MEERA", "Meera K.", "Investigations Lead", "TEAM-INV", ["kyc_scam", "investment_scam"]),
    ("ANL-FARHAN", "Farhan Q.", "Fraud Investigator", "TEAM-INV", ["investment_scam"]),
    ("ANL-DEEPA", "Deepa R.", "Fraud Investigator", "TEAM-INV", ["fake_customer_care", "loan_app_scam"]),
    ("ANL-VIKRAM", "Vikram T.", "Customer Protection Lead", "TEAM-OPS", ["loan_app_scam"]),
    ("ANL-SNEHA", "Sneha P.", "Customer Protection Officer", "TEAM-OPS", ["job_scam"]),
    ("ANL-KARTHIK", "Karthik N.", "Customer Protection Officer", "TEAM-OPS", ["fake_customer_care"]),
    ("ANL-TANVI", "Tanvi G.", "Compliance Analyst", "TEAM-OPS", ["kyc_scam"]),
]

POLICIES = [
    ("P-03", "alert", None, "Customer alerts for exposed users",
     "Users who paid an account later confirmed as part of a scam ring must be alerted within 24 hours "
     "with safety guidance. Alerts must not name other victims.", "2025-01-10", {"topic": "alert"}),
    ("P-05", "complaint", None, "Cyber-crime complaint filing",
     "For every actioned scam case, prepare a complaint draft for the national cyber-crime portal with the "
     "transaction trail, frozen accounts and evidence path. A human analyst submits it.", "2025-03-01",
     {"topic": "complaint"}),
    ("P-06", "kyc", None, "KYC re-verification of new accounts",
     "Accounts younger than 90 days that receive more than 10 inbound transfers from unrelated senders in 24 hours "
     "must be re-verified.", "2025-06-15", {"topic": "kyc"}),
    ("P-07", "freeze", "P-09", "Account freeze threshold (v1)",
     "An account may be frozen when its fraud risk score is 80 or higher and the freeze is approved by an "
     "investigations lead.", "2025-04-01", {"topic": "freeze", "freeze_threshold": 80, "review_threshold": 80}),
    ("P-09", "freeze", None, "Account freeze threshold (v2)",
     "Replaces P-07. Accounts with a risk score of 70 or higher that are members of a detected ring may be "
     "frozen immediately; scores from 50 to 69 go to human review by an analyst who knows the scam type. "
     "Every freeze must store its evidence path.", "2026-09-01",
     {"topic": "freeze", "freeze_threshold": 70, "review_threshold": 50}),
    ("P-11", "retention", None, "Evidence retention",
     "Evidence paths, Cypher queries and agent decisions are retained for 7 years.", "2025-02-01",
     {"topic": "retention"}),
]


def build_company(b: Builder, normals: list[str], include_demo: bool) -> None:
    rng = b.rng
    b.node("Team", "TEAM-INV", name="Fraud Investigations")
    b.node("Team", "TEAM-OPS", name="Customer Protection Ops")
    for st in SCAM_TYPES:
        b.node("ScamType", f"ST-{st}", name=st)
    for aid, name, role, team, skills in ANALYSTS:
        b.node("Analyst", aid, name=name, role=role)
        b.edge("Analyst", aid, "MEMBER_OF", "Team", team)
        for s in skills:
            b.edge("Analyst", aid, "EXPERT_IN", "ScamType", f"ST-{s}")
    for code, topic, superseded_by, title, text, eff, extra in POLICIES:
        b.node("Policy", code, code=code, title=title, text=text, effective_from=eff,
               superseded_by=superseded_by or "", **extra)
    b.edge("Policy", "P-09", "SUPERSEDES", "Policy", "P-07")
    for aid in ("AGT-intake", "AGT-investigator", "AGT-action", "AGT-brain"):
        b.node("Agent", aid, name=aid.split("-")[1])

    investigators = [a for a in ANALYSTS if a[3] == "TEAM-INV" and "Lead" not in a[2]]
    outcomes = ["freeze", "freeze", "release", "no_action", "review"]

    # the planted past case for demo scenario 3
    if include_demo:
        when = datetime(2026, 8, 17, 15, 40)
        old_dev = b.device("D-OLD")
        old_ring = []
        for nid in ("ACC-5530", None, None, None):
            acc = b.account(nid, opened=when - timedelta(days=40), status="frozen")
            b.uses_device(acc, old_dev, when - timedelta(days=2))
            old_ring.append(acc)
        b.node("Ring", "RING-0817", size=len(old_ring), scam_type="kyc_scam", detected_at=iso(when),
               detected_by="ANL-ARJUN", created_at=iso(when))
        for acc in old_ring:
            b.edge("Account", acc, "MEMBER_OF", "Ring", "RING-0817")
        b.node("Case", "C-112", status="closed", opened_at=iso(when - timedelta(hours=5)),
               summary="KYC-update scam: ACC-5530 collected transfers from 14 unrelated senders and shared "
                       "device D-OLD with 3 reported accounts.", scam_type="kyc_scam", created_at=iso(when))
        b.edge("Analyst", "ANL-ARJUN", "OWNS", "Case", "C-112")
        b.edge("Case", "C-112", "PART_OF_RING", "Ring", "RING-0817")
        b.doc("TKT-4471", "Freeze request: ACC-5530 (KYC scam collector)", "ticket",
              "ACC-5530 received 14 inbound UPI transfers from unrelated senders within 3 hours and shares device "
              "D-OLD with 3 accounts reported for a KYC-update scam. Requesting a freeze under P-07. Risk score 86. "
              "-- Arjun M.", when - timedelta(hours=1))
        b.doc("CHAT-0817", "#fraud-escalations, 17 Aug", "chat",
              "Meera K.: Reviewed TKT-4471. Score 86 is above the P-07 line of 80, approved. Freeze ACC-5530 "
              "and the 3 linked accounts. Arjun M.: Done.", when)
        b.node("Decision", "DEC-112", ts=iso(when), outcome="freeze",
               reason="Collector of a KYC-update scam ring; risk score 86 above the P-07 threshold of 80.",
               created_at=iso(when))
        b.edge("Decision", "DEC-112", "ABOUT", "Case", "C-112")
        b.edge("Decision", "DEC-112", "TARGETS", "Account", "ACC-5530")
        b.edge("Decision", "DEC-112", "MADE_BY", "Analyst", "ANL-ARJUN")
        b.edge("Decision", "DEC-112", "APPROVED_BY", "Analyst", "ANL-MEERA")
        b.edge("Decision", "DEC-112", "UNDER_POLICY", "Policy", "P-07")
        b.edge("Decision", "DEC-112", "SOURCED_FROM", "Document", "TKT-4471")
        b.edge("Decision", "DEC-112", "SOURCED_FROM", "Document", "CHAT-0817")

    # ~30 other historical cases and decisions
    for i in range(30):
        cid = f"C-{120 + i}"
        when = rand_time(rng, datetime(2026, 5, 1), datetime(2026, 10, 10))
        st = rng.choice(SCAM_TYPES)
        acc = rng.choice(normals)
        outcome = rng.choice(outcomes)
        owner = rng.choice(investigators)
        lead = "ANL-MEERA" if owner[3] == "TEAM-INV" else "ANL-VIKRAM"
        policy = "P-07" if when < datetime(2026, 9, 1) else "P-09"
        if outcome in ("release", "no_action"):
            policy = "P-06" if st == "kyc_scam" else policy
        score = rng.randint(40, 95)
        b.node("Case", cid, status="closed", opened_at=iso(when - timedelta(hours=rng.randint(2, 30))),
               summary=f"{st.replace('_', ' ')} report involving {acc}; outcome {outcome}.", scam_type=st,
               created_at=iso(when))
        b.edge("Analyst", owner[0], "OWNS", "Case", cid)
        tkt = f"TKT-{4500 + i}"
        b.doc(tkt, f"{outcome.title()} review: {acc}", "ticket",
              f"Case {cid}: {st.replace('_', ' ')} report. Account {acc} risk score {score}. "
              f"Recommendation: {outcome}. -- {owner[1]}", when - timedelta(hours=1))
        did = f"DEC-{120 + i}"
        b.node("Decision", did, ts=iso(when), outcome=outcome,
               reason=f"Risk score {score}; reviewed under {policy}.", created_at=iso(when))
        b.edge("Decision", did, "ABOUT", "Case", cid)
        b.edge("Decision", did, "TARGETS", "Account", acc)
        b.edge("Decision", did, "MADE_BY", "Analyst", owner[0])
        b.edge("Decision", did, "APPROVED_BY", "Analyst", lead)
        b.edge("Decision", did, "UNDER_POLICY", "Policy", policy)
        b.edge("Decision", did, "SOURCED_FROM", "Document", tkt)
        if outcome == "freeze":
            b.nodes["Account"][acc]["status"] = "frozen"

    # a couple of general knowledge documents
    b.doc("DOC-RUNBOOK-1", "Runbook: handling job-scam reports", "doc",
          "Job scams usually advertise a link through a phone number on messaging apps. Start from the reported "
          "UPI, check which devices the receiving account shares, then follow money towards the collector.",
          datetime(2026, 7, 2))
    b.doc("DOC-RUNBOOK-2", "Runbook: KYC-update scams", "doc",
          "KYC scams send SMS links impersonating the bank. Freeze decisions follow the current freeze policy; "
          "check P-09 for thresholds.", datetime(2026, 9, 3))


def build_playbooks(b: Builder):
    """Procedural memory: the starting investigation playbook for each scam type."""
    base = ["lookup_entity", "expand_neighbors", "find_shared_infrastructure", "detect_ring",
            "rank_ring_members", "money_trail", "risk_score"]
    variants = {
        "job_scam": base,
        "kyc_scam": ["lookup_entity", "expand_neighbors", "detect_ring", "find_shared_infrastructure",
                     "rank_ring_members", "money_trail", "risk_score"],
        "investment_scam": base,
        "fake_customer_care": base,
        "loan_app_scam": base,
    }
    when = datetime(2026, 9, 20)
    for st, steps in variants.items():
        mid = f"MEM-PLAY-{st}-v1"
        b.node("Memory", mid, kind="procedural", scam_type=st, version=1, confidence=0.6,
               content=json.dumps({"steps": steps, "note": "starting playbook"}), ts=iso(when),
               created_at=iso(when))
        b.edge("Agent", "AGT-investigator", "LEARNED", "Memory", mid)
        b.edge("Memory", mid, "ABOUT", "ScamType", f"ST-{st}")


# ---------------------------------------------------------------- main build

def generate(tenant: str, seed: int, n_normal: int, include_demo: bool) -> dict:
    bank = {"demobank": "DemoBank", "suraksha": "SurakshaBank"}.get(tenant, tenant.title() + "Bank")
    b = Builder(seed, bank)
    if include_demo:
        b.reserve("ACC-7781", "ACC-3302", "ACC-5530", "ACC-2207", "ACC-9104", "ACC-6619", "D17", "D18", "D19",
                  "UPI-JOBS", "LNK-0001", "USR-PRIYA")
    normals = build_normal(b, n_normal)

    rings = []
    exclude_handles = set()
    if include_demo:
        # Priya's own account (a normal customer)
        b.account("ACC-2207", holder="Priya S.", opened=SNAPSHOT - timedelta(days=1400))
        d, i = b.device(), b.ip()
        b.uses_device("ACC-2207", d, SNAPSHOT - timedelta(days=3))
        b.logged_in("ACC-2207", i, SNAPSHOT - timedelta(days=3))
        b.node("User", "USR-PRIYA", name="Priya S.", language="en")
        b.edge("User", "USR-PRIYA", "HOLDS", "Account", "ACC-2207")
        rings.append(build_ring(b, normals, "R1", "job_scam", n_front=4, n_layer=7, n_victims=14, fixed={
            "collector": "ACC-7781", "front": "ACC-3302", "front_upi_handle": "hr.jobs2026@fakepay",
            "front_upi_id": "UPI-JOBS", "hub_device": "D17", "device2": "D18", "device3": "D19",
            "link": "LNK-0001", "priya_account": "ACC-2207", "low_activity": "ACC-6619",
        }))
        exclude_handles.add("hr.jobs2026@fakepay")  # nobody has reported this UPI yet: Priya is first
        others = [("R2", "kyc_scam", 3, 9, 12), ("R3", "investment_scam", 3, 13, 10),
                  ("R4", "fake_customer_care", 2, 4, 8), ("R5", "loan_app_scam", 3, 6, 9)]
    else:
        others = [("R1", "investment_scam", 3, 8, 8), ("R2", "loan_app_scam", 2, 5, 6)]
    for key, st, nf, nl, nv in others:
        rings.append(build_ring(b, normals, key, st, nf, nl, nv))

    n_reports_per_ring = {"R1": 9, "R2": 14, "R3": 12, "R4": 10, "R5": 11} if include_demo else {"R1": 5, "R2": 4}
    for r in rings:
        build_reports(b, r, n_reports_per_ring.get(r["ring_key"], 6), exclude_handles)

    build_company(b, normals, include_demo)
    build_playbooks(b)

    # demo scenario 2 lives in a "feed" applied later (a new mule account opened after the snapshot)
    feed = {}
    if include_demo:
        feed = {
            "account": {"id": "ACC-9104", "holder_name": "Rakesh V.", "bank": bank,
                        "opened_at": "2026-10-15T18:20:00", "status": "active", "risk_score": 0},
            "upi": {"id": "UPI-9104", "handle": "rakesh.9104@fakepay"},
            "phone": {"id": "PH-41177", "number": "+91-90000-41177"},
            "device": "D17",
            "ip": {"id": "IP-41177", "address": "10.41.17.7", "city": "Kolkata"},
            "first_payment_from": "ACC-3302",
            "first_payment_amount": 9000,
        }

    nodes = {label: list(m.values()) for label, m in b.nodes.items()}
    return {
        "tenant": tenant,
        "bank": bank,
        "seed": seed,
        "snapshot": iso(SNAPSHOT),
        "nodes": nodes,
        "edges": b.edges,
        "feed_scenario2": feed,
        "ground_truth": {
            r["ring_key"]: {"scam_type": r["scam_type"], "collector": r["collector"], "members": r["members"],
                            "hub_device": r["hub_device"], "stolen": r["stolen"], "link": r["link"]}
            for r in rings
        },
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--normal", type=int, default=2000, help="normal accounts in the main tenant")
    args = ap.parse_args()
    OUT_DIR.mkdir(exist_ok=True)
    for tenant, seed, n, demo in [("demobank", args.seed, args.normal, True),
                                  ("suraksha", args.seed + 7, max(300, args.normal // 4), False)]:
        data = generate(tenant, seed, n, demo)
        path = OUT_DIR / f"{tenant}.json"
        path.write_text(json.dumps(data))
        counts = {k: len(v) for k, v in data["nodes"].items()}
        print(f"{tenant}: {sum(counts.values())} nodes, {len(data['edges'])} edges -> {path}")
        print("   ", counts)
        for k, r in data["ground_truth"].items():
            print(f"    ring {k} ({r['scam_type']}): {len(r['members'])} accounts, collector {r['collector']}, "
                  f"stolen Rs {r['stolen']:,}")


if __name__ == "__main__":
    main()
