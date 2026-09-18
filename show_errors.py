"""Print the stored error for recent failed case attempts."""
import sys; sys.path.insert(0, ".")
from studyforge import db
db.init_db()
with db.conn() as c:
    rows = c.execute(
        "SELECT id, status, error, submitted_at FROM case_attempts "
        "WHERE status='failed' ORDER BY submitted_at DESC LIMIT 10").fetchall()
if not rows:
    print("No failed attempts found in this database.")
for r in rows:
    print(f"\n--- attempt {r['id']} ({r['submitted_at']}) ---")
    print(r["error"] or "(no error text stored)")
