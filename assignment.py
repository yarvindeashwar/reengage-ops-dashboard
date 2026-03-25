"""
Review assignment engine.

Runs daily at 8 AM (via cron / Cloud Scheduler / manual trigger).
Distributes pending reviews to operators, optimising for:
  1. Expiring reviews first (lowest days_left)
  2. Minimum chain×platform variations per operator (fewer 3P logins)
  3. Equal load, adjusted for new_operator reduction_pct
  4. Leads are never assigned reviews
"""

from __future__ import annotations

import uuid

import pandas as pd

from config import ROLE_LEAD, ROLE_NEW
from local_db import get_conn
from data_loaders import load_reviews
from write_helpers import insert_assignments, expire_stale_assignments, log_action


def _load_operators() -> pd.DataFrame:
    """Load approved, non-lead operators from local SQLite."""
    conn = get_conn()
    df = pd.read_sql_query(
        f"SELECT email, name, role, reduction_pct FROM users "
        f"WHERE approved = 1 AND role != '{ROLE_LEAD}' ORDER BY email",
        conn,
    )
    conn.close()
    return df


def _load_already_assigned_uids() -> set:
    """Get review_uids that already have a pending or completed assignment."""
    conn = get_conn()
    df = pd.read_sql_query(
        "SELECT DISTINCT review_uid FROM assignments "
        "WHERE status IN ('pending', 'completed') "
        "AND assigned_at >= datetime('now', '-14 days')",
        conn,
    )
    conn.close()
    return set(df["review_uid"].tolist()) if not df.empty else set()


def run_assignment() -> dict:
    """
    Main assignment function. Returns summary stats.

    Algorithm:
    1. Get all PENDING reviews (not expired, not responded, not already assigned)
    2. Sort by days_left ASC (most urgent first)
    3. Group by (chain_name, platform)
    4. Calculate each operator's capacity based on role + reduction_pct
    5. Assign whole chain×platform groups to operators to minimize login variation
    6. If a group is too large, split across fewest operators possible
    """
    expire_stale_assignments()

    reviews = load_reviews()
    operators = _load_operators()

    if operators.empty:
        return {"error": "No approved operators found", "assigned": 0}
    if reviews.empty:
        return {"info": "No reviews to assign", "assigned": 0}

    pending = reviews[reviews["status"] == "PENDING"].copy()
    if pending.empty:
        return {"info": "No pending reviews", "assigned": 0}

    already_assigned = _load_already_assigned_uids()
    pending = pending[~pending["review_uid"].isin(already_assigned)]
    if pending.empty:
        return {"info": "All pending reviews already assigned", "assigned": 0}

    pending = pending.sort_values("days_left", ascending=True)

    # Build operator list with effective capacity weights
    ops = []
    for _, op in operators.iterrows():
        reduction = float(op.get("reduction_pct", 0)) if op["role"] == ROLE_NEW else 0.0
        weight = max(1.0 - reduction / 100.0, 0.1)
        ops.append({
            "email": op["email"],
            "role": op["role"],
            "weight": weight,
        })

    total_weight = sum(o["weight"] for o in ops)
    total_reviews = len(pending)

    for op in ops:
        op["target"] = round(total_reviews * op["weight"] / total_weight)
        op["assigned_count"] = 0
        op["chain_platforms"] = set()

    # Group reviews by (chain_name, platform), sorted by group size descending
    groups = {}
    for (chain, plat), grp in pending.groupby(["chain_name", "platform"]):
        groups[(chain, plat)] = grp.sort_values("days_left").to_dict("records")

    sorted_groups = sorted(groups.items(), key=lambda x: len(x[1]), reverse=True)

    assignments = []

    for (chain, plat), review_rows in sorted_groups:
        remaining = list(review_rows)

        while remaining:
            best = _pick_operator(ops, chain, plat)
            if best is None:
                break

            capacity_left = best["target"] - best["assigned_count"]
            if capacity_left <= 0:
                best = max(ops, key=lambda o: o["target"] - o["assigned_count"])
                capacity_left = max(best["target"] - best["assigned_count"], 1)

            take = remaining[:capacity_left]
            remaining = remaining[capacity_left:]

            for rev in take:
                assignments.append({
                    "assignment_id": str(uuid.uuid4()),
                    "review_uid": rev["review_uid"],
                    "order_id": rev.get("order_id", ""),
                    "operator_email": best["email"],
                    "chain_name": chain,
                    "platform": plat,
                    "days_left": rev["days_left"],
                })

            best["assigned_count"] += len(take)
            best["chain_platforms"].add((chain, plat))

    if assignments:
        insert_assignments(assignments)

    summary = {
        "assigned": len(assignments),
        "operators": len(ops),
        "groups": len(sorted_groups),
        "per_operator": {
            op["email"]: {
                "count": op["assigned_count"],
                "chain_platforms": len(op["chain_platforms"]),
            }
            for op in ops
        },
    }

    log_action(
        review_uid="batch", platform="all", chain_name="all",
        action="assign_batch", operator_email="system", performed_by="system",
        remarks=f"Assigned {len(assignments)} reviews to {len(ops)} operators",
    )

    return summary


def _pick_operator(ops: list[dict], chain: str, plat: str) -> dict | None:
    """
    Pick the best operator for a (chain, platform) group.
    Priority:
      1. Already has this (chain, plat) and has remaining capacity
      2. Fewest distinct (chain, plat) combos (minimise logins)
      3. Most remaining capacity
    """
    candidates = [o for o in ops if o["assigned_count"] < o["target"]]
    if not candidates:
        return None

    already_have = [o for o in candidates if (chain, plat) in o["chain_platforms"]]
    if already_have:
        return max(already_have, key=lambda o: o["target"] - o["assigned_count"])

    return min(candidates, key=lambda o: (len(o["chain_platforms"]), -(o["target"] - o["assigned_count"])))


if __name__ == "__main__":
    import json
    result = run_assignment()
    print(json.dumps(result, indent=2, default=str))
