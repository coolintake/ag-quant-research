"""
tabs/_widgets.py
==================
Shared UI helpers reused across multiple tabs. Every tab that has a
"Commodities to include" multiselect uses this, so the Select All / Reset
behavior can't drift inconsistent between tabs by being duplicated 3+ times.
"""

from typing import List, Optional

import streamlit as st


def commodity_multiselect_with_quick_actions(
    label: str,
    options: List[str],
    default: Optional[List[str]] = None,
    key: str = "commodities",
    help: Optional[str] = None,
) -> List[str]:
    """A "Commodities to include" multiselect with Select All / Reset
    buttons directly above it.

    Uses Streamlit's `on_click` callback pattern, NOT a manual
    `st.rerun()`: callbacks run in a dedicated phase before the script
    re-executes and before the multiselect widget is re-instantiated, so
    the new selection is picked up on that same, single, natural rerun --
    the one Streamlit already performs for any button press. Manually
    mutating `st.session_state[key]` after the widget has already run in
    the current script pass (or calling `st.rerun()` on top of that) is
    what actually causes the double-rerun / stale-then-correct flicker
    this was built to avoid; the callback approach never hits that path.

    Deliberately never passes `default=` to the underlying `st.multiselect`
    call itself -- Streamlit warns ("created with a default value but also
    had its value set via the Session State API") if a keyed widget gets
    both a `default` and a session-state value in the same run, which
    happens on every run here once a button callback has touched
    `st.session_state[key]`. Seeding `st.session_state[key]` once, only if
    it's not already present, reproduces `default`'s actual effect (the
    widget's value on its very first render) without ever handing the
    widget both inputs at once.
    """
    default = list(default) if default else list(options)
    if key not in st.session_state:
        st.session_state[key] = list(default)

    def _select_all() -> None:
        st.session_state[key] = list(options)

    def _reset() -> None:
        st.session_state[key] = list(default)

    button_col1, button_col2, _spacer = st.columns([1, 1, 6])
    with button_col1:
        st.button("Select All", key=f"{key}__select_all", on_click=_select_all, width="stretch")
    with button_col2:
        st.button("Reset", key=f"{key}__reset", on_click=_reset, width="stretch")

    return st.multiselect(label, options, key=key, help=help)
