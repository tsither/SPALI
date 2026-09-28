"""ASP interface for preference fragments.

Loads and toggles ASP externals in a clingo Control object based on the
active PreferenceState. All preference state classes live in core.state.
"""

from __future__ import annotations

import os

from clingo import Control, Function, Number

from core.state import Preference, PreferenceState

_FRAGMENTS_DIR = os.path.dirname(__file__)

_RESET_NAMES = {"pgroup"}


def load_fragments(ctl: Control, pref_state: PreferenceState) -> None:
    """Load the preference fragment file into ctl and ground it.

    Idempotent — safe to call multiple times on the same (ctl, pref_state)
    pair. After loading, all preference externals default to False.
    """
    if pref_state._fragments_loaded:
        return
    path = os.path.join(_FRAGMENTS_DIR, "preferences.lp")
    with open(path) as f:
        ctl.add("pref_fragments", [], f.read())
    ctl.ground([("pref_fragments", [])])
    pref_state._fragments_loaded = True


def apply_preferences(ctl: Control, pref_state: PreferenceState) -> Control:
    """Load fragments (idempotent), reset all preference externals to False,
    then activate the externals for every currently enabled preference.

    Safe to call multiple times — fully re-applies the current state each
    time. Does NOT rebuild or re-ground the Control object.

    Returns the ctl for convenient chaining.
    """
    load_fragments(ctl, pref_state)
    _reset_preference_externals(ctl)
    for pref in pref_state.active():
        _apply_preference(ctl, pref)
    _apply_llm_preferences(ctl, pref_state)
    return ctl


def collect_pin_assumptions(pref_state: PreferenceState) -> list[tuple]:
    """Build (Symbol, bool) assumption tuples from all custom pins.

    Returned list is passed directly to ctl.solve(assumptions=...).
    No grounding required — assumptions are applied per solve call.
    """
    result = []
    for pin in pref_state.custom_pins:
        for a in pin.assumptions:
            sym = Function(
                "in", [Function(a["module"]), Function("s", [Number(a["semester"])])]
            )
            result.append((sym, a["positive"]))
    return result


def _reset_preference_externals(ctl: Control) -> None:
    """Set all preference-related externals to False."""
    for atom in ctl.symbolic_atoms:
        if atom.is_external:
            name = atom.symbol.name
            if name.startswith("pref_") or name == "llm_pref_active" or name in _RESET_NAMES:
                ctl.assign_external(atom.symbol, False)


def _apply_llm_preferences(ctl: Control, pref_state: PreferenceState) -> None:
    """Set llm_pref_active(N) externals for all LLM-generated custom preferences."""
    for p in pref_state.llm_custom_preferences:
        ctl.assign_external(Function("llm_pref_active", [Number(p.slot)]), p.enabled)


def _apply_preference(ctl: Control, pref: Preference) -> None:
    """Set ASP externals for a single preference based on its name and params.

    Only standard_timeline and credit_balancing map to ASP externals. The
    others (diversity, n_semesters, n_plans) affect the solve pipeline
    rather than ASP externals.
    """
    t = pref.name
    p = pref.params

    if t == "standard_timeline":
        ctl.assign_external(Function("pref_ordering"), True)
        prio = int(p.get("priority", 2))
        ctl.assign_external(Function("pref_ordering_prio", [Number(prio)]), True)

    elif t == "credit_balancing":
        ctl.assign_external(Function("pref_balance"), True)
        prio = int(p.get("priority", 1))
        ctl.assign_external(Function("pref_balance_prio", [Number(prio)]), True)

    # diversity, n_semesters, n_plans — handled at solve level, not here
