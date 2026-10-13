"""Load generated JSON into FalkorDB: one isolated graph per bank (tenant).

Usage:
    python -m graph.load                 # loads every file in data/out/
    python -m graph.load --tenant demobank
"""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import config
from graph.schema import create_indexes
from graph import queries as Q

DATA_DIR = config.ROOT / "data" / "out"
BATCH = 2000


def load_tenant(tenant: str, verbose: bool = True) -> dict:
    path = DATA_DIR / f"{tenant}.json"
    data = json.loads(path.read_text())
    db = config.connect()
    name = config.graph_name(tenant)
    if name in db.list_graphs():
        db.select_graph(name).delete()
    g = db.select_graph(name)
    t0 = time.time()
    create_indexes(g)

    for label, rows in data["nodes"].items():
        for i in range(0, len(rows), BATCH):
            g.query(f"UNWIND $rows AS r CREATE (n:{label}) SET n = r", {"rows": rows[i:i + BATCH]})

    groups = defaultdict(list)
    for e in data["edges"]:
        groups[(e["src_label"], e["type"], e["dst_label"])].append(
            {"s": e["src"], "d": e["dst"], "p": e["props"]})
    for (sl, rel, dl), rows in groups.items():
        for i in range(0, len(rows), BATCH):
            g.query(
                f"UNWIND $rows AS r MATCH (a:{sl} {{id: r.s}}), (b:{dl} {{id: r.d}}) "
                f"CREATE (a)-[x:{rel}]->(b) SET x = r.p",
                {"rows": rows[i:i + BATCH]})

    Q.refresh_paid_projection(g)
    stats = Q.graph_stats(g)
    if verbose:
        print(f"[{name}] loaded in {time.time() - t0:.1f}s: {stats}")
    return stats


def apply_scenario2_feed(tenant: str = "demobank") -> dict:
    """Simulates the bank's live feed: a new mule account opened ~14 hours before the demo,
    using the same device D17 as ring R1. Used by demo scenario 2 ("knowledge kept current")."""
    data = json.loads((DATA_DIR / f"{tenant}.json").read_text())
    feed = data.get("feed_scenario2")
    if not feed:
        return {"applied": False}
    from datetime import datetime, timedelta

    opened = (datetime.now() - timedelta(hours=14)).replace(microsecond=0)
    feed["account"]["opened_at"] = opened.isoformat()
    paid_at = (opened + timedelta(hours=2, minutes=45)).isoformat()
    g = config.get_graph(tenant)
    exists = g.query("MATCH (a:Account {id: $id}) RETURN a.id", {"id": feed["account"]["id"]}).result_set
    if exists:
        return {"applied": False, "reason": "already present"}
    g.query(
        """
        CREATE (a:Account) SET a = $acc, a.created_at = $acc.opened_at
        CREATE (u:UPI) SET u = $upi, u.created_at = $acc.opened_at
        CREATE (p:Phone) SET p = $phone, p.created_at = $acc.opened_at
        CREATE (i:IP) SET i = $ip, i.created_at = $acc.opened_at
        CREATE (u)-[:LINKED_TO]->(a), (a)-[:REGISTERED_PHONE]->(p), (a)-[:LOGGED_IN_FROM {ts: $acc.opened_at}]->(i)
        WITH a
        MATCH (d:Device {id: $device}), (src:Account {id: $src})
        CREATE (a)-[:USED_DEVICE {last_seen: $acc.opened_at}]->(d)
        CREATE (t:Transaction {id: 'TXN-FEED-9104', amount: $amt, ts: $paid_at, channel: 'UPI', created_at: $paid_at})
        CREATE (src)-[:SENT]->(t)-[:TO]->(a)
        """,
        {"acc": feed["account"], "upi": feed["upi"], "phone": feed["phone"], "ip": feed["ip"],
         "device": feed["device"], "src": feed["first_payment_from"], "amt": feed["first_payment_amount"],
         "paid_at": paid_at})
    Q.refresh_paid_projection(g)
    return {"applied": True, "account": feed["account"]["id"], "phone": feed["phone"]["number"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", help="load only this tenant")
    args = ap.parse_args()
    tenants = [args.tenant] if args.tenant else sorted(p.stem for p in DATA_DIR.glob("*.json"))
    if not tenants:
        raise SystemExit("No data found. Run: python -m data.generate")
    for t in tenants:
        load_tenant(t)


if __name__ == "__main__":
    main()
