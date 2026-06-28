"""Compute ranks for any test whose window has closed.

Run on a schedule (Render Cron Job or Supabase pg_cron). It finds tests whose
`ends_at` has passed but ranks aren't computed, and calls the SQL function
compute_test_ranks() for each — keeping ranking off the live request path.
"""
from datetime import datetime, timezone

from backend.supabase_client import supabase


def main() -> None:
    now = datetime.now(timezone.utc).isoformat()
    due = (supabase.table("tests").select("id,title")
           .lt("ends_at", now).neq("ranking_status", "done").execute().data)
    if not due:
        print("No tests due for ranking.")
        return
    for t in due:
        try:
            supabase.rpc("compute_test_ranks", {"p_test_id": t["id"]}).execute()
            print(f"Ranked: {t['title']} ({t['id']})")
        except Exception as e:  # noqa: BLE001
            print(f"Failed for {t['id']}: {e}")


if __name__ == "__main__":
    main()
