from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import json, hashlib
import uuid

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

    def __init__(self, user_id: str, event_id: Optional[str] = None):
        self.user_id = user_id
        self.event_id = event_id  # None means legacy single-row mode
        self.state: Dict[str, Any] = {}
        self.updated_at: Optional[str] = None
        self._sb = auth.get_client()
        if self._sb is None:
            raise RuntimeError("Supabase client not configured")
        self.load()

    # ------- compatibility API -------
    def load(self):
        # Try read; if missing create with DEFAULT_STATE
        # Try new multi-event schema first: (user_id, event_id)
        if self.event_id:
            try:
                res = (self._sb.table("events")
                       .select("state,updated_at,name")
                       .eq("user_id", self.user_id)
                       .eq("event_id", self.event_id)
                       .execute())
                data = getattr(res, "data", None) or []
                if data:
                    row = data[0]
                    self.state = row.get("state") or DEFAULT_STATE.copy()
                    self.updated_at = row.get("updated_at")
                    # keep event name in state if present
                    if row.get("name"):
                        self.state["event_name"] = row["name"]
                else:
                    self.state = DEFAULT_STATE.copy()
                    self.save()  # create row
                return
            except Exception:
                # fall through to legacy mode
                pass

        # Legacy single-row mode per user
        try:
            res = self._sb.table("events").select("state,updated_at").eq("user_id", self.user_id).execute()
            data = getattr(res, "data", None) or []
            if data:
                row = data[0]
                self.state = row.get("state") or DEFAULT_STATE.copy()
                self.updated_at = row.get("updated_at")
            else:
                self.state = DEFAULT_STATE.copy()
                self.save()  # create row
        except Exception:
            self.state = DEFAULT_STATE.copy()

    def save(self):
        now_iso = datetime.now(timezone.utc).isoformat()
        if self.event_id:
            name = self.state.get("event_name") or "Event"
            payload = {
                "user_id": self.user_id,
                "event_id": self.event_id,
                "name": name,
                "state": self.state,
                "updated_at": now_iso,
            }
            try:
                self._sb.table("events").upsert(payload, on_conflict="user_id,event_id").execute()
            except Exception:
                # Fallback if composite conflict target unsupported; try no conflict target
                self._sb.table("events").upsert(payload).execute()
        else:
            payload = {
                "user_id": self.user_id,
                "state": self.state,
                "updated_at": now_iso,
            }
            self._sb.table("events").upsert(payload, on_conflict="user_id").execute()
        # Update local metadata + saved markers
        self.updated_at = now_iso
        try:
            import streamlit as st  # type: ignore
            snap = json.dumps(self.state, sort_keys=True, ensure_ascii=False)
            st.session_state["last_saved_hash"] = hashlib.sha1(snap.encode("utf-8")).hexdigest()
            st.session_state["last_saved_ts"] = datetime.now(timezone.utc).timestamp()
        except Exception:
            pass

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

    # ------- multi-event helpers -------
    @classmethod
    def list_events_for(cls, user_id: str) -> List[Dict[str, Any]]:
        sb = auth.get_client()
        if sb is None:
            return []
        # Try multi-event first
        try:
            res = sb.table("events").select("event_id,name,updated_at").eq("user_id", user_id).order("updated_at", desc=True).execute()
            rows = getattr(res, "data", None) or []
            # Filter out legacy rows without event_id
            events = [r for r in rows if r.get("event_id")]
            if events:
                return events
        except Exception:
            pass
        # Legacy: synthesize a single default event if a row exists
        try:
            res = sb.table("events").select("state,updated_at").eq("user_id", user_id).execute()
            rows = getattr(res, "data", None) or []
            if rows:
                name = (rows[0].get("state") or {}).get("event_name") or "Default Event"
                return [{"event_id": None, "name": name, "updated_at": rows[0].get("updated_at")}]  # type: ignore[dict-item]
        except Exception:
            pass
        return []

    @classmethod
    def create_event(cls, user_id: str, name: str) -> str:
        sb = auth.get_client()
        if sb is None:
            raise RuntimeError("Supabase not configured")
        event_id = str(uuid.uuid4())
        state = DEFAULT_STATE.copy()
        state["event_name"] = name
        payload = {
            "user_id": user_id,
            "event_id": event_id,
            "name": name,
            "state": state,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        sb.table("events").upsert(payload).execute()
        return event_id

    @classmethod
    def rename_event(cls, user_id: str, event_id: str, new_name: str) -> None:
        sb = auth.get_client()
        if sb is None:
            return
        sb.table("events").update({"name": new_name, "updated_at": datetime.now(timezone.utc).isoformat()}).match({"user_id": user_id, "event_id": event_id}).execute()

    @classmethod
    def delete_event(cls, user_id: str, event_id: str) -> None:
        sb = auth.get_client()
        if sb is None:
            return
        sb.table("events").delete().match({"user_id": user_id, "event_id": event_id}).execute()

    @classmethod
    def duplicate_event(cls, user_id: str, source_event_id: Optional[str], new_name: str) -> str:
        sb = auth.get_client()
        if sb is None:
            raise RuntimeError("Supabase not configured")
        # Load source
        state = DEFAULT_STATE.copy()
        try:
            if source_event_id:
                res = sb.table("events").select("state").match({"user_id": user_id, "event_id": source_event_id}).execute()
            else:
                res = sb.table("events").select("state").eq("user_id", user_id).execute()
            rows = getattr(res, "data", None) or []
            if rows:
                state = rows[0].get("state") or DEFAULT_STATE.copy()
        except Exception:
            pass
        state = dict(state)
        state["event_name"] = new_name
        new_id = str(uuid.uuid4())
        payload = {
            "user_id": user_id,
            "event_id": new_id,
            "name": new_name,
            "state": state,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        sb.table("events").upsert(payload).execute()
        return new_id
