"""
Review assignment engine.

Runs daily at 8 AM (via cron / Cloud Scheduler / manual trigger).
Distributes pending reviews to operators, optimising for:
  1. Expiring reviews first (lowest days_left)
  2. Minimum chain×platform variations per operator (fewer 3P logins)
  3. Equal load, adjusted for new_operator reduction_pct
  4. Leads are never assigned reviews

Algorithm (two-phase bin-packing):
  Phase 1: Assign whole chain×platform groups to operators to minimise
           the number of distinct groups (= logins) per operator.
           Uses a "largest-group-first, emptiest-operator-first" heuristic.
  Phase 2: Split any oversized groups that couldn't fit in one operator.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

import pandas as pd

from config import ROLE_LEAD, ROLE_NEW
from local_db import get_conn
from data_loaders import load_reviews
from write_helpers import insert_assignments, expire_stale_assignments, log_action


def _load_operators() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query(
        f"SELECT email, name, role, reduction_pct FROM users "
        f"WHERE approved = 1 AND role != '{ROLE_LEAD}' ORDER BY email",
        conn,
    )
    conn.close()
    return df


def _load_already_assigned_uids() -> set:
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

    # Build operator list with capacity targets
    ops = []
    for _, op in operators.iterrows():
        reduction = float(op.get("reduction_pct", 0)) if op["role"] == ROLE_NEW else 0.0
        weight = max(1.0 - reduction / 100.0, 0.1)
        ops.append({
            "email": op["email"],
            "role": op["role"],
            "weight": weight,
            "target": 0,
            "assigned_count": 0,
            "groups": [],           # list of (chain, plat) assigned
        })

    total_weight = sum(o["weight"] for o in ops)
    total_reviews = len(pending)

    for op in ops:
        op["target"] = round(total_reviews * op["weight"] / total_weight)

    # Group reviews by (chain_name, platform)
    groups = {}
    for (chain, plat), grp in pending.groupby(["chain_name", "platform"]):
        groups[(chain, plat)] = grp.sort_values("days_left").to_dict("records")

    # ── Phase 1: Plan group-to-operator mapping ──
    # Sort groups largest first. Assign each whole group to the operator
    # with the most remaining capacity. This naturally gives big groups to
    # high-capacity operators and small groups cluster together.
    sorted_groups = sorted(groups.items(), key=lambda x: len(x[1]), reverse=True)

    # mapping: (chain, plat) -> list of (operator_idx, count)
    group_plan = defaultdict(list)

    for (chain, plat), review_rows in sorted_groups:
        group_size = len(review_rows)

        # Find operator with most remaining capacity
        best_idx = _best_operator_for_group(ops, group_size)

        if best_idx is not None and ops[best_idx]["target"] - ops[best_idx]["assigned_count"] >= group_size:
            # Whole group fits in one operator
            group_plan[(chain, plat)].append((best_idx, group_size))
            ops[best_idx]["assigned_count"] += group_size
            ops[best_idx]["groups"].append((chain, plat))
        else:
            # Group too large for any single operator — split across operators
            remaining = group_size
            while remaining > 0:
                best_idx = _best_operator_for_group(ops, remaining)
                if best_idx is None:
                    # All operators full — force into operator with most remaining
                    best_idx = max(range(len(ops)),
                                   key=lambda i: ops[i]["target"] - ops[i]["assigned_count"])

                capacity = max(ops[best_idx]["target"] - ops[best_idx]["assigned_count"], 1)
                take = min(remaining, capacity)
                group_plan[(chain, plat)].append((best_idx, take))
                ops[best_idx]["assigned_count"] += take
                ops[best_idx]["groups"].append((chain, plat))
                remaining -= take

    # ── Phase 2: Build actual assignment rows from the plan ──
    assignments = []

    for (chain, plat), plan in group_plan.items():
        review_rows = groups[(chain, plat)]
        offset = 0
        for (op_idx, count) in plan:
            for rev in review_rows[offset:offset + count]:
                assignments.append({
                    "assignment_id": str(uuid.uuid4()),
                    "review_uid": rev["review_uid"],
                    "order_id": rev.get("order_id", ""),
                    "operator_email": ops[op_idx]["email"],
                    "chain_name": chain,
                    "platform": plat,
                    "days_left": rev["days_left"],
                })
            offset += count

    if assignments:
        insert_assignments(assignments)

    # Deduplicate group counts for summary
    summary = {
        "assigned": len(assignments),
        "operators": len(ops),
        "groups": len(sorted_groups),
        "per_operator": {
            op["email"]: {
                "count": op["assigned_count"],
                "chain_platforms": len(set(op["groups"])),
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


def _best_operator_for_group(ops: list[dict], group_size: int) -> int | None:
    """
    Pick the operator with the most remaining capacity.
    Among ties, prefer the one with fewer distinct groups (fewer logins).
    Returns the index into ops, or None if all are full.
    """
    best_idx = None
    best_remaining = 0
    best_groups = float("inf")

    for i, op in enumerate(ops):
        remaining = op["target"] - op["assigned_count"]
        if remaining <= 0:
            continue
        n_groups = len(set(op["groups"]))
        # Prefer: most remaining capacity, then fewest groups
        if (remaining > best_remaining) or \
           (remaining == best_remaining and n_groups < best_groups):
            best_idx = i
            best_remaining = remaining
            best_groups = n_groups

    return best_idx


if __name__ == "__main__":
    import json
    result = run_assignment()
    print(json.dumps(result, indent=2, default=str))
