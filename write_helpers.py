"""
Write operations — BigQuery v2 tables for assignments and ops log.
"""

import uuid
from collections import defaultdict
from datetime import datetime, timezone

from config import ROLE_LEAD, TABLE_ASSIGNMENTS, TABLE_OPS_LOG, TABLE_USERS
from db import bq_exec, bq_read


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _safe(val: str) -> str:
    return (val or "").replace("'", "''")


# ── Ops log ──────────────────────────────────────────────────────────────────

def log_action(review_uid, platform, chain_name, action, operator_email, performed_by, remarks=""):
    now = _now()
    bq_exec(f"""
    INSERT INTO `{TABLE_OPS_LOG}`
        (id, review_uid, platform, chain_name, action,
         operator_email, performed_by, remarks, processing_timestamp)
    VALUES
        ('{uuid.uuid4()}', '{_safe(review_uid)}', '{_safe(platform)}', '{_safe(chain_name)}',
         '{_safe(action)}', '{_safe(operator_email)}', '{_safe(performed_by)}',
         '{_safe(remarks)}', TIMESTAMP('{now}'))
    """)


# ── Assignment writes ────────────────────────────────────────────────────────

def mark_assignment_completed(assignment_id: str, performed_by: str):
    now = _now()
    bq_exec(f"""
    UPDATE `{TABLE_ASSIGNMENTS}`
    SET status = 'completed', completed_at = TIMESTAMP('{now}')
    WHERE assignment_id = '{_safe(assignment_id)}'
    """)


def expire_stale_assignments():
    bq_exec(f"""
    UPDATE `{TABLE_ASSIGNMENTS}`
    SET status = 'expired'
    WHERE status = 'pending' AND days_left <= 0
    """)


def insert_assignments(rows: list[dict]):
    if not rows:
        return
    now = _now()
    values = []
    for r in rows:
        values.append(
            f"('{r['assignment_id']}', '{_safe(r['review_uid'])}', '{_safe(r.get('order_id', ''))}', "
            f"'{_safe(r['operator_email'])}', '{_safe(r['chain_name'])}', '{_safe(r['platform'])}', "
            f"{r['days_left']}, 'pending', TIMESTAMP('{now}'), NULL)"
        )
    batch_size = 500
    for i in range(0, len(values), batch_size):
        batch = values[i:i + batch_size]
        bq_exec(f"""
        INSERT INTO `{TABLE_ASSIGNMENTS}`
            (assignment_id, review_uid, order_id, operator_email,
             chain_name, platform, days_left, status, assigned_at, completed_at)
        VALUES {', '.join(batch)}
        """)


def redistribute_assignments(removed_email: str):
    """
    Reassign a removed operator's pending reviews to remaining operators.
    Minimises chain×platform switches.
    """
    se = _safe(removed_email)

    orphaned_df = bq_read(f"""
        SELECT assignment_id, review_uid, order_id, chain_name, platform, days_left
        FROM `{TABLE_ASSIGNMENTS}`
        WHERE operator_email = '{se}' AND status = 'pending'
        ORDER BY days_left ASC
    """)

    if orphaned_df.empty:
        return

    remaining_df = bq_read(f"""
        SELECT email FROM `{TABLE_USERS}`
        WHERE approved = TRUE AND role != '{ROLE_LEAD}' AND email != '{se}'
    """)

    if remaining_df.empty:
        return

    operators = remaining_df["email"].tolist()

    existing_df = bq_read(f"""
        SELECT operator_email, chain_name, platform
        FROM `{TABLE_ASSIGNMENTS}`
        WHERE status = 'pending' AND operator_email != '{se}'
    """)

    op_combos = defaultdict(set)
    op_counts = defaultdict(int)
    if not existing_df.empty:
        for _, row in existing_df.iterrows():
            op_combos[row["operator_email"]].add((row["chain_name"], row["platform"]))
            op_counts[row["operator_email"]] += 1

    for op in operators:
        op_combos.setdefault(op, set())
        op_counts.setdefault(op, 0)

    groups = defaultdict(list)
    for _, row in orphaned_df.iterrows():
        groups[(row["chain_name"], row["platform"])].append(row)

    now = _now()
    updates = []

    for (chain, plat), reviews in groups.items():
        has_combo = [op for op in operators if (chain, plat) in op_combos[op]]
        no_combo = [op for op in operators if (chain, plat) not in op_combos[op]]
        has_combo.sort(key=lambda op: op_counts[op])
        no_combo.sort(key=lambda op: (len(op_combos[op]), op_counts[op]))
        preferred = has_combo + no_combo

        for i, row in enumerate(reviews):
            target = preferred[i % len(preferred)]
            updates.append((row["assignment_id"], target))
            op_counts[target] += 1
            op_combos[target].add((chain, plat))

    # Batch update in a single DML statement
    if updates:
        cases = " ".join(
            f"WHEN '{_safe(aid)}' THEN '{_safe(target)}'" for aid, target in updates
        )
        aid_list = ", ".join(f"'{_safe(aid)}'" for aid, _ in updates)
        bq_exec(f"""
        UPDATE `{TABLE_ASSIGNMENTS}`
        SET operator_email = CASE assignment_id {cases} END,
            assigned_at = TIMESTAMP('{now}')
        WHERE assignment_id IN ({aid_list})
        """)

    log_action(
        review_uid="batch", platform="all", chain_name="all",
        action="redistribute", operator_email=removed_email, performed_by="system",
        remarks=f"Redistributed {len(orphaned_df)} reviews from {removed_email} to {len(operators)} operators (login-optimised)",
    )
