"""
Write operations — local SQLite for assignments and ops log.
"""

import uuid
from datetime import datetime, timezone

from local_db import get_conn


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ── Ops log ──────────────────────────────────────────────────────────────────

def log_action(review_uid, platform, chain_name, action, operator_email, performed_by, remarks=""):
    conn = get_conn()
    conn.execute("""
        INSERT INTO ops_log (id, review_uid, platform, chain_name, action,
                             operator_email, performed_by, remarks, processing_timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (str(uuid.uuid4()), review_uid, platform, chain_name, action,
          operator_email, performed_by, remarks, _now()))
    conn.commit()
    conn.close()


# ── Assignment writes ────────────────────────────────────────────────────────

def mark_assignment_completed(assignment_id: str, performed_by: str):
    conn = get_conn()
    conn.execute(
        "UPDATE assignments SET status = 'completed', completed_at = ? WHERE assignment_id = ?",
        (_now(), assignment_id),
    )
    conn.commit()
    conn.close()


def expire_stale_assignments():
    """Mark assignments as expired when the review window has passed."""
    conn = get_conn()
    conn.execute("UPDATE assignments SET status = 'expired' WHERE status = 'pending' AND days_left <= 0")
    conn.commit()
    conn.close()


def insert_assignments(rows: list[dict]):
    """Bulk insert assignment rows."""
    if not rows:
        return
    now = _now()
    conn = get_conn()
    conn.executemany("""
        INSERT INTO assignments
            (assignment_id, review_uid, order_id, operator_email,
             chain_name, platform, days_left, status, assigned_at, completed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)
    """, [
        (r["assignment_id"], r["review_uid"], r.get("order_id", ""),
         r["operator_email"], r["chain_name"], r["platform"],
         r["days_left"], now)
        for r in rows
    ])
    conn.commit()
    conn.close()


def redistribute_assignments(removed_email: str):
    """
    Reassign a removed operator's pending reviews to remaining operators.
    Minimises chain×platform switches — prefers operators who already
    handle the same (chain, platform) combo so they don't need extra logins.
    """
    from collections import defaultdict
    from config import ROLE_LEAD

    conn = get_conn()

    orphaned = conn.execute(
        "SELECT assignment_id, review_uid, order_id, chain_name, platform, days_left "
        "FROM assignments WHERE operator_email = ? AND status = 'pending' "
        "ORDER BY days_left ASC",
        (removed_email,),
    ).fetchall()

    if not orphaned:
        conn.close()
        return

    remaining = conn.execute(
        "SELECT email FROM users WHERE approved = 1 AND role != ? AND email != ?",
        (ROLE_LEAD, removed_email),
    ).fetchall()

    if not remaining:
        conn.close()
        return

    operators = [r["email"] for r in remaining]

    # Build map: which (chain, platform) combos each operator already handles
    existing = conn.execute(
        "SELECT operator_email, chain_name, platform "
        "FROM assignments WHERE status = 'pending' AND operator_email != ?",
        (removed_email,),
    ).fetchall()

    op_combos = defaultdict(set)     # email -> set of (chain, platform)
    op_counts = defaultdict(int)     # email -> current pending count
    for row in existing:
        op_combos[row["operator_email"]].add((row["chain_name"], row["platform"]))
        op_counts[row["operator_email"]] += 1

    # Ensure all operators are in the maps even if they have 0 assignments
    for op in operators:
        op_combos.setdefault(op, set())
        op_counts.setdefault(op, 0)

    # Group orphaned reviews by (chain, platform)
    groups = defaultdict(list)
    for row in orphaned:
        groups[(row["chain_name"], row["platform"])].append(row)

    now = _now()

    for (chain, plat), reviews in groups.items():
        # Prefer operators who already have this combo (no extra login)
        # Break ties by fewest total assignments (balance load)
        has_combo = [op for op in operators if (chain, plat) in op_combos[op]]
        no_combo = [op for op in operators if (chain, plat) not in op_combos[op]]

        # Sort each group by current count ascending (least loaded first)
        has_combo.sort(key=lambda op: op_counts[op])
        no_combo.sort(key=lambda op: (len(op_combos[op]), op_counts[op]))

        # Prioritise operators who already have the combo, then others
        preferred = has_combo + no_combo

        for i, row in enumerate(reviews):
            target = preferred[i % len(preferred)]
            conn.execute(
                "UPDATE assignments SET operator_email = ?, assigned_at = ? "
                "WHERE assignment_id = ?",
                (target, now, row["assignment_id"]),
            )
            op_counts[target] += 1
            op_combos[target].add((chain, plat))

    conn.commit()
    conn.close()

    log_action(
        review_uid="batch", platform="all", chain_name="all",
        action="redistribute",
        operator_email=removed_email,
        performed_by="system",
        remarks=f"Redistributed {len(orphaned)} reviews from {removed_email} "
                f"to {len(operators)} operators (login-optimised)",
    )
