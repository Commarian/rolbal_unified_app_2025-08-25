# storage.py
import json, os, time, hashlib
from typing import Dict, Any

DEFAULT_STATE = {
    "event_name": "SISHEN BORGDAG",
    "sections": ["SEKSIE 1", "SEKSIE 2"],
    "rinks": 7,
    "rounds": 6,
    "players": {},  # id -> {"name": str, "section": str}
    "pairings": {}, # f"{section}:{round}" -> [{rink, a_id, b_id}]
    "scores": {},   # f"{section}:{round}:{rink}" -> {"a": {"vir": int, "teen": int}, "b": {...}}
    "scores_per_end": {},  # f"{section}:{round}:{rink}" -> {"n": int, "ends": [{"a": int, "b": int}, ...]}
    "rules": {
        "POINTS_WIN": 2, "POINTS_DRAW": 1, "POINTS_LOSS": 0,
        "BONUS_ENABLED": False, "BONUS_THRESHOLD": 10, "BONUS_POINTS": 1,
        "TIEBREAKERS": ["Total", "Verskil", "Player#"]
    },
    "ui": {
        "mirror_mode": True
    },
    "locks": {  # f"{section}:{round}" -> True/False
    },
    "audit": []  # list of {ts, action, payload}
}

class Store:
    def __init__(self, path: str):
        self.path = path
        if not os.path.exists(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.state = None
        self.load()

    def load(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self.state = json.load(f)
        else:
            self.state = DEFAULT_STATE.copy()
            self.save()

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
        # Update saved markers for local mode
        try:
            import streamlit as st  # type: ignore
            snap = json.dumps(self.state, sort_keys=True, ensure_ascii=False)
            st.session_state["last_saved_hash"] = hashlib.sha1(snap.encode("utf-8")).hexdigest()
            st.session_state["last_saved_ts"] = time.time()
        except Exception:
            pass

    def log(self, action: str, payload: Any):
        self.state["audit"].append({"ts": time.time(), "action": action, "payload": payload})
        self.save()

    def key_pair(self, section: str, round_no: int):
        return f"{section}:{round_no}"

    def key_score(self, section: str, round_no: int, rink: int):
        return f"{section}:{round_no}:{rink}"
