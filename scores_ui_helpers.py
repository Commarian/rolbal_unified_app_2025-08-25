"""
Helpers to apply a compact visual style to the Scores tab without
affecting the rest of the app.

Usage in app.py (inside the Scores tab/page only):

    from scores_ui_helpers import apply_scores_compact
    apply_scores_compact()

This injects styles/styles_slim.css at runtime via st.markdown.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st


_CSS_DEFAULT_PATH = Path("styles") / "scores_slim.css"


def apply_scores_compact(css_path: Path | str = _CSS_DEFAULT_PATH) -> None:
    """Inject compact CSS for the Scores view.

    Call this only within the Scores tab/page to avoid globally shrinking
    other parts of the app.

    Parameters
    ----------
    css_path: Path | str
        Optional override to a custom CSS file.
    """

    try:
        path = Path(css_path)
        if path.exists():
            css = path.read_text(encoding="utf-8")
        else:
            # Fallback to an embedded minimal stylesheet if file not found
            css = """
            <style>
            :root { --scores-font: 13.5px; --scores-line: 1.25; --scores-gap: 6px; --scores-pad: 4px 6px; --scores-radius: 6px; }
            [data-testid="stMarkdownContainer"] h1, [data-testid="stMarkdownContainer"] h2, [data-testid="stMarkdownContainer"] h3 { margin: 4px 0 8px !important; line-height: 1.2 !important; }
            [data-testid="stMarkdownContainer"] p, label { font-size: 12px !important; line-height: 1.2 !important; margin: 0 0 4px !important; }
            input[type="text"], input[type="number"], textarea, select { height: 28px !important; padding: var(--scores-pad) !important; font-size: var(--scores-font) !important; line-height: var(--scores-line) !important; border-radius: var(--scores-radius) !important; }
            .stSelectbox [data-baseweb="select"] > div { min-height: 28px !important; }
            .stSelectbox [role="combobox"] { min-height: 28px !important; padding: 0 6px !important; font-size: var(--scores-font) !important; }
            .stNumberInput input, .stTextInput input { height: 28px !important; }
            .stButton > button { height: 28px !important; padding: 2px 10px !important; font-size: 12px !important; border-radius: var(--scores-radius) !important; margin: 0 !important; }
            [data-testid="stHorizontalBlock"], .stColumns { gap: var(--scores-gap) !important; }
            .stAlert, .stRadio, .stSelectbox, .stNumberInput, .stTextInput { margin-bottom: var(--scores-gap) !important; }
            .scores-row { padding: 4px 6px !important; }
            .scores-header { padding: 2px 6px !important; font-size: 12px !important; }
            .stDataFrame, .stTable table { font-size: 13px !important; }
            </style>
            """

        if not css.strip().startswith("<style>"):
            css = f"<style>\n{css}\n</style>"

        st.markdown(css, unsafe_allow_html=True)

    except Exception as exc:  # pragma: no cover - defensive
        st.warning(f"Could not apply compact scores CSS: {exc}")


__all__ = ["apply_scores_compact"]

