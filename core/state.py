# core/state.py
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StudentState:
    # Core info
    current_semester: Optional[int] = None
    bridge_modules: list = field(default_factory=list)
    n_semesters: Optional[int] = None

    # Completed modules
    completed_modules: list = field(default_factory=list)

    # Planning
    selected_courses: list = field(default_factory=list)
    study_plan: list = field(default_factory=list)

    # Section completion flags
    core_info_confirmed: bool = False
    completed_modules_confirmed: bool = False
    plan_confirmed: bool = False

    # Meta
    program_id: str = ""

    # --- Completeness checks ---

    def is_core_info_complete(self) -> bool:
        """Core info section is complete when confirmed by user."""
        return self.core_info_confirmed

    def is_completed_modules_complete(self) -> bool:
        """Completed modules section - skip if semester 1, else need confirmation."""
        if self.current_semester == 1:
            return True
        return self.completed_modules_confirmed

    def is_plan_complete(self) -> bool:
        """Plan section is complete when confirmed by user."""
        return self.plan_confirmed

    def get_current_phase(self) -> int:
        """Return 0-indexed current phase for progress panel."""
        if not self.is_core_info_complete():
            return 0
        if not self.is_completed_modules_complete():
            return 1
        if not self.is_plan_complete():
            return 2
        return 3  # all done

    # --- Field validation (for prompting user) ---

    def has_required_core_fields(self) -> bool:
        """Check if all core info fields are filled (before confirmation)."""
        return all(
            [
                self.current_semester is not None,
                len(self.bridge_modules) > 0,
                self.n_semesters is not None,
            ]
        )

    def get_missing_core_fields(self) -> list[str]:
        """Return list of missing core info fields."""
        missing = []
        if self.current_semester is None:
            missing.append("current semester")
        if len(self.bridge_modules) == 0:
            missing.append("bridge modules (or 'none' if you have none)")
        if self.n_semesters is None:
            missing.append("number of semesters to plan")
        return missing


# ---------------------------------------------------------------------------
# Preference state (for study plan navigation — Phase 4)
# ---------------------------------------------------------------------------

_PIN_LABELS = "abcdefghijklmnopqrstuvwxyz"

VALID_PREFERENCES = frozenset(
    {
        "standard_timeline",
        "credit_balancing",
        "diversity",
        "n_semesters",
        "n_plans",
    }
)

# Enabled at the start of a navigation session. Applied one preference at a
# time via apply_defaults().
DEFAULT_PREFERENCES = ("standard_timeline", "credit_balancing", "diversity")


@dataclass
class CustomPin:
    """A single user-declared placement constraint."""

    label: str  # "a", "b", "c", ...
    summary: str  # human-readable description
    assumptions: list  # [{"module": str, "semester": int, "positive": bool}, ...]


@dataclass
class Preference:
    """A single preference with its parameters."""

    name: str
    enabled: bool = True
    params: dict = field(default_factory=dict)


def _parse_asp_priority(asp_code: str) -> int:
    """Extract the highest @N priority level from ASP weak constraint syntax."""
    hits = re.findall(r'\[.*?@(\d+)', asp_code)
    return max((int(h) for h in hits), default=3)


def _rewrite_asp_priority(asp_code: str, new_priority: int) -> str:
    """Replace all @N occurrences inside weak constraint weight brackets with @new_priority."""
    return re.sub(r'(\[.*?)@\d+', lambda m: f"{m.group(1)}@{new_priority}", asp_code)


@dataclass
class LLMCustomPreference:
    """An LLM-generated ASP soft constraint, toggleable at runtime."""

    label: str                    # "c1", "c2", ...
    description: str              # user's original free-text
    asp_code: str                 # full generated fragment (stored for reground replay)
    slot: int                     # N in llm_pref_active(N)
    priority: int = 3             # ASP @N priority level, parsed from asp_code at creation
    enabled: bool = True


@dataclass
class PreferenceState:
    """All preferences for a solve session.

    The default state is empty — no preferences active — so the solver
    returns arbitrary valid plans without any optimization.

    Fragments are loaded lazily the first time apply_preferences() is
    called. They are grounded into the Control object once and cannot be
    unloaded; only the external guards are toggled between solves.
    """

    preferences: dict[str, Preference] = field(default_factory=dict)
    custom_pins: list[CustomPin] = field(default_factory=list)
    llm_custom_preferences: list[LLMCustomPreference] = field(default_factory=list)
    _fragments_loaded: bool = False
    _llm_slot_counter: int = 0
    _pending_reground: bool = False

    # ------------------------------------------------------------------
    # Preference management
    # ------------------------------------------------------------------

    def add(self, name: str, **params) -> None:
        """Add or replace a preference. Overwrites existing params."""
        if name not in VALID_PREFERENCES:
            raise ValueError(
                f"Unknown preference '{name}'. "
                f"Valid preferences: {sorted(VALID_PREFERENCES)}"
            )
        self.preferences[name] = Preference(name=name, enabled=True, params=params)

    def remove(self, name: str) -> None:
        """Remove a preference entirely."""
        self.preferences.pop(name, None)

    def toggle(self, name: str, enabled: bool) -> None:
        """Enable or disable a preference without removing its params."""
        if name in self.preferences:
            self.preferences[name].enabled = enabled

    def clear(self) -> None:
        """Remove all preferences, custom pins, and LLM custom preferences."""
        self.preferences.clear()
        self.custom_pins.clear()
        self.llm_custom_preferences.clear()
        # _llm_slot_counter is NOT reset — slots are permanent within a session

    def active(self) -> list[Preference]:
        """Return all enabled preferences."""
        return [p for p in self.preferences.values() if p.enabled]

    def apply_defaults(self) -> None:
        """Enable the preferences a session starts with.

        Additive: a preference that is already configured is left exactly as
        it is, since add() would overwrite its params. Nothing here can
        discard preferences, priorities, pins, or the planning horizon.
        """
        for pref_name in DEFAULT_PREFERENCES:
            if pref_name not in self.preferences:
                self.add(pref_name)

    # ------------------------------------------------------------------
    # Custom pin management
    # ------------------------------------------------------------------

    def _next_pin_label(self) -> str:
        """Return the next available letter label for a custom pin."""
        used = {p.label for p in self.custom_pins}
        for ch in _PIN_LABELS:
            if ch not in used:
                return ch
        raise RuntimeError("Too many custom pins (max 26)")

    def add_custom_pin(self, summary: str, assumptions: list) -> str:
        """Add a custom pin constraint. Returns the assigned label."""
        label = self._next_pin_label()
        self.custom_pins.append(
            CustomPin(label=label, summary=summary, assumptions=assumptions)
        )
        return label

    def remove_custom_pin(self, label: str) -> bool:
        """Remove a custom pin by label. Returns True if found."""
        for i, pin in enumerate(self.custom_pins):
            if pin.label == label:
                self.custom_pins.pop(i)
                return True
        return False

    # ------------------------------------------------------------------
    # LLM custom preference management
    # ------------------------------------------------------------------

    def add_llm_custom(self, description: str, asp_code: str) -> str:
        """Add an LLM-generated custom preference. Returns the assigned label."""
        self._llm_slot_counter += 1
        slot = self._llm_slot_counter
        label = f"c{slot}"
        self.llm_custom_preferences.append(
            LLMCustomPreference(
                label=label,
                description=description,
                asp_code=asp_code,
                slot=slot,
                priority=_parse_asp_priority(asp_code),
                enabled=True,
            )
        )
        return label

    def set_llm_custom_priority(self, label: str, new_priority: int) -> bool:
        """Rewrite the ASP code priority for a custom preference. Returns True if found.

        Since @N priority is fixed at ground time, this also sets _pending_reground so
        the caller knows to rebuild the Control before the next solve.
        """
        for pref in self.llm_custom_preferences:
            if pref.label == label:
                pref.asp_code = _rewrite_asp_priority(pref.asp_code, new_priority)
                pref.priority = new_priority
                self._pending_reground = True
                return True
        return False

    def remove_llm_custom(self, label: str) -> bool:
        """Remove a custom LLM preference by label. Returns True if found."""
        for i, pref in enumerate(self.llm_custom_preferences):
            if pref.label == label:
                self.llm_custom_preferences.pop(i)
                return True
        return False

    def toggle_llm_custom(self, label: str, enabled: bool) -> bool:
        """Toggle a custom LLM preference by label. Returns True if found."""
        for pref in self.llm_custom_preferences:
            if pref.label == label:
                pref.enabled = enabled
                return True
        return False

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def has_diversity(self) -> bool:
        """Check if diversity preference is active."""
        p = self.preferences.get("diversity")
        return p is not None and p.enabled

    def get_n_semesters(self) -> int | None:
        """Return overridden n_semesters value, or None if not set."""
        p = self.preferences.get("n_semesters")
        if p and p.enabled:
            return p.params.get("value")
        return None

    def get_n_plans(self) -> int | None:
        """Return user-requested number of plans to generate, or None if not set."""
        p = self.preferences.get("n_plans")
        if p and p.enabled:
            return p.params.get("value")
        return None

    def summary(self) -> str:
        """Human-readable summary of active preferences and pins."""
        lines = []

        active = self.active()
        if not active and not self.custom_pins and not self.llm_custom_preferences:
            return "\n".join(lines) if lines else "No preferences active (unconstrained enumeration)"

        _PRIO_DEFAULTS = {"standard_timeline": 2, "credit_balancing": 1}
        for p in active:
            status = "ON" if p.enabled else "OFF"
            line = f"  [{status}] {p.name}"
            extra_parts = []
            if p.name in _PRIO_DEFAULTS:
                prio = p.params.get("priority", _PRIO_DEFAULTS[p.name])
                extra_parts.append(f"priority: {prio}")
            other_params = {k: v for k, v in p.params.items() if k != "priority"}
            extra_parts += [f"{k}={v}" for k, v in other_params.items()]
            if extra_parts:
                line += f" ({', '.join(extra_parts)})"
            lines.append(line)

        if self.custom_pins:
            lines.append("  Custom pins:")
            for pin in self.custom_pins:
                lines.append(f"    ({pin.label}) {pin.summary}")

        has_llm_prefs = bool(self.llm_custom_preferences)
        if has_llm_prefs:
            lines.append("  Custom LLM preferences:")
            for p in self.llm_custom_preferences:
                status = "ON" if p.enabled else "OFF"
                desc = p.description[:60] + ("..." if len(p.description) > 60 else "")
                lines.append(f"    ({p.label}) [{status}] {desc} (priority: {p.priority})")

        has_named_prio = any(p.name in _PRIO_DEFAULTS for p in active)
        if has_named_prio or has_llm_prefs:
            lines.append(
                "  [Priority scale: 3 = user custom (highest) · "
                "2 = standard_timeline · 1 = credit_balancing]"
            )

        return "\n".join(lines) if lines else "No preferences active"
