"""Study plan navigation: solving, display, LLM extraction, and preference actions.

Program-agnostic — credit values for display are read directly from the grounded
clingo Control object; module-code existence checks (pins, custom ASP fragments)
validate against the program's ChromaDB module collection instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace as dc_replace
from datetime import datetime

from clingo import Control, Function, Number
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from asp import (
    assign_core,
    assign_requested_courses,
    build_initial_program,
    ground_program,
)
from asp.preferences.preferences import apply_preferences, collect_pin_assumptions
from core.extraction import (
    _anthropic_client,
    _call_llm,
    _call_llm_unstructured,
    _get_model,
    _parse_json,
)
from core.logging import is_verbose
from core.state import VALID_PREFERENCES, PreferenceState, StudentState
from db.chroma.chroma_store import module_exists
from utils.utils import load_config, read_yaml

console = Console()

# Preferences that support adjustable optimization priorities (1–10).
# All others (diversity, n_semesters, n_plans) and custom pins
# either operate outside the optimizer or are hard constraints.
_PRIO_PREFS = frozenset({"standard_timeline", "credit_balancing"})


def _user_input(prompt: str = "") -> str:
    return input(prompt).strip()


# ---------------------------------------------------------------------------
# Custom ASP preference: LLM context loading
# ---------------------------------------------------------------------------


def _load_lp_context(program_id: str) -> str:
    """Load and concatenate the three .lp files as LLM context for the generator.

    Called once per navigator session and cached in a local variable.
    """
    paths = [
        "asp/lp/encoding.lp",
        "asp/preferences/preferences.lp",
        f"asp/study_program_instances/{program_id}.lp",
    ]
    parts = []
    for path in paths:
        with open(path) as f:
            content = f.read()
        parts.append(f"=== {path} ===\n{content}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Custom ASP preference: fragment validation
# ---------------------------------------------------------------------------


def validate_custom_fragment(asp_code: str) -> tuple[bool, str]:
    """Syntax-check an ASP fragment using clingo's parser. Returns (ok, error_msg)."""
    try:
        from clingo.ast import parse_string

        parse_string(asp_code, lambda _: None)
        return True, ""
    except RuntimeError as e:
        return False, str(e)


def validate_custom_fragment_modules(
    referenced_modules: list[str], modules_collection: str
) -> tuple[bool, str]:
    """Check the LLM's self-reported module references against the program's
    real module vocabulary (via ChromaDB exact-match lookup). Returns (ok, err);
    err is a bare comma-joined list of unknown module codes when not ok, empty
    string when ok."""
    unknown = sorted(
        m for m in set(referenced_modules) if not module_exists(modules_collection, m)
    )
    if unknown:
        return False, ", ".join(unknown)
    return True, ""


# ---------------------------------------------------------------------------
# Custom ASP preference: LLM generator calls (with prompt caching)
# ---------------------------------------------------------------------------


def _build_generator_system_prompt(slot: int, lp_context: str) -> str:
    """Build the generator system prompt with slot substituted and lp_context appended."""
    prompts = read_yaml("prompts.yaml")
    template = prompts["prompts"]["generate_custom_preference"]
    base = template.replace("{SLOT}", str(slot))
    return f"{base}\n{lp_context}"


def _call_generator_llm(system_prompt: str, user_message: str, schema: dict) -> dict:
    """Call Anthropic API for ASP generation with prompt caching on the system prompt."""
    response = _anthropic_client.messages.create(
        model=_get_model(),
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_message}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    return json.loads(response.content[0].text)


def generate_custom_asp_preference(
    normalized_description: str,
    original_user_text: str,
    slot: int,
    lp_context: str,
) -> dict:
    """Pass 2: generate a custom ASP fragment for the given description and slot."""
    prompts = read_yaml("prompts.yaml")
    schema = prompts["schemas"]["generate_custom_preference"]
    system_prompt = _build_generator_system_prompt(slot, lp_context)
    user_message = (
        f"PREFERENCE DESCRIPTION:\n{normalized_description}\n\n"
        f"ORIGINAL USER TEXT:\n{original_user_text}\n\n"
        f"Generate slot N={slot} fragment. Use llm_pref_active({slot}) as the guard."
    )
    return _call_generator_llm(system_prompt, user_message, schema)


def _retry_custom_asp_generation(
    asp_code: str,
    error_text: str,
    slot: int,
    lp_context: str,
    *,
    error_label: str = "PARSE ERROR",
    fix_instruction: str = (
        "Fix the syntax error and return a corrected fragment. "
        "If the error reveals the preference is not expressible in clingo, set rejected: true."
    ),
) -> dict:
    """Pass 2 retry: repair a previously generated fragment. error_label and
    fix_instruction let callers reframe the problem (syntax vs. unknown
    module references) without duplicating the LLM-call plumbing; defaults
    reproduce the original syntax-error framing exactly."""
    prompts = read_yaml("prompts.yaml")
    schema = prompts["schemas"]["generate_custom_preference"]
    system_prompt = _build_generator_system_prompt(slot, lp_context)
    user_message = (
        f"ORIGINAL FRAGMENT:\n{asp_code}\n\n"
        f"{error_label}:\n{error_text}\n\n"
        f"{fix_instruction}"
    )
    return _call_generator_llm(system_prompt, user_message, schema)


def _check_fragment(gen_result: dict, modules_collection: str) -> tuple[bool, str, str]:
    """Returns (ok, kind, err); kind is 'syntax' or 'modules', '' if ok."""
    ok, err = validate_custom_fragment(gen_result.get("asp_code", ""))
    if not ok:
        return False, "syntax", err
    ok, err = validate_custom_fragment_modules(
        gen_result.get("referenced_modules", []), modules_collection
    )
    if not ok:
        return False, "modules", err
    return True, "", ""


def _validate_and_repair_custom_fragment(
    gen_result: dict,
    slot: int,
    lp_context: str,
    modules_collection: str,
    telemetry: dict | None = None,
) -> dict | None:
    """Validate a generated fragment (syntax, then self-reported module
    references), attempting exactly one LLM repair pass on the first
    failure of either kind. Owns all console messaging for this subflow.

    Returns the accepted gen_result dict (original or repaired), or None if
    rejected — a rejection message has already been printed in that case.

    telemetry, when a dict is passed, is filled with the validity/repair
    trace: first_pass_ok, first_pass_failure ('syntax' | 'modules' | ''),
    first_pass_error, repair_attempted, repairs, outcome, outcome_detail.
    It exists so the evaluation harness can report first-pass syntactic
    validity and re-run counts, which are otherwise only printed to the
    console. Production callers pass None and behaviour is unchanged.
    """

    def _note(**kv) -> None:
        if telemetry is not None:
            telemetry.update(kv)

    ok, kind, err = _check_fragment(gen_result, modules_collection)
    _note(
        first_pass_ok=ok,
        first_pass_failure=kind,
        first_pass_error=err,
        repair_attempted=False,
        repairs=0,
        outcome="accepted" if ok else "",
        outcome_detail="",
    )
    if ok:
        return gen_result

    asp_code = gen_result.get("asp_code", "")
    if kind == "syntax":
        console.print("[dim]  Parse error in generated ASP, retrying...[/dim]")
        if is_verbose():
            console.print(f"[dim]  Error: {err}[/dim]")
        retry_kwargs: dict = {}
    else:
        console.print(
            "[dim]  Unknown module reference(s) in generated ASP, retrying...[/dim]"
        )
        if is_verbose():
            console.print(f"[dim]  Unknown module(s): {err}[/dim]")
        retry_kwargs = {
            "error_label": "UNKNOWN MODULES",
            "fix_instruction": (
                "These module codes reported in referenced_modules do not exist "
                "in this program (see the program context above for the real "
                "module codes). Correct the fragment and referenced_modules to "
                "use only real module codes, or drop the literal referencing "
                "them. If the preference cannot be expressed using only real "
                "module codes, set rejected: true."
            ),
        }

    _note(repair_attempted=True, repairs=1)

    try:
        retry_result = _retry_custom_asp_generation(
            asp_code, err, slot, lp_context, **retry_kwargs
        )
    except Exception as e:
        console.print(f"[red]Retry LLM error: {e}[/red]")
        _note(outcome="repair_error", outcome_detail=str(e))
        return None

    if retry_result.get("rejected"):
        console.print(
            f"[yellow]Custom preference rejected after retry: "
            f"{retry_result.get('reject_reason', 'Unspecified reason')}[/yellow]"
        )
        _note(
            outcome="rejected_after_repair",
            outcome_detail=retry_result.get("reject_reason", ""),
        )
        return None

    ok, post_kind, post_err = _check_fragment(retry_result, modules_collection)
    if not ok:
        if post_kind == "syntax":
            console.print(
                f"[red]Custom preference rejected: "
                f"parse error after retry ({post_err})[/red]"
            )
        else:
            console.print(
                f"[red]Custom preference rejected: "
                f"unknown module reference(s) after retry: {post_err}[/red]"
            )
        _note(outcome=f"invalid_after_repair_{post_kind}", outcome_detail=post_err)
        return None

    _note(outcome="repaired")
    return retry_result


# ---------------------------------------------------------------------------
# Custom ASP preference: reground with replay
# ---------------------------------------------------------------------------


def _replay_custom_llm_fragments(ctl: Control, pref_state: PreferenceState) -> None:
    """Replay all LLM custom preference fragments into ctl in slot order."""
    for p in sorted(pref_state.llm_custom_preferences, key=lambda p: p.slot):
        ctl.add(f"llm_pref_{p.slot}", [], p.asp_code)
        ctl.ground([(f"llm_pref_{p.slot}", [])])


# ---------------------------------------------------------------------------
# Module data extraction from ctl
# ---------------------------------------------------------------------------


def extract_module_data(ctl: Control) -> dict[str, int]:
    """Read module credits from a grounded Control object.

    Returns:
        credits: {module_code: credit_value}

    Reads map(c, M, V) for credits.
    """
    credits: dict[str, int] = {}
    for atom in ctl.symbolic_atoms.by_signature("map", 3):
        sym = atom.symbol
        if sym.arguments[0].name == "c":
            mod = sym.arguments[1].name
            if mod:
                credits[mod] = sym.arguments[2].number

    return credits


# ---------------------------------------------------------------------------
# Plan extraction and solving
# ---------------------------------------------------------------------------

Plan = list[tuple[str, int]]  # [(module_code, semester), ...]


def extract_plan(model) -> Plan:
    """Extract (module_code, semester) pairs from a clingo model."""
    plan: Plan = []
    for atom in model.symbols(atoms=True):
        if atom.name == "in" and len(atom.arguments) == 2:
            second = atom.arguments[1]
            if second.name == "s" and len(second.arguments) == 1:
                try:
                    sem = second.arguments[0].number
                    mod = atom.arguments[0].name
                    if mod:
                        plan.append((mod, sem))
                except RuntimeError:
                    continue
    plan.sort(key=lambda x: x[1])
    return plan


def solve_optimized(
    ctl: Control,
    n_plans: int = 3,
    assumptions: list = (),
) -> list[tuple[list[int], Plan]]:
    """Enumerate up to n_plans optimal plans (minimizing active weak constraints).

    Returns up to n_plans (cost_vector, plan) tuples for display.
    Cancels enumeration after collecting n_plans models — fast.
    Use solve_brave_cautious separately for accurate brave/cautious consequences.
    """
    ctl.configuration.solve.enum_mode = "auto"
    ctl.configuration.solve.models = "0"
    ctl.configuration.solve.opt_mode = "optN"

    results: list[tuple[list[int], Plan]] = []

    with ctl.solve(assumptions=list(assumptions), yield_=True) as handle:
        for model in handle:
            if model.optimality_proven:
                plan = extract_plan(model)
                results.append((list(model.cost), plan))
                if len(results) >= n_plans:
                    handle.cancel()
                    break

    return results


def solve_brave_cautious(
    ctl: Control,
    assumptions: list = (),
) -> tuple[set, set]:
    """Compute brave and cautious consequences over all optimal models.

    Two native clingo solve calls with enum_mode="brave"/"cautious" and
    opt_mode="optN". Clingo terminates early once the sets stabilize.

    Toggles the show_in_placements external (declared in encoding.lp) so
    that in(M, s(I)) atoms appear in the consequence set output. Resets
    the external on exit so other phases are unaffected.

    Returns (brave, cautious) as sets of (module, semester) tuples.
    """

    brave: set[tuple[str, int]] = set()
    cautious: set[tuple[str, int]] = set()

    def _plan_from_shown(model) -> Plan:
        plan: Plan = []
        for sym in model.symbols(shown=True):
            if sym.name != "in" or len(sym.arguments) != 2:
                continue
            second = sym.arguments[1]
            if second.name != "s" or len(second.arguments) != 1:
                continue
            try:
                sem = second.arguments[0].number
                mod = sym.arguments[0].name
                if mod:
                    plan.append((mod, sem))
            except RuntimeError:
                continue
        return plan

    ctl.assign_external(Function("show_in_placements"), True)
    try:
        for mode in ("brave", "cautious"):
            ctl.configuration.solve.enum_mode = mode
            ctl.configuration.solve.models = "0"
            ctl.configuration.solve.opt_mode = "optN"
            last_plan: Plan | None = None
            with ctl.solve(assumptions=list(assumptions), yield_=True) as handle:
                for model in handle:
                    last_plan = _plan_from_shown(model)
            if last_plan:
                if mode == "brave":
                    brave = set(last_plan)
                else:
                    cautious = set(last_plan)
    finally:
        ctl.assign_external(Function("show_in_placements"), False)
        ctl.configuration.solve.enum_mode = "auto"

    return brave, cautious


def solve_diverse(
    ctl: Control,
    k: int = 3,
    assumptions: list = (),
) -> tuple[list[Plan], set, set]:
    """Generate k diverse plans using iterative multi-shot solving.

    Diversity is measured by (module, semester) placement pairs: two plans
    are "different" if the same module ends up in a different semester.

    prev(J, M, S) externals are pre-declared in diversity_external.lp and
    assigned True as plans accumulate. After all rounds they are reset to
    False so the ctl is clean for the next solve call — no rebuild needed.
    """
    ctl.configuration.solve.enum_mode = "auto"
    ctl.configuration.solve.models = "1"
    ctl.configuration.solve.opt_mode = "optN"

    # Activate the diversity guard
    ctl.assign_external(Function("diversity_schedule"), True)

    plans: list[Plan] = []
    brave: set[tuple[str, int]] = set()
    cautious: set[tuple[str, int]] | None = None

    for _ in range(k):
        # Activate prev externals for all plans found so far
        for j, plan in enumerate(plans):
            for mod, sem in plan:
                ctl.assign_external(
                    Function("prev", [Number(j), Function(mod), Number(sem)]), True
                )

        best_plan = None
        with ctl.solve(assumptions=list(assumptions), yield_=True) as handle:
            for model in handle:
                best_plan = extract_plan(model)

        if best_plan is None:
            break
        atoms = set(best_plan)
        brave |= atoms
        cautious = atoms if cautious is None else cautious & atoms
        plans.append(best_plan)

    # Reset all prev and diversity externals so ctl is clean for the next session
    for atom in ctl.symbolic_atoms:
        if atom.is_external and atom.symbol.name == "prev":
            ctl.assign_external(atom.symbol, False)
    ctl.assign_external(Function("diversity_schedule"), False)

    return plans, brave, cautious or set()


# ---------------------------------------------------------------------------
# UX guides
# ---------------------------------------------------------------------------


def _print_normal_mode_guide() -> None:
    """Print a concise action guide for preference navigation mode."""
    console.print(
        "\n[bold]Here you can do the following:[/bold]\n"
        "  [cyan]·[/cyan] Add [cyan]pins[/cyan] to fix a module to (or away from) a semester"
        " — e.g. [italic]'put FM1 in semester 2'[/italic] or [italic]'no PM3 in semester 1'[/italic]\n"
        "  [cyan]·[/cyan] Or describe [cyan]any custom constraint[/cyan] in plain language"
        " — e.g. [italic]'at most 2 AM modules per semester'[/italic]"
        " or [italic]'no PMs in semester 3'[/italic]\n"
        "  [cyan]·[/cyan] [cyan]Select a plan by number[/cyan] if it matches your vision"
        " — type [bold]'done'[/bold] to finish with it, or keep refining first\n"
        "  [cyan]·[/cyan] [cyan]Remove or adjust[/cyan] any active preferences"
        " — e.g. [italic]'remove timeline preference'[/italic] / [italic]'reset all'[/italic]\n"
        "  [cyan]·[/cyan] Type [bold]'?'[/bold] for the full preference guide\n"
    )


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def display_solution_space(
    brave: set[tuple[str, int]],
    cautious: set[tuple[str, int]],
    n_semesters: int,
) -> None:
    """Print a Rich table showing always/sometimes module placements per semester.

    always   = cautious consequences (module in every optimal plan for that semester)
    sometimes = brave-only (module in some but not all optimal plans for that semester)

    Suppressed when brave is empty (no plans found).
    """
    if not brave:
        return

    from rich.console import Console
    from rich.table import Table

    table = Table(
        title="Solution space (based on current preferences)",
        caption="Fixed: in every optimal plan · Possible: in some but not all optimal plans",
        border_style="dim",
    )
    table.add_column("Semester", style="bold white", justify="center", no_wrap=True)
    table.add_column("Fixed", style="green")
    table.add_column("Possible", style="cyan")

    for sem in range(1, n_semesters + 1):
        always = sorted(mod for mod, s in cautious if s == sem)
        sometimes = sorted(mod for mod, s in brave - cautious if s == sem)
        if not always and not sometimes:
            continue
        table.add_row(
            f"Sem {sem}",
            ", ".join(always) if always else "—",
            ", ".join(sometimes) if sometimes else "—",
        )

    Console().print(table)


def _display_plans_table(
    plans: list[Plan],
    ctl: Control,
    state,
    title: str,
    costs: list[list[int]] | None,
    brave: set[tuple[str, int]],
    cautious: set[tuple[str, int]],
) -> None:
    from rich.console import Console
    from rich.table import Table

    credits_map = extract_module_data(ctl)

    all_sems = [sem for plan in plans for _, sem in plan]
    max_sem = max(all_sems) if all_sems else (state.n_semesters or 1)

    legend = "[blue]■ Fixed[/blue]  (in every plan)     [yellow]■ Possible[/yellow]  (in some plans)"
    table = Table(title=title, caption=legend, border_style="dim")
    table.add_column("Semester", style="bold white", justify="center", no_wrap=True)

    for idx in range(len(plans)):
        cost_str = f"\ncost={costs[idx]}" if costs else ""
        table.add_column(f"Plan {idx + 1}{cost_str}", justify="left")

    for sem in range(1, max_sem + 1):
        done = " [dim](done)[/dim]" if sem < (state.current_semester or 1) else ""
        sem_cell = f"Sem {sem}{done}"
        row = [sem_cell]
        for plan in plans:
            sem_groups: dict[int, list[str]] = {}
            for mod, s in plan:
                sem_groups.setdefault(s, []).append(mod)
            mods = sorted(sem_groups.get(sem, []))
            if not mods:
                row.append("[dim](free)[/dim]")
            else:
                total = sum(credits_map.get(m, 0) for m in mods)
                parts = []
                for mod in mods:
                    if (mod, sem) in cautious:
                        parts.append(f"[blue]{mod}[/blue]")
                    else:
                        parts.append(f"[yellow]{mod}[/yellow]")
                row.append(", ".join(parts) + f"  ({total} cr)")
        table.add_row(*row)

    Console().print(table)


def display_plans(
    plans: list[Plan],
    ctl: Control,
    state,
    title: str,
    costs: list[list[int]] | None = None,
    brave: set[tuple[str, int]] | None = None,
    cautious: set[tuple[str, int]] | None = None,
) -> None:
    """Print plans with per-semester credit totals. Reads credits from ctl."""
    if len(plans) <= 3 and brave is not None and cautious is not None:
        _display_plans_table(plans, ctl, state, title, costs, brave, cautious)
        return

    credits_map = extract_module_data(ctl)
    width = 62

    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}")

    for idx, plan in enumerate(plans):
        sem_groups: dict[int, list[str]] = {}
        for mod, sem in plan:
            sem_groups.setdefault(sem, []).append(mod)

        cost_str = f"  cost={costs[idx]}" if costs else ""
        print(f"\n  Plan {idx + 1}{cost_str}")

        max_sem = max(sem_groups) if sem_groups else (state.n_semesters or 1)
        for sem in range(1, max_sem + 1):
            done = " [completed]" if sem < (state.current_semester or 1) else ""
            if sem in sem_groups:
                mods = sorted(sem_groups[sem])
                total = sum(credits_map.get(m, 0) for m in mods)
                print(f"    Sem {sem} ({total:3d} cr){done}: {', '.join(mods)}")
            else:
                print(f"    Sem {sem} (  0 cr){done}: (Free)")


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------


def build_llm_context(
    pref_state,
    state,
    prev_user_msg: str | None,
    prev_pref_summary: str | None,
) -> str:
    """Build context string injected into the LLM system prompt."""
    parts = [
        f"Student: semester {state.current_semester}, "
        f"planning {pref_state.get_n_semesters() or state.n_semesters} semesters.",
        f"\nCURRENT PREFERENCE STATE:\n{pref_state.summary()}",
    ]
    if prev_user_msg and prev_pref_summary:
        parts.append(
            f"\nPREVIOUS INTERACTION:\n"
            f'User said: "{prev_user_msg}"\n'
            f"Preference state was:\n{prev_pref_summary}"
        )
    return "\n".join(parts)


def extract_preference_intent(context: str, user_input: str) -> dict:
    """Call LLM to extract preference actions from natural language."""
    prompts = read_yaml("prompts.yaml")
    system_prompt = prompts["prompts"]["navigate_preferences"]
    full_prompt = (
        system_prompt
        + f"\n\n---\nCONTEXT:\n{context}"
        + "\n\nRespond with a JSON object only, no other text."
    )
    result = _call_llm_unstructured(full_prompt, user_input)
    if not isinstance(result.get("actions"), list):
        result["actions"] = []
    return result


# ---------------------------------------------------------------------------
# Pin → assumptions conversion
# ---------------------------------------------------------------------------


def pin_to_assumptions(
    constraints: list[dict],
    modules_collection: str | None = None,
) -> list[dict]:
    """Convert structured pin constraints to assumption dicts.

    Each dict: {"module": str, "semester": int, "positive": bool}
    positive=True  → in(module, s(semester)) must be True  (force placement)
    positive=False → in(module, s(semester)) must be False (forbid placement)

    Both map to native clingo solve assumptions, so both are hard: an
    unsatisfiable pin yields UNSAT rather than a silently violated plan.
    Pins cover a single module in a single semester. Broader negatives
    ("no PMs at all", "never take PM2 in any semester") are routed to the
    LLM custom-preference generator instead.

    Module codes are validated against ChromaDB when modules_collection is
    given — clingo ignores assumptions on atoms that do not exist, so an
    unvalidated typo would produce a pin that shows up in the UI and does
    nothing. Raises ValueError for unknown modules or missing fields.
    """

    def _module_of(c: dict, ctype: str) -> str:
        mod = c.get("module")
        if not mod:
            raise ValueError(f"{ctype} requires 'module'")
        if modules_collection is not None and not module_exists(
            modules_collection, mod
        ):
            raise ValueError(f"Unknown module '{mod}'")
        return mod

    assumptions = []
    for c in constraints:
        ctype = c["type"]
        sem = c["semester"]

        if ctype == "pin_module_semester":
            mod = _module_of(c, ctype)
            assumptions.append({"module": mod, "semester": sem, "positive": True})

        elif ctype == "exclude_module_semester":
            mod = _module_of(c, ctype)
            assumptions.append({"module": mod, "semester": sem, "positive": False})

        else:
            raise ValueError(f"Unknown constraint type: {ctype}")

    return assumptions


# ---------------------------------------------------------------------------
# Action application
# ---------------------------------------------------------------------------


def apply_llm_actions(
    pref_state,
    actions: list[dict],
    modules_collection: str | None = None,
) -> list[str]:
    """Apply LLM-extracted actions to preference state. Returns status messages."""
    messages = []

    for act in actions:
        action = act.get("action")

        if action == "enable":
            pref_name = act.get("preference")
            if pref_name:
                try:
                    if pref_name in pref_state.preferences:
                        pref_state.toggle(pref_name, True)
                    else:
                        pref_state.add(pref_name)
                    messages.append(f"Enabled {pref_name}")
                except ValueError as e:
                    messages.append(f"Error: {e}")

        elif action == "disable":
            pref_name = act.get("preference")
            if pref_name and pref_name in pref_state.preferences:
                pref_state.toggle(pref_name, False)
                messages.append(f"Disabled {pref_name}")

        elif action == "add_pin":
            summary = act.get("pin_summary", "Custom constraint")
            c_type = act.get("constraint_type", "")
            c_sem = act.get("constraint_semester")
            if c_type and c_sem is not None and c_sem >= 1:
                constraint = {
                    "type": c_type,
                    "module": act.get("constraint_module") or None,
                    "semester": c_sem,
                }
                try:
                    assumptions = pin_to_assumptions([constraint], modules_collection)
                    label = pref_state.add_custom_pin(summary, assumptions)
                    messages.append(f"Added pin ({label}): {summary}")
                except (ValueError, KeyError) as e:
                    messages.append(f"Error creating pin: {e}")
            else:
                messages.append("No constraints provided for pin")

        elif action == "remove_pin":
            label = act.get("pin_label")
            if label and pref_state.remove_custom_pin(label):
                messages.append(f"Removed pin ({label})")
            else:
                messages.append(f"Pin ({label}) not found")

        elif action == "set_n_semesters":
            value = act.get("value")
            if value is not None:
                n = int(value)
                if n < 1 or n > 12:
                    messages.append(f"Invalid n_semesters={n} (must be 1-12)")
                else:
                    pref_state.add("n_semesters", value=n)
                    messages.append(f"Set n_semesters to {n}")

        elif action == "set_n_plans":
            value = act.get("value")
            if value is not None:
                n = int(value)
                if n < 1 or n > 20:
                    messages.append(f"Invalid n_plans={n} (must be 1-20)")
                else:
                    pref_state.add("n_plans", value=n)
                    messages.append(f"Set n_plans to {n}")

        elif action == "clear_all":
            pref_state.clear()
            messages.append("Cleared all preferences and pins")

        elif action == "set_priority":
            pref_name = act.get("preference")
            value = act.get("priority_value")
            if not pref_name:
                messages.append("set_priority requires a preference name")
            elif value is None:
                messages.append("set_priority requires a priority_value (1–10)")
            else:
                v = int(value)
                if not (1 <= v <= 10):
                    messages.append(f"Priority must be between 1 and 10 (got {v})")
                elif re.match(r"^c\d+$", pref_name):
                    # Custom LLM preference: rewrite ASP code and flag for reground
                    if pref_state.set_llm_custom_priority(pref_name, v):
                        messages.append(
                            f"Set custom preference ({pref_name}) priority to {v}"
                        )
                    else:
                        messages.append(f"Custom preference ({pref_name}) not found")
                elif pref_name not in _PRIO_PREFS:
                    messages.append(
                        f"Priority not supported for '{pref_name}' — use standard_timeline, "
                        f"credit_balancing, or a custom preference label (c1, c2, ...)"
                    )
                else:
                    auto_enabled = pref_name not in pref_state.preferences
                    if auto_enabled:
                        # Setting a priority on an inactive preference enables it.
                        pref_state.add(pref_name)
                    pref_state.preferences[pref_name].params["priority"] = v
                    if auto_enabled:
                        messages.append(f"Enabled {pref_name} and set priority to {v}")
                    else:
                        messages.append(f"Set {pref_name} priority to {v}")

        elif action == "remove_llm_custom":
            label = act.get("llm_label")
            if label and pref_state.remove_llm_custom(label):
                messages.append(f"Removed custom preference ({label})")
            else:
                messages.append(f"Custom preference ({label}) not found")

        elif action == "toggle_llm_custom":
            label = act.get("llm_label")
            enabled = act.get("enabled", True)
            if label and pref_state.toggle_llm_custom(label, enabled):
                state_str = "enabled" if enabled else "disabled"
                messages.append(f"Custom preference ({label}) {state_str}")
            else:
                messages.append(f"Custom preference ({label}) not found")

        else:
            messages.append(f"Unknown action: {action}")

    return messages


# ---------------------------------------------------------------------------
# Phase 4: Preference navigator
# ---------------------------------------------------------------------------


def _print_preferences_guide() -> None:
    """Print a short guide on the kinds of preferences users can express."""
    guide = Table.grid(padding=(0, 2))
    guide.add_column(style="bold cyan", no_wrap=True)
    guide.add_column()

    guide.add_row("Timeline", '"I want to follow a standard degree timeline"')
    guide.add_row("Balance", '"Spread credits evenly across semesters"')
    guide.add_row(
        "Limit credits",
        '"Keep semester 3 light" / "max 15 credits in sem 2" / "make sem 3 free"',
    )
    guide.add_row("Diversity", '"Show me plans that are different from one another"')
    guide.add_row("Pin a module", '"Put PM3 in semester 3" / "no PM2 in semester 1"')
    guide.add_row("Remove a pin", '"Remove pin a" / "clear all pins"')
    guide.add_row("No. of plans", '"Show me 3 plans" / "give me 2 options"')
    guide.add_row("Reset", '"Reset preferences" / "start over"')
    guide.add_row("", "")
    guide.add_row("[bold]Custom preferences[/bold]", "")
    guide.add_row(
        "  Describe anything",
        '"at most 2 AM modules per semester"'
        ' / "MSc thesis in my last semester"'
        ' / "avoid IM and MSc in the same semester"',
    )
    guide.add_row(
        "  Manage by label",
        '"disable c1" / "enable c1" / "remove c1"'
        " — label shown in preference summary (c1, c2, …)",
    )
    guide.add_row("", "")
    guide.add_row("[bold]Optimization priorities[/bold]", "")
    guide.add_row(
        "  Set priority",
        '"Set timeline priority to 5" / "make balancing more important"',
    )
    guide.add_row(
        "  Swap priorities",
        '"Switch priorities of timeline and balance"',
    )
    guide.add_row(
        "  How it works",
        "Integers 1–10. Higher = optimized first (strictly before lower-priority preferences). Defaults: timeline=2, balance=1.",
    )
    guide.add_row(
        "  Not applicable",
        "Diversity and custom pins do not use this priority system — they work independently.",
    )

    console.print()
    console.print(
        Panel(
            guide,
            title="[bold]Preference guide[/bold]",
            border_style="dim",
            padding=(0, 1),
        )
    )
    console.print(
        "[dim]  Type any of the above in your own words — including arbitrary custom constraints."
        " The system will generate them automatically.[/dim]\n"
    )


def plan_preference_navigator(state: StudentState, phase2_ctl) -> StudentState:
    """Phase 4: Interactive preference-driven study plan navigator.

    Reuses the Phase 2 ctl directly. Rebuilds only when n_semesters changes.
    All other preference changes (pins, diversity, soft constraints) are
    applied without regrounding via externals or solve-time assumptions.
    """
    console.print()
    console.print(
        Panel("[bold cyan]Study Plan Navigator[/bold cyan]", border_style="cyan")
    )
    console.print(
        "[dim]  Tip: Beyond pins and standard preferences, you can describe any custom"
        " constraint in plain language — e.g. [italic]'at most 2 AM modules per semester'[/italic]."
        " Type [bold]'?'[/bold] for the full guide.[/dim]"
    )

    pref_state = PreferenceState()
    pref_state.apply_defaults()

    ctl = phase2_ctl
    ctl.assign_external(Function("lock_history"), True)
    # Tracks the n_semesters value the current ctl was built for.
    # LLM custom fragments are added incrementally and do not update this.
    _last_n: int = state.n_semesters

    # Load .lp context once for the custom preference generator
    lp_context = _load_lp_context(state.program_id)

    def _rebuild(target_n: int) -> None:
        nonlocal ctl, _last_n
        build_state = (
            state
            if target_n == state.n_semesters
            else dc_replace(state, n_semesters=target_n)
        )
        program = build_initial_program(build_state)
        new_ctl = ground_program(program)
        assign_core(new_ctl, build_state)
        assign_requested_courses(new_ctl, build_state.selected_courses)
        new_ctl.assign_external(Function("lock_history"), True)
        pref_state._fragments_loaded = False
        _replay_custom_llm_fragments(new_ctl, pref_state)
        ctl = new_ctl
        _last_n = target_n

    def _solve() -> tuple[list, list | None, set, set]:
        target_n = pref_state.get_n_semesters() or state.n_semesters
        if target_n != _last_n:
            _rebuild(target_n)
        apply_preferences(ctl, pref_state)

        pin_assumptions = collect_pin_assumptions(pref_state)
        n_plans = pref_state.get_n_plans() or 3

        if pref_state.has_diversity():
            console.print(f"[dim]Solving {n_plans} diverse plans...[/dim]")
            plans, _div_brave, _div_cautious = solve_diverse(
                ctl, k=n_plans, assumptions=pin_assumptions
            )
            if not plans:
                return [], None, set(), set()
            console.print("[dim]Computing solution space...[/dim]")
            brave, cautious = solve_brave_cautious(ctl, assumptions=pin_assumptions)
            return plans, None, brave, cautious
        else:
            console.print("[dim]Solving optimized plans...[/dim]")
            results = solve_optimized(ctl, n_plans=n_plans, assumptions=pin_assumptions)
            if not results:
                return [], None, set(), set()
            console.print("[dim]Computing solution space...[/dim]")
            brave, cautious = solve_brave_cautious(ctl, assumptions=pin_assumptions)
            return [p for _, p in results], [c for c, _ in results], brave, cautious

    # Module vocabulary for existence checks (program-specific, doesn't change across solves)
    modules_collection = load_config()["modules_collection"]

    # Initial solve
    console.print(f"\n  Preferences:\n{pref_state.summary()}")
    plans, costs, brave, cautious = _solve()
    if plans:
        display_solution_space(
            brave, cautious, pref_state.get_n_semesters() or state.n_semesters
        )
        display_plans(
            plans,
            ctl,
            state,
            "Study plans (initial)",
            costs=costs,
            brave=brave,
            cautious=cautious,
        )
        _print_normal_mode_guide()
    else:
        console.print("[red]No valid plans found with initial preferences.[/red]")

    prev_user_msg: str | None = None
    prev_pref_summary: str | None = None
    # Index into `plans` of the plan the user picked by number, or None.
    # Reset on every re-solve: the numbering refers to the plans currently
    # on screen, so a stale index would finalize the wrong plan.
    selected_idx: int | None = None

    while True:
        console.print(f"\n  Preferences:\n{pref_state.summary()}")
        if selected_idx is not None:
            console.print(f"  [Selected: plan {selected_idx + 1}]")

        try:
            text = _user_input(
                "\nYou (number to select a plan · describe preferences or pins"
                " · '?' for help · 'done' to finish): "
            )
        except (EOFError, KeyboardInterrupt):
            break

        if not text:
            continue
        if text.lower() in ("done", "exit", "quit"):
            if plans:
                idx = selected_idx if selected_idx is not None else 0
                state.study_plan = plans[idx]
            break

        if text.strip().lower() in ("?", "help"):
            _print_preferences_guide()
            continue

        # Plan selection by number — records the choice, does not finalize.
        # 'done' turns the recorded choice into the final study plan; until
        # then the user can keep refining preferences.
        if text.strip().isdigit():
            idx = int(text.strip()) - 1
            if plans and 0 <= idx < len(plans):
                selected_idx = idx
                console.print(
                    f"\n[cyan]Plan {idx + 1} selected — "
                    "type 'done' to finish, or keep refining.[/cyan]"
                )
            else:
                console.print(
                    f"[yellow]Enter a number between 1 and {len(plans) if plans else 0}.[/yellow]"
                )
            continue

        # LLM extraction
        prev_summary = pref_state.summary()
        context = build_llm_context(pref_state, state, prev_user_msg, prev_pref_summary)

        console.print("[dim]  Extracting preference intent...[/dim]")
        try:
            result = extract_preference_intent(context, text)
        except Exception as e:
            console.print(f"[red]LLM error: {e}[/red]")
            continue

        if is_verbose():
            console.print(f"[dim]  LLM result: {json.dumps(result, indent=2)}[/dim]")

        explanation = result.get("explanation", "")
        actions = result.get("actions", [])
        custom_asp_needed = result.get("custom_asp_needed", False)
        custom_description = result.get("custom_description")

        did_something = False

        if actions:
            console.print(f"  [dim]{explanation}[/dim]")
            messages = apply_llm_actions(
                pref_state, actions, modules_collection=modules_collection
            )
            for msg in messages:
                console.print(f"  → {msg}")
            did_something = True

        if custom_asp_needed and custom_description:
            if not did_something:
                console.print(f"  [dim]{explanation}[/dim]")

            slot = pref_state._llm_slot_counter + 1
            console.print("[dim]  Generating custom ASP preference...[/dim]")
            try:
                gen_result = generate_custom_asp_preference(
                    custom_description, text, slot, lp_context
                )
            except Exception as e:
                console.print(f"[red]Generator LLM error: {e}[/red]")
                gen_result = None

            if gen_result is not None:
                if gen_result.get("rejected"):
                    console.print(
                        f"[yellow]Cannot generate custom preference: "
                        f"{gen_result.get('reject_reason', 'Unspecified reason')}[/yellow]"
                    )
                else:
                    gen_result = _validate_and_repair_custom_fragment(
                        gen_result, slot, lp_context, modules_collection
                    )
                    asp_code = (
                        gen_result.get("asp_code", "") if gen_result else None
                    )

                    if asp_code:
                        label = pref_state.add_llm_custom(text, asp_code)
                        pref_slot = pref_state._llm_slot_counter

                        if is_verbose():
                            console.print(
                                f"[dim][LLM-CUSTOM] slot={pref_slot} "
                                f"label={label}\n--- asp_code ---\n"
                                f"{asp_code}\n--- end ---[/dim]"
                            )
                        ctl.add(f"llm_pref_{pref_slot}", [], asp_code)
                        ctl.ground([(f"llm_pref_{pref_slot}", [])])

                        console.print(
                            f"  → Added custom preference ({label}): "
                            f"{gen_result.get('explanation', custom_description)}"
                        )
                        did_something = True

        if did_something:
            if pref_state._pending_reground:
                console.print("[dim]  Applying priority change...[/dim]")
                _rebuild(pref_state.get_n_semesters() or state.n_semesters)
                pref_state._pending_reground = False

            if is_verbose():
                console.print(
                    f"[dim]  Preference state:\n{pref_state.summary()}[/dim]"
                )

            # Re-solve with updated preferences
            plans, costs, brave, cautious = _solve()
            selected_idx = None
            if plans:
                display_solution_space(
                    brave,
                    cautious,
                    pref_state.get_n_semesters() or state.n_semesters,
                )
                display_plans(
                    plans,
                    ctl,
                    state,
                    f"After: {explanation}",
                    costs=costs,
                    brave=brave,
                    cautious=cautious,
                )
                _print_normal_mode_guide()
            else:
                console.print(
                    "[red]No valid plans found. Consider removing constraints or pins.[/red]"
                )

        prev_user_msg = text
        prev_pref_summary = prev_summary

    return state
