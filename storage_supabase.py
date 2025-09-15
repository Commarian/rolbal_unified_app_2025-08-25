from __future__ import annotations

from typing import Any, Dict
from datetime import datetime, timezone

from storage import DEFAULT_STATE
import auth_supabase as auth


class SupabaseStore:
    """Minimal Store-compatible wrapper backed by Supabase Postgres.

    Table schema (create this in Supabase SQL editor):

        create table if not exists public.events (
          user_id uuid primary key,
          state jsonb not null default '{}'::jsonb,
          updated_at timestamptz not null default now()
        );

    RLS policies (with RLS enabled on the table):

        create policy "read own" on public.events
          for select using (auth.uid() = user_id);
        create policy "insert own" on public.events
          for insert with check (auth.uid() = user_id);
        create policy "update own" on public.events
          for update using (auth.uid() = user_id);
    """

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.state: Dict[str, Any] = {}
        self._sb = auth.get_client()
        if self._sb is None:
            raise RuntimeError("Supabase client not configured")
        self.load()

    # ------- compatibility API -------
    def load(self):
        # Try read; if missing create with DEFAULT_STATE
        try:
            res = self._sb.table("events").select("state").eq("user_id", self.user_id).execute()
            data = getattr(res, "data", None) or []
            if data:
                self.state = data[0].get("state") or DEFAULT_STATE.copy()
            else:
                self.state = DEFAULT_STATE.copy()
                self.save()  # create row
        except Exception:
            # Fallback to fresh state if table/policies not yet set
            self.state = DEFAULT_STATE.copy()

    def save(self):
        payload = {
            "user_id": self.user_id,
            "state": self.state,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        # Upsert ensures the record exists
        self._sb.table("events").upsert(payload, on_conflict="user_id").execute()

    def log(self, action: str, payload: Any):
        try:
            self.state.setdefault("audit", []).append({
                "ts": datetime.now(timezone.utc).timestamp(),
                "action": action,
                "payload": payload,
            })
        except Exception:
            pass
        self.save()

    def key_pair(self, section: str, round_no: int) -> str:
        return f"{section}:{round_no}"

    def key_score(self, section: str, round_no: int, rink: int) -> str:
        return f"{section}:{round_no}:{rink}"

