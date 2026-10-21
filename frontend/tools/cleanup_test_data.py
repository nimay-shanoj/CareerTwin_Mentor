"""Remove the rows that the verification scripts created.

Keeps the seeded mentors and the accounts a human made; removes the
probe@example.test / sana-* / ravi-* accounts and everything attached to them.
"""

import sqlite3
import sys

DB = "instance/careertwin.db"
JUNK_EXACT = ("probe@example.test",)
JUNK_PREFIX = ("sana-", "ravi-")

connection = sqlite3.connect(DB)
cur = connection.cursor()

junk_users = []
for email, in cur.execute("SELECT email FROM users").fetchall():
    if email in JUNK_EXACT or email.startswith(JUNK_PREFIX):
        junk_users.append((email,))

if not junk_users:
    print("nothing to clean")
    sys.exit(0)

ids = [
    row[0]
    for row in cur.execute("SELECT id FROM users WHERE email IN (%s)"
                           % ",".join("?" * len(junk_users)),
                           [email for email, in junk_users]).fetchall()
]
print(f"removing {len(ids)} test account(s):")
for email, in junk_users:
    print("  -", email)

placeholders = ",".join("?" * len(ids))

# Requests first (they reference the users), then conversations and messages.
cur.execute(f"DELETE FROM messages WHERE conversation_id IN "
            f"(SELECT id FROM conversations WHERE mentorship_id IN "
            f"(SELECT id FROM mentorship_requests WHERE mentee_id IN ({placeholders}) "
            f"OR mentor_id IN ({placeholders})))", ids + ids)
cur.execute(f"DELETE FROM conversations WHERE mentorship_id IN "
            f"(SELECT id FROM mentorship_requests WHERE mentee_id IN ({placeholders}) "
            f"OR mentor_id IN ({placeholders}))", ids + ids)
cur.execute(f"DELETE FROM mentorship_requests WHERE mentee_id IN ({placeholders}) "
            f"OR mentor_id IN ({placeholders})", ids + ids)
cur.execute(f"DELETE FROM mentor_profiles WHERE user_id IN ({placeholders})", ids)
cur.execute(f"DELETE FROM user_progress WHERE user_id IN ({placeholders})", ids)
cur.execute(f"DELETE FROM quiz_results WHERE user_id IN ({placeholders})", ids)
cur.execute(f"DELETE FROM profiles WHERE user_id IN ({placeholders})", ids)
cur.execute(f"DELETE FROM resumes WHERE user_id IN ({placeholders})", ids)
cur.execute(f"DELETE FROM users WHERE id IN ({placeholders})", ids)

connection.commit()

print("\n--- what is left ---")
for table in ("users", "mentor_profiles", "mentorship_requests", "conversations", "messages"):
    count = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"  {table:22} {count} rows")
print("\n  accounts:")
for email, in cur.execute("SELECT email FROM users ORDER BY id").fetchall():
    print("   ", email)
connection.close()
