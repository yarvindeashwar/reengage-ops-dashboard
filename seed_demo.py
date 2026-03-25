"""
Seed the local SQLite DB with demo users for a quick demo.
Run once:  python seed_demo.py
"""

from local_db import get_conn


DEMO_USERS = [
    ("lead@loopkitchen.com",     "Demo Lead",       "lead",              0),
    ("alice@loopkitchen.com",    "Alice (Tenured)",  "tenured_operator",  0),
    ("bob@loopkitchen.com",      "Bob (Tenured)",    "tenured_operator",  0),
    ("charlie@loopkitchen.com",  "Charlie (New)",    "new_operator",     20),
    ("diana@loopkitchen.com",    "Diana (New)",      "new_operator",     30),
]


def seed():
    conn = get_conn()
    for email, name, role, reduction in DEMO_USERS:
        conn.execute("""
            INSERT INTO users (email, name, role, approved, reduction_pct, added_by)
            VALUES (?, ?, ?, 1, ?, 'seed_demo')
            ON CONFLICT(email) DO UPDATE SET
                name = excluded.name, role = excluded.role,
                reduction_pct = excluded.reduction_pct
        """, (email, name, role, reduction))
    conn.commit()
    conn.close()
    print(f"Seeded {len(DEMO_USERS)} demo users:")
    for email, name, role, red in DEMO_USERS:
        suffix = f" (-{red}%)" if red else ""
        print(f"  {email:35s} {role}{suffix}")


if __name__ == "__main__":
    seed()
