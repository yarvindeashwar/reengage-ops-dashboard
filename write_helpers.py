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
    """Reassign a removed operator's pending reviews equally to remaining operators."""
    from config import ROLE_LEAD

    conn = get_conn()

    # Get the removed operator's pending assignments
    orphaned = conn.execute(
        "SELECT assignment_id, review_uid, order_id, chain_name, platform, days_left "
        "FROM assignments WHERE operator_email = ? AND status = 'pending'",
        (removed_email,),
    ).fetchall()

    if not orphaned:
        conn.close()
        return

    # Get remaining approved non-lead operators
    remaining = conn.execute(
        "SELECT email FROM users WHERE approved = 1 AND role != ? AND email != ?",
        (ROLE_LEAD, removed_email),
    ).fetchall()

    if not remaining:
        conn.close()
        return

    operators = [r["email"] for r in remaining]
    now = _now()

    # Distribute round-robin
    for i, row in enumerate(orphaned):
        new_operator = operators[i % len(operators)]
        conn.execute(
            "UPDATE assignments SET operator_email = ?, assigned_at = ? "
            "WHERE assignment_id = ?",
            (new_operator, now, row["assignment_id"]),
        )

    conn.commit()
    conn.close()

    # Log the redistribution
    log_action(
        review_uid="batch", platform="all", chain_name="all",
        action="redistribute",
        operator_email=removed_email,
        performed_by="system",
        remarks=f"Redistributed {len(orphaned)} reviews from {removed_email} to {len(operators)} operators",
    )
