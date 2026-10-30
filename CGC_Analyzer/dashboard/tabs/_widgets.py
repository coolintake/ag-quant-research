"""
tabs/_widgets.py
==================
Shared UI helpers reused across multiple tabs. Every tab that has a
"Commodities to include" multiselect uses this, so the Reset behavior
can't drift inconsistent between tabs by being duplicated 3+ times.
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
    """A "Commodities to include" multiselect with a single Reset button
    directly above it. Reset selects every option -- every current caller
    passes `default=options`, so a separate "Select All" button would do
    the exact same thing as Reset; one button replaces two that were
    functionally identical.

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
    happens on every run here once the Reset callback has touched
    `st.session_state[key]`. Seeding `st.session_state[key]` once, only if
    it's not already present, reproduces `default`'s actual effect (the
    widget's value on its very first render) without ever handing the
    widget both inputs at once.
    """
    default = list(default) if default else list(options)
    if key not in st.session_state:
        st.session_state[key] = list(default)

    def _reset() -> None:
        st.session_state[key] = list(default)

    button_col, _spacer = st.columns([1, 7])
    with button_col:
        st.button("Reset", key=f"{key}__reset", on_click=_reset, width="stretch")

    return st.multiselect(label, options, key=key, help=help)


def pills_single_select_with_reset(
    label: str,
    options: List[str],
    default: str,
    key: str = "pills",
    help: Optional[str] = None,
) -> str:
    """A single-select `st.pills` control with a Reset button directly
    above it (Reset returns to `default`) -- the same callback pattern as
    `commodity_multiselect_with_quick_actions`, adapted for a single-choice
    control rather than a multiselect. "Select All" has no meaning for a
    single-choice pill row, so only Reset is offered here.

    Deliberately does NOT pass `required=True` to the underlying
    `st.pills` call -- that kwarg was added in a Streamlit version newer
    than some deployments run (confirmed: raises `TypeError: unexpected
    keyword argument 'required'` on Streamlit versions that predate it).
    Instead, a `None` return (which single-select `st.pills` can produce
    if the active pill is clicked again to deselect it, depending on
    version) is handled explicitly below by falling back to `default` for
    that render -- this reproduces `required=True`'s practical effect
    without depending on a kwarg that may not exist everywhere this app
    runs.
    """
    if key not in st.session_state:
        st.session_state[key] = default

    def _reset() -> None:
        st.session_state[key] = default

    button_col, _spacer = st.columns([1, 7])
    with button_col:
        st.button("Reset", key=f"{key}__reset", on_click=_reset, width="stretch")

    selected = st.pills(label, options, key=key, help=help, selection_mode="single")
    return selected if selected is not None else default
