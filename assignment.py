"""
Review assignment engine.

Runs daily at 8 AM (via Cloud Scheduler or manual trigger).
Distributes pending reviews to operators, optimising for:
  1. Expiring reviews first (lowest days_left)
  2. Minimum chain×platform variations per operator (fewer 3P logins)
  3. Equal load, adjusted for new_operator reduction_pct
  4. Leads are never assigned reviews
"""

from __future__ import annotations

import uuid
from collections import defaultdict

import pandas as pd

from config import ROLE_LEAD, ROLE_NEW, TABLE_USERS, TABLE_ASSIGNMENTS
from db import bq_read
from data_loaders import load_reviews
from write_helpers import insert_assignments, expire_stale_assignments, log_action


def _load_operators() -> pd.DataFrame:
    return bq_read(f"""
        SELECT email, name, role, reduction_pct
        FROM `{TABLE_USERS}`
        WHERE approved = TRUE AND role != '{ROLE_LEAD}'
        ORDER BY email
    """)


def _load_already_assigned_uids() -> set:
    df = bq_read(f"""
        SELECT DISTINCT review_uid
        FROM `{TABLE_ASSIGNMENTS}`
        WHERE status IN ('pending', 'completed')
          AND assigned_at >= TIMESTAMP(DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
    """)
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
            "groups": [],
        })

    total_weight = sum(o["weight"] for o in ops)
    total_reviews = len(pending)

    for op in ops:
        op["target"] = round(total_reviews * op["weight"] / total_weight)

    groups = {}
    for (chain, plat), grp in pending.groupby(["chain_name", "platform"]):
        groups[(chain, plat)] = grp.sort_values("days_left").to_dict("records")

    sorted_groups = sorted(groups.items(), key=lambda x: len(x[1]), reverse=True)

    group_plan = defaultdict(list)

    for (chain, plat), review_rows in sorted_groups:
        group_size = len(review_rows)
        best_idx = _best_operator_for_group(ops, group_size)

        if best_idx is not None and ops[best_idx]["target"] - ops[best_idx]["assigned_count"] >= group_size:
            group_plan[(chain, plat)].append((best_idx, group_size))
            ops[best_idx]["assigned_count"] += group_size
            ops[best_idx]["groups"].append((chain, plat))
        else:
            remaining = group_size
            while remaining > 0:
                best_idx = _best_operator_for_group(ops, remaining)
                if best_idx is None:
                    best_idx = max(range(len(ops)),
                                   key=lambda i: ops[i]["target"] - ops[i]["assigned_count"])
                capacity = max(ops[best_idx]["target"] - ops[best_idx]["assigned_count"], 1)
                take = min(remaining, capacity)
                group_plan[(chain, plat)].append((best_idx, take))
                ops[best_idx]["assigned_count"] += take
                ops[best_idx]["groups"].append((chain, plat))
                remaining -= take

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
    best_idx = None
    best_remaining = 0
    best_groups = float("inf")

    for i, op in enumerate(ops):
        remaining = op["target"] - op["assigned_count"]
        if remaining <= 0:
            continue
        n_groups = len(set(op["groups"]))
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
