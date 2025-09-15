# app.py
import streamlit as st
import streamlit.components.v1 as components
from storage import Store
from storage_supabase import SupabaseStore
from engine import (
    PlayerStanding, compute_standings, round_result,
    round_robin_pairs, build_history,
    strong_vs_strong_pairs, assign_rinks_with_preferences, last_rink_map, sort_standings
)
from config import EVENT_NAME, DEFAULT_RINKS, DEFAULT_ROUNDS, DEFAULT_SECTIONS
import os
import auth_supabase as auth
import csv, io, datetime as dt, random, re
import json, hashlib, time
import pandas as pd
try:
    import openpyxl  # needed by pandas for .xlsx/.xlsm
except ImportError:
    st.error("Missing dependency: openpyxl. Install with:  pip install openpyxl")
    st.stop()

st.set_page_config(page_title="Rolbal Unified", layout="wide")

# Capture Supabase recovery hash (#access_token=...&type=recovery) into query string
components.html(
    """
    <script>
    (function(){
      try {
        var h = window.location.hash;
        var s = window.location.search;
        if (h && h.indexOf('type=recovery') !== -1 && (!s || s.indexOf('type=recovery') === -1)){
          var hp = new URLSearchParams(h.substring(1));
          var sp = new URLSearchParams(window.location.search);
          ['access_token','refresh_token','type'].forEach(function(k){ var v = hp.get(k); if(v){ sp.set(k,v);} });
          var url = window.location.pathname + '?' + sp.toString();
          window.location.replace(url);
        }
      } catch(e) {}
    })();
    </script>
    """,
    height=0,
)
def render_rink_score(section: str, round_no: int, rink: int, pr: dict, store, mirror_on: bool):
    """Compact row renderer for a single rink."""
    a_id = pr.get("a_id"); b_id = pr.get("b_id")
    a = store.state["players"].get(str(a_id), {}) if a_id else {}
    b = store.state["players"].get(str(b_id), {}) if b_id else {}
    a_name = a.get("name",""); b_name = b.get("name","")

    sk = store.key_score(section, int(round_no), int(rink))
    sc = store.state["scores"].get(sk, {"a": {"vir": 0, "teen": 0}, "b": {"vir": 0, "teen": 0}})
    # Keys are already namespaced by section/round/rink (safe to re-use)
    key_va = f"score_va_{section}_{round_no}_{rink}"
    key_ta = f"score_ta_{section}_{round_no}_{rink}"
    key_vb = f"score_vb_{section}_{round_no}_{rink}"
    key_tb = f"score_tb_{section}_{round_no}_{rink}"

    # Inputs (A always editable)
    va = st.number_input("", min_value=0, value=int(sc["a"]["vir"]), step=1, key=key_va, label_visibility="collapsed")
    ta = st.number_input("", min_value=0, value=int(sc["a"]["teen"]), step=1, key=key_ta, label_visibility="collapsed")

    if mirror_on:
        vb, tb = ta, va
        vb_view = f"{vb}"
        tb_view = f"{tb}"
    else:
        vb = st.number_input("", min_value=0, value=int(sc["b"]["vir"]), step=1, key=key_vb, label_visibility="collapsed")
        tb = st.number_input("", min_value=0, value=int(sc["b"]["teen"]), step=1, key=key_tb, label_visibility="collapsed")
        vb_view = None; tb_view = None

    sec_color = SECTION_COLORS.get(section, "#a78bfa")
    # Row grid
    c = st.columns([1e-6])  # placeholder (Streamlit needs at least one column call)
    st.markdown(
        f"""
        <div class="score-row">
          <div><span class="rink-chip" style="color:{sec_color};border-color:{sec_color}44">{rink}</span></div>
          <div class="name">A&nbsp;#{a_id or '—'} {a_name or ''}</div>
          <div class="name">B&nbsp;#{b_id or '—'} {b_name or ''}</div>
          <div>{st.session_state[key_va]}</div>
          <div>{st.session_state[key_ta]}</div>
          <div>{vb_view if mirror_on else ''}</div>
          <div>{tb_view if mirror_on else ''}</div>
          <div class="smallbtn">
            {''}
          </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # Put the actual editable widgets after the row so layout remains tight
    col_va, col_ta, col_vb, col_tb, col_btn = st.columns([0.001,0.001,0.001,0.001,0.001])
    with col_btn:
        if st.button(f"Save", key=f"save_rink_{section}_{round_no}_{rink}"):
            store.state["scores"][sk] = {
                "a": {"vir": int(st.session_state[key_va]), "teen": int(st.session_state[key_ta])},
                "b": {"vir": int(vb if mirror_on else st.session_state.get(key_vb, 0)),
                      "teen": int(tb if mirror_on else st.session_state.get(key_tb, 0))},
            }
            store.save()
            st.success(f"Saved Rink {rink}")


def render_rink_score_compact(section: str, round_no: int, rink: int, pr: dict, store, mirror_on: bool):
    """New compact row renderer using Streamlit columns only.

    Merges separate A/B name columns into a single compact "Teams" column
    to reduce visual noise.
    """
    a_id = pr.get("a_id"); b_id = pr.get("b_id")
    a = store.state["players"].get(str(a_id), {}) if a_id else {}
    b = store.state["players"].get(str(b_id), {}) if b_id else {}
    a_name = a.get("name", ""); b_name = b.get("name", "")

    sk = store.key_score(section, int(round_no), int(rink))
    sc = store.state.get("scores", {}).get(sk, {"a": {"vir": 0, "teen": 0}, "b": {"vir": 0, "teen": 0}})

    key_va = f"score_va_{section}_{round_no}_{rink}"
    key_ta = f"score_ta_{section}_{round_no}_{rink}"
    key_vb = f"score_vb_{section}_{round_no}_{rink}"
    key_tb = f"score_tb_{section}_{round_no}_{rink}"

    sec_color = SECTION_COLORS.get(section, "#a78bfa")
    # Rink | Teams | A | A | B | B | Save (headers below clarify Vir/Teen)
    c_rk, c_team, c_av, c_at, c_bv, c_bt, c_btn = st.columns([0.7, 2.8, 0.8, 0.8, 0.8, 0.8, 0.8])

    with c_rk:
        st.markdown(f'<span class="rink-chip" style="color:{sec_color};border-color:{sec_color}44">{rink}</span>', unsafe_allow_html=True)
    with c_team:
        a_txt = f"#{a_id or '—'} {a_name or ''}".strip()
        b_txt = f"#{b_id or '—'} {b_name or ''}".strip()
        st.markdown(
            f"<span class='ab-chip'>A</span><span class='score-name'>{a_txt}</span>"
            f" <span class='vs'>vs</span> "
            f"<span class='ab-chip'>B</span><span class='score-name'>{b_txt}</span>",
            unsafe_allow_html=True,
        )

    # Show the A player's name above inputs for clarity
    with c_av:
        if a_name:
            st.caption(f"{a_name}")
        va = st.number_input(
            "A Vir",
            min_value=0,
            value=int(sc["a"]["vir"]),
            step=1,
            key=key_va,
            label_visibility="collapsed",
            help=f"Vir = points for {a_name or 'A'}"
        )
    with c_at:
        if a_name:
            st.caption(f"{a_name}")
        ta = st.number_input(
            "A Teen",
            min_value=0,
            value=int(sc["a"]["teen"]),
            step=1,
            key=key_ta,
            label_visibility="collapsed",
            help=f"Teen = points against {a_name or 'A'}"
        )

    if mirror_on:
        # Mirror B from A, but still label with the B player's name
        vb, tb = ta, va
        with c_bv:
            if b_name:
                st.caption(f"{b_name}")
            st.markdown(f"<div class='readout vir'>{vb}</div>", unsafe_allow_html=True)
        with c_bt:
            if b_name:
                st.caption(f"{b_name}")
            st.markdown(f"<div class='readout teen'>{tb}</div>", unsafe_allow_html=True)
    else:
        with c_bv:
            if b_name:
                st.caption(f"{b_name}")
            vb = st.number_input(
                "B Vir",
                min_value=0,
                value=int(sc["b"]["vir"]),
                step=1,
                key=key_vb,
                label_visibility="collapsed",
                help=f"Vir = points for {b_name or 'B'}"
            )
        with c_bt:
            if b_name:
                st.caption(f"{b_name}")
            tb = st.number_input(
                "B Teen",
                min_value=0,
                value=int(sc["b"]["teen"]),
                step=1,
                key=key_tb,
                label_visibility="collapsed",
                help=f"Teen = points against {b_name or 'B'}"
            )

    with c_btn:
        if st.button(f"Save", key=f"save_rink_{section}_{round_no}_{rink}"):
            store.state.setdefault("scores", {})[sk] = {
                "a": {"vir": int(va), "teen": int(ta)},
                "b": {"vir": int(vb), "teen": int(tb)},
            }
            store.save()
            st.success(f"Saved Rink {rink}")


def _section_player_options(store, section):
    """
    Returns a list like [(None, "—"), (1, "1 — Alice"), (2, "2 — Bob"), ...]
    filtered to the selected section.
    """
    rows = [(int(k), v["name"]) for k, v in store.state["players"].items() if v["section"] == section]
    rows.sort(key=lambda x: x[0])
    return [(None, "—")] + [(pid, f"{pid} — {name}") for pid, name in rows]


ENTER_SCORES_CSS = """
<style>
/* Compact, row-like layout for Score entry */
.score-hdr, .score-row {
  display: grid;
  grid-template-columns: 64px 1.4fr 1.4fr 0.7fr 0.7fr 0.7fr 0.7fr 80px;
  gap: 10px;
  align-items: center;
}
.score-hdr { opacity:.85; font-weight:700; margin-top:6px; margin-bottom:4px; }
.score-row { padding:6px 8px; border-bottom:1px dashed rgba(255,255,255,.08); }
.rink-chip {
  font-weight:700; font-size:.9rem; padding:2px 10px; border-radius:999px;
  border:1px solid rgba(255,255,255,.18); display:inline-block; min-width:40px; text-align:center;
}
.name { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; opacity:.95; }
.readout { opacity:.85; }
.smallbtn .stButton>button { padding:6px 10px; }
.stNumberInput>div>div>input { height:30px; padding:0 8px; } /* shrink inputs */
</style>
"""

st.markdown(ENTER_SCORES_CSS, unsafe_allow_html=True)
ENTER_SCORES_CSS_COMPACT = """
<style>
/* Slimmer controls specifically for Scores tab (overrides) */
.stNumberInput>div>div>input { height:26px; padding:0 6px; }
.stSelectbox [data-baseweb="select"] > div { min-height:26px; }
.stButton>button { height:26px; padding:2px 8px; font-size:12px; }
.rink-chip { font-weight:700; font-size:.85rem; padding:2px 8px; border-radius:999px; border:1px solid rgba(255,255,255,.18); display:inline-block; min-width:36px; text-align:center; }
.score-name { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.ab-chip { font-weight:700; font-size:10px; padding:1px 6px; border-radius:999px; border:1px solid rgba(255,255,255,.18); opacity:.8; margin-right:6px; }
.vs { opacity:.6; margin:0 6px; }

/* Vir/Teen tinting */
:root {
  --vir-fg: #22c55e;
  --vir-bg: rgba(34,197,94,.14);
  --teen-fg: #f59e0b;
  --teen-bg: rgba(245,158,11,.14);
  --scores-radius: 6px;
}
.col.vir { color: var(--vir-fg); font-weight:700; }
.col.teen { color: var(--teen-fg); font-weight:700; }

.stNumberInput input[aria-label="A Vir"],
.stNumberInput input[aria-label="B Vir"] {
  background: var(--vir-bg) !important;
  border-color: rgba(34,197,94,.35) !important;
}
.stNumberInput input[aria-label="A Teen"],
.stNumberInput input[aria-label="B Teen"] {
  background: var(--teen-bg) !important;
  border-color: rgba(245,158,11,.35) !important;
}

/* Tint the mirror readouts too */
.readout { padding:6px 10px; border-radius: var(--scores-radius); display:flex; align-items:center; min-height:26px; }
.readout.vir { background: var(--vir-bg); color: var(--vir-fg); }
.readout.teen { background: var(--teen-bg); color: var(--teen-fg); }
</style>
"""
st.markdown(ENTER_SCORES_CSS_COMPACT, unsafe_allow_html=True)
# ---- Authentication (Supabase) & per-user data path ----
SUPABASE_CONFIGURED = auth.get_client() is not None
REQUIRE_AUTH = True
try:
    # Allow override via secrets or env for local development
    REQUIRE_AUTH = bool(int(os.getenv("REQUIRE_AUTH", "1")))
    if "auth" in st.secrets:
        require_val = st.secrets["auth"].get("require")
        if require_val is not None:
            REQUIRE_AUTH = bool(require_val)
except Exception:
    pass

def _render_auth_gate():
    """Render login/register UI if Supabase is configured.

    Sets st.session_state["auth_user"] to a dict with at least {"id","email"}
    when authenticated. Stops the app flow until the user signs in.
    """
    if not SUPABASE_CONFIGURED:
        # If auth is required but client is missing, block access with guidance
        if REQUIRE_AUTH:
            st.error("Authentication required but Supabase is not configured. Add secrets or env keys.")
            st.stop()
        # Otherwise allow guest mode
        return

    # Already signed in
    if st.session_state.get("auth_user"):
        return

    # Password recovery flow
    try:
        q = dict(st.query_params)
    except Exception:
        try:
            q = st.experimental_get_query_params()
        except Exception:
            q = {}
    if str(q.get("type", "")) == "recovery" and q.get("access_token"):
        st.markdown("## Reset your password")
        with st.form("pw_reset_form", clear_on_submit=False):
            npw = st.text_input("New password", type="password", key="pw_new")
            npw2 = st.text_input("Confirm password", type="password", key="pw_new2")
            submit_pw = st.form_submit_button("Update password")
        if submit_pw:
            if not npw or npw != npw2:
                st.error("Passwords do not match or empty")
            else:
                try:
                    sess = auth.apply_recovery(str(q.get("access_token")), str(q.get("refresh_token", "")), npw)
                    st.session_state["auth_user"] = {"id": sess.user.get("id"), "email": sess.user.get("email")}
                    try:
                        st.experimental_set_query_params()  # clear tokens from URL
                    except Exception:
                        pass
                    st.success("Password updated. Signed in.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not update password: {e}")
        st.stop()

    st.markdown("## Sign in to continue")
    tabs = st.tabs(["Sign In", "Sign Up"])

    with tabs[0]:
        with st.form("login_form", clear_on_submit=False):
            email = st.text_input("Email", key="auth_email")
            password = st.text_input("Password", type="password", key="auth_password")
            submitted = st.form_submit_button("Sign In")
        if submitted:
            try:
                sess = auth.sign_in(email.strip(), password)
                st.session_state["auth_user"] = {"id": sess.user.get("id"), "email": sess.user.get("email")}
                st.success("Signed in")
                st.rerun()
            except Exception as e:
                st.error(f"Login failed: {e}")
        # Forgot password
        if st.button("Forgot password? Email reset link", key="pw_forgot"):
            if not email:
                st.error("Enter your email above first")
            else:
                try:
                    auth.send_password_reset(email.strip())
                    st.success("Password reset email sent. Check your inbox.")
                except Exception as e:
                    st.error(f"Could not send reset email: {e}")

    with tabs[1]:
        with st.form("signup_form", clear_on_submit=False):
            email2 = st.text_input("Email", key="auth_email2")
            pw2 = st.text_input("Password", type="password", key="auth_password2")
            submitted2 = st.form_submit_button("Create Account")
        if submitted2:
            try:
                sess = auth.sign_up(email2.strip(), pw2)
                st.info("Check your email to confirm your account if required. Then sign in.")
            except Exception as e:
                st.error(f"Sign up failed: {e}")

    # Always stop until user signs in
    st.stop()


_render_auth_gate()

# Compute data path / event selection when auth is enabled
_user = st.session_state.get("auth_user") if SUPABASE_CONFIGURED else None
_event_id = None
if SUPABASE_CONFIGURED and _user and _user.get("id"):
    # Multi-event selection UI (before opening the store)
    with st.sidebar:
        st.markdown("### Event")
        events = SupabaseStore.list_events_for(_user["id"]) or []
        # Resolve current selection
        if "current_event_id" not in st.session_state:
            st.session_state["current_event_id"] = (events[0]["event_id"] if events else None)

        # Build radio list with clean labels (no input field)
        label_to_id = {}
        labels = []
        for e in events:
            eid = e.get("event_id") or None
            nm = (e.get("name") or "Event").strip() or "Event"
            label = nm if eid else "Default Event"
            # ensure uniqueness if duplicate names
            base = label; k = 2
            while label in label_to_id:
                label = f"{base} ({k})"; k += 1
            label_to_id[label] = eid
            labels.append(label)
        if not labels:
            label_to_id = {"Default Event": None}
            labels = ["Default Event"]

        # Determine current label from stored event_id
        current_id = st.session_state.get("current_event_id")
        try:
            cur_label = next(l for l, v in label_to_id.items() if v == current_id)
        except StopIteration:
            cur_label = labels[0]
        idx = max(0, labels.index(cur_label)) if labels else 0

        sel_label = st.radio("Select event", options=labels, index=idx, key="sb_event_select")
        _event_id = label_to_id.get(sel_label)
        if _event_id != current_id:
            st.session_state["current_event_id"] = _event_id
            st.rerun()

        # Actions as toggled forms to avoid sticky inputs
        c1, c2, c3, c4 = st.columns(4)
        if c1.button("New", key="ev_new_btn"):
            st.session_state["ev_show_create"] = True
        if c2.button("Rename", key="ev_rename_btn", disabled=_event_id is None):
            st.session_state["ev_show_rename"] = True
        if c3.button("Duplicate", key="ev_dup_btn"):
            st.session_state["ev_show_dup"] = True
        if c4.button("Delete", key="ev_del_btn", disabled=_event_id is None):
            st.session_state["ev_show_del"] = True

        # Create form
        if st.session_state.get("ev_show_create"):
            with st.form("ev_create_form", clear_on_submit=False):
                nm = st.text_input("Event name", key="ev_create_name")
                cc1, cc2 = st.columns([1,1])
                create_ok = cc1.form_submit_button("Create")
                cancel = cc2.form_submit_button("Cancel")
            if cancel:
                st.session_state["ev_show_create"] = False
                st.rerun()
            if create_ok and nm.strip():
                try:
                    new_id = SupabaseStore.create_event(_user["id"], nm.strip())
                    st.session_state["ev_show_create"] = False
                    st.session_state["current_event_id"] = new_id
                    st.success("Event created")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not create: {e}")

        # Rename form
        if st.session_state.get("ev_show_rename") and _event_id:
            with st.form("ev_rename_form", clear_on_submit=False):
                nm = st.text_input("New name", key="ev_rename_name")
                rc1, rc2 = st.columns([1,1])
                apply = rc1.form_submit_button("Apply rename")
                cancel = rc2.form_submit_button("Cancel")
            if cancel:
                st.session_state["ev_show_rename"] = False
                st.rerun()
            if apply and nm.strip():
                try:
                    SupabaseStore.rename_event(_user["id"], _event_id, nm.strip())
                    st.session_state["ev_show_rename"] = False
                    st.success("Renamed")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not rename: {e}")

        # Duplicate form
        if st.session_state.get("ev_show_dup"):
            with st.form("ev_dup_form", clear_on_submit=False):
                nm = st.text_input("Duplicate as", key="ev_dup_name")
                dc1, dc2 = st.columns([1,1])
                do_dup = dc1.form_submit_button("Duplicate")
                cancel = dc2.form_submit_button("Cancel")
            if cancel:
                st.session_state["ev_show_dup"] = False
                st.rerun()
            if do_dup and nm.strip():
                try:
                    src = _event_id
                    new_id = SupabaseStore.duplicate_event(_user["id"], src, nm.strip())
                    st.session_state["ev_show_dup"] = False
                    st.session_state["current_event_id"] = new_id
                    st.success("Duplicated")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not duplicate: {e}")

        # Delete confirm
        if st.session_state.get("ev_show_del") and _event_id:
            with st.form("ev_del_form", clear_on_submit=False):
                st.warning("This will permanently delete the selected event.")
                confirm = st.text_input("Type DELETE to confirm", key="ev_del_confirm")
                del_ok = st.form_submit_button("Delete permanently")
            if del_ok and confirm == "DELETE":
                try:
                    SupabaseStore.delete_event(_user["id"], _event_id)
                    st.session_state["ev_show_del"] = False
                    st.session_state["current_event_id"] = None
                    st.success("Deleted")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not delete: {e}")
            elif del_ok:
                st.error("Confirmation text mismatch.")

        st.markdown("---")
        auto = st.checkbox("Auto-refresh every 5s", value=False, key="sb_auto")
        if auto:
            components.html("""
                <script>
                setTimeout(function(){ try { window.location.reload(); } catch(e){} }, 5000);
                </script>
            """, height=0)

    # Initialize cloud store for the selected event
    store = SupabaseStore(_user["id"], _event_id)
else:
    DATA_PATH = "data/event.json"
    store = Store(DATA_PATH)

# Optional auth diagnostics (only when debug=1 in URL)
try:
    qp = dict(st.query_params)  # 1.36+
except Exception:
    try:
        qp = st.experimental_get_query_params()
    except Exception:
        qp = {}
want_debug = str(qp.get("debug", "0")).lower() in ("1","true","yes")
if want_debug:
    diag = auth.diagnose_config()
    with st.sidebar:
        st.caption("Auth diagnostics (safe):")
        st.write({k: v for k, v in diag.items()})

# Background autosave: if state changed, persist to cloud/local every ~8s
def _autosave(store_obj, throttle_sec: int = 8):
    try:
        snapshot = json.dumps(store_obj.state, sort_keys=True, ensure_ascii=False)
        h = hashlib.sha1(snapshot.encode("utf-8")).hexdigest()
        last_h = st.session_state.get("_autosave_hash")
        last_t = float(st.session_state.get("_autosave_ts", 0.0))
        now = time.time()
        if h != last_h and (now - last_t) > throttle_sec:
            store_obj.save()
            st.session_state["_autosave_hash"] = h
            st.session_state["_autosave_ts"] = now
    except Exception:
        # autosave is best-effort; never crash UI
        pass

_autosave(store)

# ---- Styles for Schedule tab ----
SECTION_COLORS = {
    "SEKSIE 1": "#2d7dff",
    "SEKSIE 2": "#22c55e",
    # add more if you have more sections
}

SCHEDULE_CSS = """
<style>
.schedule-wrap {border:1px solid rgba(255,255,255,.08); border-radius:14px; padding:10px 12px; margin-top:8px;}
.section-banner {display:flex; align-items:center; gap:10px; margin-bottom:8px;}
.section-dot {width:10px; height:10px; border-radius:999px; display:inline-block;}
.section-title {font-weight:700; letter-spacing:.2px;}
.rink-pill {font-weight:700; font-size:.9rem; padding:4px 10px; border-radius:999px; border:1px solid rgba(255,255,255,.18); display:inline-block; min-width:40px; text-align:center;}
.rink-row {padding:8px 0; border-top:1px dashed rgba(255,255,255,.10);}
.rink-row:first-of-type {border-top:none;}
</style>
"""

st.markdown(SCHEDULE_CSS, unsafe_allow_html=True)

# ---- Styles for Players tab ----
PLAYERS_CSS = """
<style>
.section-legend {display:flex; gap:8px; align-items:center; margin:6px 0 12px;}
.pill {display:inline-block; padding:2px 10px; border-radius:999px; font-weight:700; border:1px solid rgba(255,255,255,.18);}
.row-card {border-bottom:1px dashed rgba(255,255,255,.08); padding:6px 0;}
.help-dim {opacity:.85;}
.warn {color:#ef4444;}
</style>
"""

st.markdown(PLAYERS_CSS, unsafe_allow_html=True)

# -------- Sidebar Global Settings --------
if SUPABASE_CONFIGURED:
    if _user:
        with st.sidebar:
            st.caption(f"Signed in as {_user.get('email')}")
            if st.button("Sign out", key="sb_signout"):
                try:
                    auth.sign_out()
                except Exception:
                    pass
                st.session_state.pop("auth_user", None)
                st.rerun()
    else:
        with st.sidebar:
            st.info("Guest mode (auth not active)")
st.sidebar.title("⚙️ Event Settings")
event_name = st.sidebar.text_input("Event name", store.state.get("event_name", EVENT_NAME), key="sb_event")
rinks = st.sidebar.number_input("Rinks", 1, 20, int(store.state.get("rinks", DEFAULT_RINKS)), 1, key="sb_rinks")
rounds = st.sidebar.number_input("Rounds", 1, 30, int(store.state.get("rounds", DEFAULT_ROUNDS)), 1, key="sb_rounds")
sections = st.sidebar.multiselect("Sections", options=store.state.get("sections", DEFAULT_SECTIONS), default=store.state.get("sections", DEFAULT_SECTIONS), key="sb_sections")
mirror_mode = st.sidebar.checkbox("Mirror B from A in score entry", value=store.state.get("ui", {}).get("mirror_mode", True), key="sb_mirror")
if st.sidebar.button("Save settings", key="sb_save"):
    store.state["event_name"] = event_name
    store.state["rinks"] = int(rinks)
    store.state["rounds"] = int(rounds)
    store.state["sections"] = sections
    store.state["ui"]["mirror_mode"] = bool(mirror_mode)
    store.save()
    st.sidebar.success("Saved settings")

st.title(f"{event_name} — Unified App")

# Global on-leave warning if there are unsaved pairings changes
components.html(
    f"""
    <script>
    (function(){{
      var dirty = {str(bool(st.session_state.get('pairings_dirty_any', False))).lower()};
      var handler = function(e){{ if(!dirty) return; e.preventDefault(); e.returnValue=''; return ''; }};
      try {{ window.top.onbeforeunload = handler; }} catch(err) {{}}
      window.onbeforeunload = handler;
    }})();
    </script>
    """,
    height=0,
)

STATUS_CSS = """
<style>
.status-chip { display:inline-flex; align-items:center; gap:8px; padding:4px 10px; border-radius:999px; font-weight:700; font-size:12px; border:1px solid rgba(255,255,255,.18); }
.ok { color:#22c55e; border-color: rgba(34,197,94,.35); }
.warn { color:#f59e0b; border-color: rgba(245,158,11,.35); }
.err { color:#ef4444; border-color: rgba(239,68,68,.35); }
</style>
"""
st.markdown(STATUS_CSS, unsafe_allow_html=True)

def _render_saved_status():
    try:
        snap = json.dumps(store.state, sort_keys=True, ensure_ascii=False)
        cur_h = hashlib.sha1(snap.encode("utf-8")).hexdigest()
    except Exception:
        cur_h = None
    last_h = st.session_state.get("last_saved_hash")
    last_ts = st.session_state.get("last_saved_ts")
    saved = (cur_h is not None and last_h == cur_h)
    if st.session_state.get("pairings_dirty_any"):
        saved = False
    cls = "ok" if saved else "err"
    label = "Saved" if saved else "Unsaved"
    when = None
    if last_ts:
        try:
            when = dt.datetime.fromtimestamp(float(last_ts)).strftime("%H:%M:%S")
        except Exception:
            when = None
    txt = f"<span class='status-chip {cls}'>{label}{' · '+when if when else ''}</span>"
    st.markdown(txt, unsafe_allow_html=True)

hdr_l, hdr_r = st.columns([0.75, 0.25])
with hdr_l:
    st.title(f"{event_name} · Unified App")
with hdr_r:
    _render_saved_status()

tab_rules, tab_players, tab_schedule, tab_scores, tab_perend, tab_standings, tab_lb, tab_io, tab_tools = st.tabs([
    "Rules", "Players", "Schedule", "Enter Scores", "Per-end", "Standings", "Leaderboard", "Import/Export", "Tools"
])

# -------- Rules --------
with tab_rules:
    st.subheader("Scoring Rules & Tiebreakers")
    if st.session_state.get("pairings_dirty_any"):
        st.warning("Unsaved pairings detected in Schedule. Save or clear them before leaving.")
    rules = store.state.get("rules", {})
    c1,c2,c3 = st.columns(3)
    with c1:
        rules["POINTS_WIN"] = st.number_input("Points: Win", 0, 10, int(rules.get("POINTS_WIN", 2)), key="rl_win")
    with c2:
        rules["POINTS_DRAW"] = st.number_input("Points: Draw", 0, 10, int(rules.get("POINTS_DRAW", 1)), key="rl_draw")
    with c3:
        rules["POINTS_LOSS"] = st.number_input("Points: Loss", 0, 10, int(rules.get("POINTS_LOSS", 0)), key="rl_loss")
    st.markdown("---")
    c1,c2,c3 = st.columns(3)
    with c1:
        rules["BONUS_ENABLED"] = st.checkbox("Enable bonus points on big win", value=bool(rules.get("BONUS_ENABLED", False)), key="rl_bonus_en")
    with c2:
        rules["BONUS_THRESHOLD"] = st.number_input("Bonus threshold (|Verskil| ≥)", 1, 100, int(rules.get("BONUS_THRESHOLD", 10)), key="rl_thr")
    with c3:
        rules["BONUS_POINTS"] = st.number_input("Bonus points", 0, 10, int(rules.get("BONUS_POINTS", 1)), key="rl_bpts")
    st.markdown("---")
    rules["ENDS_PER_GAME"] = st.number_input("Ends per game", 1, 30, int(rules.get("ENDS_PER_GAME", 18)), key="rl_ends")
    st.markdown("---")
    st.caption("Tiebreakers (choose up to 3; leave later ones as '— None —')")
    TB_OPTIONS = ["— None —","Total","Punte","Bonus","Verskil","Player#","Coinflip","Skips draw to Jack"]
    cur = rules.get("TIEBREAKERS", ["Total","Verskil","Player#"]) + ["— None —","— None —","— None —"]

    tb1 = st.selectbox("1st", TB_OPTIONS, index=TB_OPTIONS.index(cur[0] if cur[0] in TB_OPTIONS else "Total"), key="tb1")
    tb2 = st.selectbox("2nd", TB_OPTIONS, index=TB_OPTIONS.index(cur[1] if cur[1] in TB_OPTIONS else "Verskil"), key="tb2")
    tb3 = st.selectbox("3rd", TB_OPTIONS, index=TB_OPTIONS.index(cur[2] if cur[2] in TB_OPTIONS else "Player#"), key="tb3")

    chosen = [x for x in (tb1, tb2, tb3) if x != "— None —"]
    rules["TIEBREAKERS"] = chosen if chosen else ["Player#"]  # always keep deterministic fallback
    if st.button("Save rules", key="rl_save"):
        store.state["rules"] = rules
        store.save()
        st.success("Rules saved.")

# -------- Players --------
with tab_players:
    st.subheader("Players")
    if st.session_state.get("pairings_dirty_any"):
        st.warning("Unsaved pairings detected in Schedule. Save or clear them before leaving.")

    # Color legend (matches Schedule/other tabs)
    legend_html = '<div class="section-legend">'
    for s in store.state.get("sections", DEFAULT_SECTIONS):
        col = SECTION_COLORS.get(s, "#a78bfa")
        legend_html += f'<span class="pill" style="color:{col};border-color:{col}44">{s}</span>'
    legend_html += '</div>'
    st.markdown(legend_html, unsafe_allow_html=True)

    # ---- Add / Edit (dynamic) ----
    st.markdown("### Add / Edit")
    c = st.columns([1,3,2,1])
    with c[0]:
        pid = st.number_input("Player #", 1, 9999, int(st.session_state.get("pl_id", 1)), key="pl_id")

    # Detect existing player for this number
    players_dict = store.state.get("players", {})
    existing = players_dict.get(str(int(pid)))

    # When the PID changes, sync defaults for name/section so it autofills on edit
    if st.session_state.get("pl_last_pid") != int(pid):
        st.session_state["pl_last_pid"] = int(pid)
        if existing:
            st.session_state["pl_name"] = existing.get("name", "")
            st.session_state["pl_section"] = existing.get("section", (store.state.get("sections", DEFAULT_SECTIONS) or DEFAULT_SECTIONS)[0])
        else:
            st.session_state["pl_name"] = ""
            st.session_state["pl_section"] = (store.state.get("sections", DEFAULT_SECTIONS) or DEFAULT_SECTIONS)[0]

    with c[1]:
        if "pl_name" not in st.session_state:
            st.session_state["pl_name"] = ""
        pname = st.text_input("Player name", key="pl_name")

    with c[2]:
        secs = store.state.get("sections", DEFAULT_SECTIONS) or DEFAULT_SECTIONS
        if "pl_section" not in st.session_state or st.session_state["pl_section"] not in secs:
            st.session_state["pl_section"] = secs[0]
        psection = st.selectbox("Section", options=secs, key="pl_section")
        # colored cue
        _col = SECTION_COLORS.get(psection, "#a78bfa")
        st.markdown(f'<span class="pill" style="color:{_col};border-color:{_col}44">Editing: {psection}</span>', unsafe_allow_html=True)

    with c[3]:
        btn_label = "Edit" if existing else "Add"
        if st.button(btn_label, key="pl_save"):
            if pname.strip():
                store.state["players"][str(int(pid))] = {"name": pname.strip(), "section": psection}
                store.save()
                st.success(f'{"Updated" if existing else "Added"} {pid} — {pname} ({psection})')

    st.markdown("---")

    # ---- Remove (explicit + obvious) ----
    st.markdown("### Remove player")
    st.caption("Pick a player and confirm by typing their number to avoid mistakes.")

    # session flag for pending force-remove
    if "pl_pending_remove" not in st.session_state:
        st.session_state["pl_pending_remove"] = None

    # Build options like: (pid, "12 — Alice (SEKSIE 1)")
    all_rows = [(int(k), f'{int(k)} — {v["name"]} ({v["section"]})', v["section"])
                for k, v in store.state.get("players", {}).items()]
    all_rows.sort(key=lambda x: x[0])
    opt_labels = ["—"] + [r[1] for r in all_rows]
    opt_values = [None] + [r[0] for r in all_rows]

    rc1, rc2, rc3 = st.columns([4,2,2])
    with rc1:
        sel_idx = st.selectbox("Player to remove", options=list(range(len(opt_labels))),
                               format_func=lambda i: opt_labels[i], key="pl_remove_idx")
        sel_pid = opt_values[sel_idx]
    with rc2:
        confirm_text = st.text_input("Type player # to confirm", key="pl_remove_confirm", placeholder="e.g. 12")
    with rc3:
        if st.button("🗑 Remove", key="pl_remove_btn", disabled=(sel_pid is None)):
            if str(confirm_text).strip() != ("" if sel_pid is None else str(sel_pid)):
                st.error("Confirmation number does not match the selected player.")
            else:
                # Safety: check usage in pairings first
                used = 0
                where = []
                for key, prs in store.state.get("pairings", {}).items():
                    for pr in prs:
                        if pr.get("a_id") == sel_pid or pr.get("b_id") == sel_pid:
                            used += 1
                            where.append((key, pr.get("rink")))
                if used > 0:
                    # stage force-remove in session so the confirm button works on its own rerun
                    st.session_state["pl_pending_remove"] = {"pid": sel_pid, "used": used, "where": where}
                    st.warning(f"Player #{sel_pid} appears in {used} pairing(s). Review below, then confirm force remove.")
                else:
                    # Simple remove
                    store.state["players"].pop(str(sel_pid), None)
                    store.log("remove_player", {"player_id": sel_pid})
                    store.save()
                    st.success(f"Removed player #{sel_pid}.")

    # If a force-remove is pending, show details and a persistent confirm button
    pending = st.session_state.get("pl_pending_remove")
    if pending is not None:
        with st.expander(f"Player #{pending['pid']} appears in {pending['used']} pairing(s). Click to view."):
            st.table([{"Round Key": k, "Rink": r} for k, r in pending["where"]])
        cfr1, cfr2 = st.columns([1,5])
        with cfr1:
            if st.button("Force remove now", key="pl_force_remove_confirm"):
                pid_fr = int(pending["pid"])
                # 1) remove from players
                store.state["players"].pop(str(pid_fr), None)
                # 2) clear from pairings
                for key, prs in store.state.get("pairings", {}).items():
                    for pr in prs:
                        if pr.get("a_id") == pid_fr:
                            pr["a_id"] = None
                        if pr.get("b_id") == pid_fr:
                            pr["b_id"] = None
                store.log("remove_player_forced", {"player_id": pid_fr, "affected_pairings": pending["used"]})
                store.save()
                st.session_state["pl_pending_remove"] = None
                st.success(f"Removed player #{pid_fr} and cleared them from {pending['used']} pairing(s).")
        with cfr2:
            if st.button("Cancel", key="pl_force_remove_cancel"):
                st.session_state["pl_pending_remove"] = None


    st.markdown("---")

    # Filter & list (colored sections)
    st.markdown("### Current players")
    vf1, vf2 = st.columns([2,6])
    with vf1:
        filter_sec = st.selectbox("Filter by section", options=["All"] + list(store.state.get("sections", DEFAULT_SECTIONS)), key="pl_filter_sec")
    rows = [{"#": int(k), "Name": v["name"], "Section": v["section"]}
            for k, v in store.state.get("players", {}).items()]
    rows.sort(key=lambda x: x["#"])
    if filter_sec != "All":
        rows = [r for r in rows if r["Section"] == filter_sec]

    # Render as a clean table; color chip via a tiny HTML column for clarity
    if rows:
        # Make a small HTML chip column for Section colors
        def chip_html(sec_name: str) -> str:
            c = SECTION_COLORS.get(sec_name, "#a78bfa")
            return f'<span class="pill" style="color:{c};border-color:{c}44">{sec_name}</span>'

        # Build markdown table with chips (st.dataframe won't render HTML)
        st.write("")  # spacing
        for r in rows:
            col = SECTION_COLORS.get(r["Section"], "#a78bfa")
            st.markdown(
                f'<div class="row-card">'
                f'<strong>#{r["#"]}</strong> — {r["Name"]} &nbsp; {chip_html(r["Section"])}'
                f'</div>', unsafe_allow_html=True
            )
    else:
        st.info("No players yet.")

# -------- Schedule (Pairings) --------
with tab_schedule:
    st.subheader("Generate / Edit Pairings (both sections)")
    rnd = st.number_input("Round", 1, int(store.state.get("rounds", rounds)), 1, key="sc_round_combined")

    rules = store.state.get("rules", {})
    tiebreakers = rules.get("TIEBREAKERS", ["Total", "Verskil", "Player#"])
    all_sections = store.state.get("sections", DEFAULT_SECTIONS) or DEFAULT_SECTIONS
    rinksN_global = int(store.state.get("rinks", rinks))

    # Helper renders one section’s generator + editor for the current round
    def render_section_pairings(sec: str):
        key_pair = store.key_pair(sec, int(rnd))
        rinksN = int(store.state.get("rinks", rinks))
        pairings = store.state["pairings"].get(
            key_pair,
            [{"rink": r, "a_id": None, "b_id": None} for r in range(1, rinksN + 1)]
        )
        sec_color = SECTION_COLORS.get(sec, "#a78bfa")

        # Banner
        st.markdown(
            f'''
            <div class="schedule-wrap">
              <div class="section-banner">
                <span class="section-dot" style="background:{sec_color}"></span>
                <span class="section-title" style="color:{sec_color}">{sec}</span>
              </div>
            ''',
            unsafe_allow_html=True
        )

        # ---- Generator (per section) ----
        gcol1, gcol2, gcol3 = st.columns([3,2,1])
        algo = gcol1.selectbox(
            "Mode",
            [
                "Round 1: Random (within section)",
                "Strong vs Strong (standings, no repeats)",
                "Round-robin",
                "Finals: Mix Sections (standings, no repeats)",
            ],
            key=f"gen_mode_{sec}"
        )
        gcol2.selectbox("Apply to", ["This round"], key=f"gen_apply_{sec}")

        if gcol3.button("Generate", key=f"gen_go_{sec}"):
            players = [int(k) for k, v in store.state["players"].items() if v["section"] == sec]
            players.sort()

            if algo.startswith("Round 1"):
                ids = players[:]
                random.shuffle(ids)
                pairs = [(ids[i], ids[i+1] if i+1 < len(ids) else None) for i in range(0, len(ids), 2)]
                lmap = last_rink_map(store.state, sec, int(rnd))
                new_pairs = assign_rinks_with_preferences(rinksN, pairs, lmap)
                store.state["pairings"][key_pair] = new_pairs
                store.log("generate_r1_random", {"section": sec, "round": int(rnd), "pairs": new_pairs})
                store.save()
                st.success(f"{sec}: Round 1 random pairs generated.")

            elif algo.startswith("Strong vs Strong"):
                table = compute_standings(store.state, sec, rules, tiebreakers)
                prev = {r: store.state["pairings"].get(store.key_pair(sec, r), []) for r in range(1, int(rnd))}
                hist = build_history(prev)
                pairs = strong_vs_strong_pairs(table, hist)
                lmap = last_rink_map(store.state, sec, int(rnd))
                new_pairs = assign_rinks_with_preferences(rinksN, pairs, lmap)
                store.state["pairings"][key_pair] = new_pairs
                store.log("generate_strong_vs_strong", {"section": sec, "round": int(rnd), "pairs": new_pairs})
                store.save()
                st.success(f"{sec}: Strong-vs-strong pairs generated.")

            elif algo.startswith("Round-robin"):
                rr = round_robin_pairs(players)
                pairs = rr[int(rnd) - 1] if 1 <= int(rnd) <= len(rr) else []
                lmap = last_rink_map(store.state, sec, int(rnd))
                new_pairs = assign_rinks_with_preferences(rinksN, pairs, lmap)
                store.state["pairings"][key_pair] = new_pairs
                store.log("generate_roundrobin", {"section": sec, "round": int(rnd), "pairs": new_pairs})
                store.save()
                st.success(f"{sec}: Round-robin pairs generated.")

            elif algo.startswith("Finals"):
                # Build combined standings (both sections) and global history so pairs are identical
                sections_all = store.state.get("sections", ["SEKSIE 1", "SEKSIE 2"])
                all_rows = []
                for s in sections_all:
                    all_rows.extend(compute_standings(store.state, s, rules, tiebreakers))
                combined = sort_standings(all_rows, tiebreakers)

                prev_all = {}
                for s in sections_all:
                    for rprev in range(1, int(rnd)):
                        prev_all.setdefault(rprev, [])
                        prev_all[rprev].extend(store.state["pairings"].get(store.key_pair(s, rprev), []))
                hist = build_history(prev_all)

                pairs = strong_vs_strong_pairs(combined, hist)

                # Use a COMBINED last-rink map so rink assignment is the same no matter which section triggers Generate
                lmap_combined = {}
                for s in sections_all:
                    lmap_combined.update(last_rink_map(store.state, s, int(rnd)))

                new_pairs = assign_rinks_with_preferences(rinksN, pairs, lmap_combined)

                # Save IDENTICAL finals pairings to BOTH sections
                for s in sections_all:
                    store.state["pairings"][store.key_pair(s, int(rnd))] = new_pairs

                store.log("generate_finals_mix_both", {"sections": sections_all, "round": int(rnd), "pairs": new_pairs})
                store.save()
                st.success("Finals (mixed sections) pairs generated for both sections.")

                # Clear editors for BOTH sections so they immediately reflect the identical finals
                for s in sections_all:
                    ksec2 = re.sub(r"[^A-Za-z0-9_]+", "_", str(s)).lower()
                    k_prefix2 = f"sc_{ksec2}_{int(rnd)}"
                    for i in range(1, rinksN + 1):
                        st.session_state.pop(f"{k_prefix2}_a_{i}", None)
                        st.session_state.pop(f"{k_prefix2}_b_{i}", None)

                # Ensure the current editor also refreshes this render
                pairings = store.state["pairings"].get(key_pair, new_pairs)


            # clear ONLY this section/round’s widgets so fresh pairs show
            ksec = re.sub(r"[^A-Za-z0-9_]+", "_", str(sec)).lower()
            k_prefix = f"sc_{ksec}_{int(rnd)}"
            for i in range(1, rinksN + 1):
                st.session_state.pop(f"{k_prefix}_a_{i}", None)
                st.session_state.pop(f"{k_prefix}_b_{i}", None)
            # refresh local pairings for this render
            pairings = store.state["pairings"].get(key_pair, pairings)

        # ---- Editor (duplicate-safe, namespaced by section+round) ----
        def _section_player_options(store, section):
            rows = [(int(k), v["name"]) for k, v in store.state["players"].items() if v["section"] == section]
            rows.sort(key=lambda x: x[0])
            return [(None, "—")] + [(pid, f"{pid} — {name}") for pid, name in rows]

        def _options_for_round(store, sec, pairings):
            base = _section_player_options(store, sec)
            base_ids = {pid for pid, _ in base[1:]}
            extras, seen_extra = [], set()
            for pr in pairings:
                for pid in (pr.get("a_id"), pr.get("b_id")):
                    if not pid or pid in base_ids or pid in seen_extra:
                        continue
                    p = store.state["players"].get(str(pid))
                    if p:
                        label = f'{pid} — {p["name"]} ({p["section"]})'
                        extras.append((pid, label)); seen_extra.add(pid)
            extras.sort(key=lambda x: x[0])
            return base + extras

        base_opts = _options_for_round(store, sec, pairings)

        ksec = re.sub(r"[^A-Za-z0-9_]+", "_", str(sec)).lower()
        k_prefix = f"sc_{ksec}_{int(rnd)}"

        def _index_in(options, pid):
            for i, (p, _) in enumerate(options):
                if p == pid:
                    return i
            return 0  # "—"

        def _filter_opts(base, keep_id, exclude_ids):
            out = [base[0]]
            for pid, label in base[1:]:
                if pid == keep_id or (pid not in exclude_ids):
                    out.append((pid, label))
            return out

        hdr = st.columns([0.7, 5, 5])
        hdr[0].markdown("**Rink**")
        hdr[1].markdown("**A (top)**")
        hdr[2].markdown("**B (bottom)**")

        used_ids = set()
        new_pairs = []
        for idx in range(1, rinksN + 1):
            row = next((p for p in pairings if p["rink"] == idx), {"rink": idx, "a_id": None, "b_id": None})
            prev_a = st.session_state.get(f"{k_prefix}_a_{idx}")
            prev_b = st.session_state.get(f"{k_prefix}_b_{idx}")
            cur_a_id = (prev_a[0] if isinstance(prev_a, tuple) else row.get("a_id"))
            cur_b_id = (prev_b[0] if isinstance(prev_b, tuple) else row.get("b_id"))

            c1, c2, c3 = st.columns([0.7, 5, 5])
            with c1:
                st.markdown(
                    f'<div class="rink-row"><span class="rink-pill" style="border-color:{sec_color}; color:{sec_color}">{idx}</span></div>',
                    unsafe_allow_html=True
                )

            opts_a = _filter_opts(base_opts, cur_a_id, used_ids)
            a_idx = _index_in(opts_a, cur_a_id)
            with c2:
                a_choice = st.selectbox(
                    f"A_{sec}_{idx}", options=opts_a, index=a_idx,
                    key=f"{k_prefix}_a_{idx}", format_func=lambda x: x[1],
                    label_visibility="collapsed"
                )
            sel_a = a_choice[0]
            if sel_a is not None:
                used_ids.add(sel_a)

            opts_b = _filter_opts(base_opts, cur_b_id, used_ids)
            b_idx = _index_in(opts_b, cur_b_id)
            with c3:
                b_choice = st.selectbox(
                    f"B_{sec}_{idx}", options=opts_b, index=b_idx,
                    key=f"{k_prefix}_b_{idx}", format_func=lambda x: x[1],
                    label_visibility="collapsed"
                )
            sel_b = b_choice[0]

            new_pairs.append({"rink": idx, "a_id": sel_a, "b_id": sel_b})

        # Duplicate validation
        seen = {}
        for pr in new_pairs:
            for side in ("a_id", "b_id"):
                pid = pr.get(side)
                if pid:
                    seen.setdefault(pid, []).append(pr["rink"])
        dup_ids = [pid for pid, rlist in seen.items() if len(rlist) > 1]
        if dup_ids:
            names = [f'#{pid} — {store.state["players"].get(str(pid), {}).get("name", "")}' for pid in dup_ids]
            st.error("Duplicate players selected in this round: " + ", ".join(names))

        # Track unsaved changes (compare UI selections to saved pairings)
        existing_pairs_saved = store.state["pairings"].get(key_pair, [])
        def _canon(rows):
            return [{"rink": int(p.get("rink", 0)), "a_id": p.get("a_id"), "b_id": p.get("b_id")} for p in (rows or [])]
        st.session_state.setdefault("pairings_dirty", {})[key_pair] = (_canon(new_pairs) != _canon(existing_pairs_saved))

        # Wipe guard: if all empty and there are existing pairings, require explicit clear
        existing_pairs = store.state["pairings"].get(key_pair, [])
        all_empty = not any(p.get("a_id") or p.get("b_id") for p in new_pairs)

        btn_cols = st.columns([1, 1, 6])
        with btn_cols[0]:
            if st.button("Save pairings", key=f"{k_prefix}_save", disabled=bool(dup_ids)):
                if dup_ids:
                    st.error("Fix duplicates before saving.")
                elif all_empty and existing_pairs:
                    st.warning("No players selected. Not saving to avoid wiping existing pairings. Use 'Clear all pairings' to empty this round.")
                else:
                    store.state["pairings"][key_pair] = new_pairs if not all_empty else []
                    store.log("save_pairings", {"section": sec, "round": int(rnd), "pairs": store.state["pairings"][key_pair]})
                    store.save()
                    st.session_state.setdefault("pairings_dirty", {})[key_pair] = False
                    st.success(f"{sec}: Pairings saved.")

        with btn_cols[1]:
            if st.button("Clear all pairings", key=f"{k_prefix}_clear"):
                store.state["pairings"][key_pair] = []
                store.log("clear_pairings", {"section": sec, "round": int(rnd)})
                store.save()
                for i in range(1, rinksN + 1):
                    st.session_state.pop(f"{k_prefix}_a_{i}", None)
                    st.session_state.pop(f"{k_prefix}_b_{i}", None)
                st.session_state.setdefault("pairings_dirty", {})[key_pair] = False
                st.success(f"{sec}: All pairings cleared for this round.")

        st.markdown("</div>", unsafe_allow_html=True)  # close section box

    # Render BOTH sections for this round
    for sec in all_sections:
        render_section_pairings(sec)

    # Aggregate dirty flag across sections for this round
    _pd = st.session_state.get("pairings_dirty", {})
    st.session_state["pairings_dirty_any"] = any(bool(v) for v in _pd.values())


# -------- Scores --------
# -------- Scores --------
with tab_scores:
    st.subheader("Enter Scores")
    if st.session_state.get("pairings_dirty_any"):
        st.warning("Unsaved pairings detected in Schedule. Save or clear them to avoid losing changes.")
    sec = st.selectbox("Section", options=sections or DEFAULT_SECTIONS, key="scor_sec")
    rnd = st.number_input("Round", 1, int(store.state.get("rounds", rounds)), 1, key="scor_round")
    # Clear banner to make it obvious which section/round you are editing
    _sec_color = SECTION_COLORS.get(sec, "#a78bfa")
    st.markdown(
        f"<div class='section-banner'>"
        f"<span class='section-dot' style='background:{_sec_color}'></span>"
        f"<span class='section-title'>Capturing scores for <b>{sec}</b> — Round <b>{int(rnd)}</b></span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    key_pair = store.key_pair(sec, int(rnd))
    pairings = store.state["pairings"].get(key_pair, [])
    mirror_on = store.state.get("ui", {}).get("mirror_mode", True)

    if not pairings:
        st.warning("No pairings for this round.")
    else:
        st.caption("Tip: Mirror mode shows B as a live readout of A (toggle in the sidebar).")
        st.caption("Legend: Vir = for the player • Teen = against the same player")
        # Header row (columns aligned with data rows)
        h_rk, h_team, h_av, h_at, h_bv, h_bt, h_btn = st.columns([0.7, 2.8, 0.8, 0.8, 0.8, 0.8, 0.8])
        h_rk.markdown("**Rink**")
        h_team.markdown("**Teams**")
        # Column headers keep it compact; row inputs show player names
        h_av.markdown("<span class='col vir'>Vir</span>", unsafe_allow_html=True)
        h_at.markdown("<span class='col teen'>Teen</span>", unsafe_allow_html=True)
        h_bv.markdown("<span class='col vir'>Vir</span>", unsafe_allow_html=True)
        h_bt.markdown("<span class='col teen'>Teen</span>", unsafe_allow_html=True)
        h_btn.markdown("**Save**")

        # Rows
        for pr in pairings:
            render_rink_score_compact(
                section=sec, round_no=int(rnd), rink=int(pr.get("rink", 0)),
                pr=pr, store=store, mirror_on=mirror_on
            )

        # Save all
        c1, _ = st.columns([1,6])
        with c1:
            if st.button("Save all rinks", key=f"save_all_{sec}_{rnd}"):
                for pr in pairings:
                    rk = int(pr.get("rink", 0))
                    sk = store.key_score(sec, int(rnd), rk)
                    va = int(st.session_state.get(f"score_va_{sec}_{int(rnd)}_{rk}", 0))
                    ta = int(st.session_state.get(f"score_ta_{sec}_{int(rnd)}_{rk}", 0))
                    if mirror_on:
                        vb, tb = ta, va
                    else:
                        vb = int(st.session_state.get(f"score_vb_{sec}_{int(rnd)}_{rk}", 0))
                        tb = int(st.session_state.get(f"score_tb_{sec}_{int(rnd)}_{rk}", 0))
                    store.state["scores"][sk] = {"a": {"vir": va, "teen": ta}, "b": {"vir": vb, "teen": tb}}
                store.save()
                st.success("Saved scores for all rinks.")


# -------- Per-end --------
with tab_perend:
    st.subheader("Per-end (optional)")
    if st.session_state.get("pairings_dirty_any"):
        st.warning("Unsaved pairings detected in Schedule. Save or clear them before leaving.")

    sections_cfg = store.state.get("sections", DEFAULT_SECTIONS) or DEFAULT_SECTIONS
    sec = st.selectbox("Section", options=sections_cfg, key="pe_sec")
    rnd = st.number_input("Round", 1, int(store.state.get("rounds", DEFAULT_ROUNDS)), 1, key="pe_round")

    # Rinks that exist for this section/round (from pairings)
    key_pair = store.key_pair(sec, int(rnd))
    pairings = store.state.get("pairings", {}).get(key_pair, [])
    rink_ids = [int(pr.get("rink", 0)) for pr in pairings] or list(range(1, int(store.state.get("rinks", DEFAULT_RINKS)) + 1))
    rink = st.selectbox("Rink", options=rink_ids, key="pe_rink")

    # If there are no pairings at all, caution but allow data entry
    if not pairings:
        st.warning("No saved pairings for this round/section yet. You can still capture per-end data, but names will be blank.", icon="⚠️")

    # Show who is A/B (if known)
    pr = next((p for p in pairings if int(p.get("rink", 0)) == int(rink)), {"a_id": None, "b_id": None})
    a = store.state["players"].get(str(pr.get("a_id") or ""), {})
    b = store.state["players"].get(str(pr.get("b_id") or ""), {})
    a_name = a.get("name", "")
    b_name = b.get("name", "")

    st.caption(f"A = #{pr.get('a_id') or '—'} {a_name or ''} • B = #{pr.get('b_id') or '—'} {b_name or ''}")

    # Load or initialize per-end rows
    sk = store.key_score(sec, int(rnd), int(rink))
    pe_key = sk  # reuse the same triple key
    ends_n = int(store.state.get("rules", {}).get("ENDS_PER_GAME", 18))
    pe = store.state.get("scores_per_end", {}).get(pe_key, {"n": ends_n, "ends": [{"a": 0, "b": 0} for _ in range(ends_n)]})

    # If rules' ENDS_PER_GAME changed, resize safely
    if pe.get("n") != ends_n:
        old = pe["ends"]
        new = [{"a": (old[i]["a"] if i < len(old) else 0), "b": (old[i]["b"] if i < len(old) else 0)} for i in range(ends_n)]
        pe = {"n": ends_n, "ends": new}

    # Editor table (A and B points for each end)
    st.markdown("#### End-by-end points")
    head = st.columns([1,2,2])
    head[0].markdown("**End**")
    head[1].markdown(f"**A points** {'('+a_name+')' if a_name else ''}")
    head[2].markdown(f"**B points** {'('+b_name+')' if b_name else ''}")

    # Use namespaced keys so switching tabs/rounds doesn't collide
    k_prefix = f"pe_{sec}_{int(rnd)}_{int(rink)}"
    totals_a = totals_b = 0
    new_rows = []
    for i in range(1, ends_n + 1):
        c1, c2, c3 = st.columns([1,2,2])
        c1.write(f"{i}")
        a_val = c2.number_input(f"a_{i}", min_value=0, step=1, value=int(pe["ends"][i-1]["a"]), key=f"{k_prefix}_a_{i}", label_visibility="collapsed")
        b_val = c3.number_input(f"b_{i}", min_value=0, step=1, value=int(pe["ends"][i-1]["b"]), key=f"{k_prefix}_b_{i}", label_visibility="collapsed")
        new_rows.append({"a": int(a_val), "b": int(b_val)})
        totals_a += int(a_val)
        totals_b += int(b_val)

    st.markdown("---")
    st.write(f"**Totals from ends** → A: Vir={totals_a}, Teen={totals_b} • B: Vir={totals_b}, Teen={totals_a}")

    c1, c2, c3 = st.columns([1,1,4])

    # Save per-end AND write round totals into the existing 'scores' bucket
    if c1.button("Save per-end (update totals)", key=f"{k_prefix}_save"):
        store.state.setdefault("scores_per_end", {})[pe_key] = {"n": ends_n, "ends": new_rows}
        store.state.setdefault("scores", {})[sk] = {
            "a": {"vir": totals_a, "teen": totals_b},
            "b": {"vir": totals_b, "teen": totals_a},
        }
        store.log("save_per_end", {"key": pe_key, "n": ends_n})
        store.save()
        st.success("Per-end saved and totals updated.")

    # Clear just the per-end rows (keeps any previously entered totals untouched)
    if c2.button("Clear per-end rows", key=f"{k_prefix}_clear"):
        store.state.setdefault("scores_per_end", {}).pop(pe_key, None)
        store.log("clear_per_end_rows", {"key": pe_key})
        store.save()
        for i in range(1, ends_n + 1):
            st.session_state.pop(f"{k_prefix}_a_{i}", None)
            st.session_state.pop(f"{k_prefix}_b_{i}", None)
        st.success("Per-end rows cleared.")

# -------- Standings --------
with tab_standings:
    st.subheader("Standings")
    if st.session_state.get("pairings_dirty_any"):
        st.warning("Unsaved pairings detected in Schedule. Save or clear them before leaving.")
    rules = store.state.get("rules", {})
    tiebreakers = rules.get("TIEBREAKERS", ["Total","Verskil","Player#"])
    cols = st.columns(3)
    for idx, sec in enumerate(sections or DEFAULT_SECTIONS):
        with cols[idx % 3]:
            st.markdown(f"### {sec}")
            tbl = compute_standings(store.state, sec, rules, tiebreakers)
            st.table([{"#": r.player_id, "Speler": r.name, "Verskil": r.verskil, "Punte": r.punte, "Bonus": r.bonus, "Total": r.punte + r.bonus, "Posisie": i+1} for i,r in enumerate(tbl)])
    st.markdown("### Combined")
    all_rows = []
    for sec in sections or DEFAULT_SECTIONS:
        all_rows.extend(compute_standings(store.state, sec, rules, tiebreakers))
    # re-sort combined
    from engine import sort_standings
    combined = sort_standings(all_rows, tiebreakers)
    st.table([{"#": r.player_id, "Speler": r.name, "Sek": r.section, "Verskil": r.verskil, "Punte": r.punte, "Bonus": r.bonus, "Total": r.punte + r.bonus, "Posisie": i+1} for i,r in enumerate(combined)])

# -------- Leaderboard --------
with tab_lb:
    st.subheader("Live Leaderboard")
    if st.session_state.get("pairings_dirty_any"):
        st.warning("Unsaved pairings detected in Schedule. Save or clear them before leaving.")
    # Expand the dataframe to fill available viewport height
    st.markdown(
        """
        <style>
        #lb-wrap [data-testid="stDataFrame"] { height: calc(100vh - 260px) !important; }
        #lb-wrap [data-testid="stDataFrame"] div[role="grid"] { min-height: calc(100vh - 260px) !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("<div id='lb-wrap'>", unsafe_allow_html=True)

    sec_view = st.selectbox("View", options=["Combined"] + (sections or DEFAULT_SECTIONS), key="lb_view")
    interval = st.number_input("Auto-refresh (seconds)", 5, 120, 15, key="lb_int")
    st.caption("This will refresh when you reload; Streamlit Cloud/Local can auto-refresh on timer with external tools.")

    rules = store.state.get("rules", {})
    tiebreakers = rules.get("TIEBREAKERS", ["Total","Verskil","Player#"])
    if sec_view == "Combined":
        rows = []
        for s in sections or DEFAULT_SECTIONS:
            rows.extend(compute_standings(store.state, s, rules, tiebreakers))
        from engine import sort_standings
        rows = sort_standings(rows, tiebreakers)
        st.dataframe([{"Posisie": i+1, "#": r.player_id, "Speler": r.name, "Sek": r.section, "Total": r.punte + r.bonus, "Punte": r.punte, "Bonus": r.bonus, "Verskil": r.verskil} for i,r in enumerate(rows)], use_container_width=True, hide_index=True)
    else:
        rows = compute_standings(store.state, sec_view, rules, tiebreakers)
        st.dataframe([{"Posisie": i+1, "#": r.player_id, "Speler": r.name, "Total": r.punte + r.bonus, "Punte": r.punte, "Bonus": r.bonus, "Verskil": r.verskil} for i,r in enumerate(rows)], use_container_width=True, hide_index=True)

    st.markdown("</div>", unsafe_allow_html=True)

# -------- Import / Export --------
with tab_io:
    st.subheader("Import / Export")
    if st.session_state.get("pairings_dirty_any"):
        st.warning("Unsaved pairings detected in Schedule. Save or clear them before leaving.")

    # ---- Import players ----
    up = st.file_uploader(
        "Upload .xlsx / .xlsm (Players or Punte Sek 1/2 sheets)",
        type=["xlsx", "xlsm"],
        key="io_upl",
    )

    if up is not None:
        try:
            xls = pd.ExcelFile(up, engine="openpyxl")
            st.caption(f"Sheets found: {', '.join(xls.sheet_names)}")

            # Helpers
            def norm_cols(df):
                return {c.strip().lower(): c for c in map(str, df.columns)}

            def try_players_sheet():
                # Expect a sheet named "Players" with: Speler nr | Speler | Sek
                if "Players" in xls.sheet_names:
                    df = pd.read_excel(xls, sheet_name="Players", engine="openpyxl")
                    cols = norm_cols(df)
                    idc  = cols.get("speler nr") or cols.get("player #") or cols.get("player id") or cols.get("#")
                    namec = cols.get("speler") or cols.get("name")
                    sekc = cols.get("sek") or cols.get("section") or cols.get("seksie") or cols.get("seksie 1/2")
                    if idc and namec and sekc:
                        out = df[[idc, namec, sekc]].rename(columns={idc:"Speler nr", namec:"Speler", sekc:"Sek"})
                        return out
                return None

            def try_punte_sheets():
                # Look for "Punte Sek 1" / "Punte Sek 2" style sheets
                frames = []
                for sh in xls.sheet_names:
                    low = sh.lower().replace(" ", "")
                    if any(k in low for k in ["puntesek1", "seksie1", "sek1"]) or any(k in low for k in ["puntesek2", "seksie2", "sek2"]):
                        df = pd.read_excel(xls, sheet_name=sh, engine="openpyxl")
                        cols = norm_cols(df)
                        idc  = cols.get("speler nr") or cols.get("player #") or cols.get("player id") or cols.get("#")
                        namec = cols.get("speler") or cols.get("name")
                        if idc and namec:
                            sek = "SEKSIE 2" if any(k in low for k in ["2","sek2","seksie2","puntesek2"]) else "SEKSIE 1"
                            out = df[[idc, namec]].rename(columns={idc:"Speler nr", namec:"Speler"})
                            out["Sek"] = sek
                            frames.append(out)
                if frames:
                    return pd.concat(frames, ignore_index=True)
                return None

            players_df = try_players_sheet()
            if players_df is None:
                players_df = try_punte_sheets()

            if players_df is None:
                st.info("No compatible sheet found. Expected either a 'Players' sheet "
                        "with columns **Speler nr, Speler, Sek**, or Punte sheets for Sek 1/2.")
            else:
                # Clean up rows
                players_df = players_df.dropna(subset=["Speler","Speler nr"])
                players_df["Speler nr"] = players_df["Speler nr"].astype(int)
                players_df["Speler"] = players_df["Speler"].astype(str).str.strip()
                players_df["Sek"] = players_df["Sek"].astype(str).str.upper().str.replace(" ", "")
                players_df["Sek"] = players_df["Sek"].replace({"SEKSIE1":"SEKSIE 1","SEKSIE2":"SEKSIE 2","SEK1":"SEKSIE 1","SEK2":"SEKSIE 2"})

                st.success(f"Detected {len(players_df)} players:")
                st.dataframe(players_df, use_container_width=True, hide_index=True)

                if st.button("Import players", key="io_import_players"):
                    count = 0
                    for _, row in players_df.iterrows():
                        pid = int(row["Speler nr"])
                        name = row["Speler"]
                        sek  = row["Sek"] if row["Sek"] in ("SEKSIE 1","SEKSIE 2") else "SEKSIE 1"
                        if name and pid > 0:
                            store.state["players"][str(pid)] = {"name": name, "section": sek}
                            count += 1
                    store.save()
                    st.toast(f"Imported {count} players", icon="✅")

        except Exception as e:
            st.exception(e)

    st.markdown("---")

    # ---- Export workbook ----
    if st.button("Download Export Workbook", key="io_exp_btn"):
        # Gather data in-memory and write with openpyxl (no extra deps)
        out = io.BytesIO()

        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            # Players
            players = [{"Speler nr": int(k), "Speler": v["name"], "Sek": v["section"]}
                       for k, v in store.state.get("players", {}).items()]
            if players:
                pd.DataFrame(players).sort_values("Speler nr").to_excel(writer, sheet_name="Players", index=False)
            else:
                pd.DataFrame(columns=["Speler nr","Speler","Sek"]).to_excel(writer, sheet_name="Players", index=False)

            # Pairings
            rows = []
            for key, prs in store.state.get("pairings", {}).items():
                sek, rnd = key.split(":"); rnd = int(rnd)
                for pr in prs:
                    rows.append({"Sek": sek, "Round": rnd, "Rink": pr["rink"], "A_id": pr["a_id"], "B_id": pr["b_id"]})
            pd.DataFrame(rows).to_excel(writer, sheet_name="Pairings", index=False)

            # Standings per section + Combined
            sections = store.state.get("sections", ["SEKSIE 1","SEKSIE 2"])
            rules = store.state.get("rules", {})
            tiebreakers = rules.get("TIEBREAKERS", ["Total","Verskil","Player#"])

            def to_df(table):
                return pd.DataFrame([{
                    "#": r.player_id,
                    "Speler": r.name,
                    "Sek": getattr(r, "section", None),
                    "Verskil": r.verskil,
                    "Punte": r.punte,
                    "Bonus": getattr(r, "bonus", 0),
                    "Total": r.punte + getattr(r, "bonus", 0),
                    "Posisie": i + 1
                } for i, r in enumerate(table)])

            # Per-section
            for sek in sections:
                tbl = compute_standings(store.state, sek, rules, tiebreakers)
                to_df(tbl).drop(columns=["Sek"]).to_excel(writer, sheet_name=f"Standings {sek}", index=False)

            # Combined
            all_rows = []
            for sek in sections:
                all_rows.extend(compute_standings(store.state, sek, rules, tiebreakers))
            from engine import sort_standings
            combined = sort_standings(all_rows, tiebreakers)
            to_df(combined).to_excel(writer, sheet_name="Combined", index=False)

        st.download_button(
            "Download .xlsx",
            data=out.getvalue(),
            file_name=f"rolbal_export_{dt.datetime.now().date()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="io_exp_dl",
        )

# -------- Tools --------
with tab_tools:
    st.subheader("Tools")
    # Cloud/DB section
    if SUPABASE_CONFIGURED and _user and _user.get("id"):
        st.markdown("### Cloud / Database")
        c1, c2, c3 = st.columns(3)
        if c1.button("Reload from cloud", key="db_reload"):
            try:
                store.load()
                st.success("Reloaded latest state from Supabase")
            except Exception as e:
                st.error(f"Could not reload: {e}")
        if c2.button("Save to cloud", key="db_save"):
            try:
                store.save()
                st.success("Saved current state to Supabase")
            except Exception as e:
                st.error(f"Could not save: {e}")
        with c3:
            import json
            backup = json.dumps(store.state, ensure_ascii=False, indent=2).encode("utf-8")
            st.download_button("Download JSON backup", data=backup, file_name="rolbal_backup.json", mime="application/json", key="db_backup")
    if st.session_state.get("pairings_dirty_any"):
        st.warning("Unsaved pairings detected in Schedule. Save or clear them before leaving.")
    sec = st.selectbox("Section", options=sections or DEFAULT_SECTIONS, key="tl_sec")
    rnd = st.number_input("Round", 1, int(store.state.get("rounds", rounds)), 1, key="tl_round")
    key_pair = store.key_pair(sec, int(rnd))
    locked = store.state.get("locks", {}).get(key_pair, False)
    c1,c2 = st.columns(2)
    if c1.button("Lock this round", key="tl_lock", disabled=locked):
        store.state["locks"][key_pair] = True
        store.log("lock_round", {"section": sec, "round": int(rnd)})
        store.save()
        st.success("Round locked")
    if c2.button("Unlock this round", key="tl_unlock", disabled=not locked):
        store.state["locks"][key_pair] = False
        store.log("unlock_round", {"section": sec, "round": int(rnd)})
        store.save()
        st.success("Round unlocked")
    st.markdown("### Audit log")
    log = store.state.get("audit", [])[-100:]
    if log:
        import time
        st.table([{"When": dt.datetime.fromtimestamp(x["ts"]).strftime("%Y-%m-%d %H:%M:%S"), "Action": x["action"], "Details": str(x["payload"])[:80]} for x in reversed(log)])
    else:
        st.info("No actions logged yet.")
