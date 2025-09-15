from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Dict, Any

import streamlit as st

try:
    from supabase import create_client, Client  # type: ignore
except Exception:
    create_client = None  # type: ignore
    Client = None  # type: ignore


@dataclass
class AuthSession:
    user: Dict[str, Any]
    access_token: str
    refresh_token: Optional[str]


def _get_supabase_keys() -> Optional[tuple[str, str]]:
    """Read Supabase URL and Anon key from Streamlit secrets or env vars."""
    url = None
    key = None
    if "supabase" in st.secrets:
        url = st.secrets["supabase"].get("url")
        key = st.secrets["supabase"].get("anon_key")
    url = url or os.getenv("SUPABASE_URL")
    key = key or os.getenv("SUPABASE_ANON_KEY")
    if url and key:
        return url, key
    return None


def diagnose_config() -> Dict[str, Any]:
    """Return a non-sensitive snapshot of auth config state for debugging.

    Does not include secret values.
    """
    import_ok = create_client is not None
    secrets_present = False
    via = None
    try:
        if "supabase" in st.secrets:
            sb = st.secrets["supabase"]
            if isinstance(sb, dict) and sb.get("url") and sb.get("anon_key"):
                secrets_present = True
                via = "st.secrets[supabase]"
    except Exception:
        pass
    env_present = bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_ANON_KEY"))
    if not secrets_present and env_present:
        via = "env"
    client_ok = False
    if import_ok and (secrets_present or env_present):
        try:
            client_ok = get_client() is not None
        except Exception:
            client_ok = False
    return {
        "import_ok": import_ok,
        "secrets_present": secrets_present,
        "env_present": env_present,
        "client_ok": client_ok,
        "source": via,
    }


def get_client() -> Optional["Client"]:
    """Return a Supabase client if configured, else None."""
    if create_client is None:
        return None
    keys = _get_supabase_keys()
    if not keys:
        return None
    url, key = keys
    try:
        return create_client(url, key)
    except Exception:
        return None


def _redirect_url_default() -> Optional[str]:
    # Prefer Streamlit Cloud-provided base URL in secrets if set
    try:
        if "supabase" in st.secrets:
            v = st.secrets["supabase"].get("redirect_url")
            if v:
                return str(v)
    except Exception:
        pass
    # Fallback to env var (useful locally)
    v = os.getenv("SUPABASE_REDIRECT_URL") or os.getenv("APP_URL")
    return v


def sign_up(email: str, password: str) -> AuthSession:
    sb = get_client()
    if sb is None:
        raise RuntimeError("Supabase is not configured or client unavailable")
    opts = {}
    redirect_to = _redirect_url_default()
    if redirect_to:
        opts = {"email_redirect_to": redirect_to}
    res = sb.auth.sign_up({"email": email, "password": password, "options": opts})
    # Some providers require email confirmation; session may be None initially
    user = getattr(res, "user", None) or getattr(res, "user", {})
    session = getattr(res, "session", None)
    access = getattr(session, "access_token", None) if session else None
    refresh = getattr(session, "refresh_token", None) if session else None
    return AuthSession(user=_to_dict(user), access_token=access, refresh_token=refresh)


def sign_in(email: str, password: str) -> AuthSession:
    sb = get_client()
    if sb is None:
        raise RuntimeError("Supabase is not configured or client unavailable")
    res = sb.auth.sign_in_with_password({"email": email, "password": password})
    session = getattr(res, "session", None)
    user = getattr(res, "user", None)
    if not session or not user:
        raise RuntimeError("Invalid login: no session returned")
    # Persist session to client for future get_user calls
    try:
        sb.auth.set_session(session.access_token, session.refresh_token)  # type: ignore[attr-defined]
    except Exception:
        pass
    return AuthSession(user=_to_dict(user), access_token=session.access_token, refresh_token=session.refresh_token)


def sign_out() -> None:
    sb = get_client()
    if sb is None:
        return
    try:
        sb.auth.sign_out()
    except Exception:
        pass


def send_otp(email: str) -> None:
    sb = get_client()
    if sb is None:
        raise RuntimeError("Supabase is not configured or client unavailable")
    # Sends a magic link or code depending on project settings
    redirect_to = _redirect_url_default()
    opts = {"email_redirect_to": redirect_to} if redirect_to else {}
    sb.auth.sign_in_with_otp({"email": email, "options": opts})


def verify_otp(email: str, code: str) -> AuthSession:
    sb = get_client()
    if sb is None:
        raise RuntimeError("Supabase is not configured or client unavailable")
    res = sb.auth.verify_otp({"email": email, "token": code, "type": "email"})
    session = getattr(res, "session", None)
    user = getattr(res, "user", None)
    if not session or not user:
        raise RuntimeError("Invalid code or expired OTP")
    try:
        sb.auth.set_session(session.access_token, session.refresh_token)  # type: ignore[attr-defined]
    except Exception:
        pass
    return AuthSession(user=_to_dict(user), access_token=session.access_token, refresh_token=session.refresh_token)


def _to_dict(obj: Any) -> Dict[str, Any]:
    """Convert a Supabase user object to a plain dict."""
    try:
        # Attempt dataclass-like conversion
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if hasattr(obj, "__dict__"):
            return dict(obj.__dict__)
    except Exception:
        pass
    # Fallback best-effort
    return dict(obj or {})
