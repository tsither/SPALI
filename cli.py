# cli.py

import argparse

import questionary
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from core.logging import is_brave, set_brave, set_verbose
from core.state import StudentState
from setup.build_db import get_available_programs, load_program_data
from utils.utils import load_config

load_dotenv()

console = Console()

# Define the workflow steps
STEPS = ["Core Info", "Completed Modules", "Semester Planner"]


def print_progress_panel(current_step: int):
    """
    Display a progress panel showing current step in the workflow.

    Args:
        current_step: 0-indexed current step number
    """
    parts = []
    for i, step in enumerate(STEPS):
        if i < current_step:
            # Completed steps - dim
            parts.append(f"[dim]✓ {step}[/dim]")
        elif i == current_step:
            # Current step - highlighted
            parts.append(f"[bold cyan]→ {step}[/bold cyan]")
        else:
            # Future steps - muted
            parts.append(f"[dim white]{step}[/dim white]")

    progress_text = "  ──  ".join(parts)

    panel = Panel(
        progress_text,
        title="[bold]Progress[/bold]",
        border_style="cyan",
        padding=(0, 2),
    )
    console.print(panel)
    console.print()


LOGO = r"""
 _   _ ____    ____  _             _         ____  _
| | | |  _ \  / ___|| |_ _   _  __| |_   _  |  _ \| | __ _ _ __  _ __   ___ _ __
| | | | |_) | \___ \| __| | | |/ _` | | | | | |_) | |/ _` | '_ \| '_ \ / _ \ '__|
| |_| |  __/   ___) | |_| |_| | (_| | |_| | |  __/| | (_| | | | | | | |  __/ |
 \___/|_|     |____/ \__|\__,_|\__,_|\__, | |_|   |_|\__,_|_| |_|_| |_|\___|_|
                                     |___/
"""


def print_logo():
    console.print(LOGO, style="bold white")


def print_core_info(state: StudentState):
    table = Table(title="Core Info", title_style="bold cyan", border_style="cyan")
    table.add_column("Current Semester", style="bold white")
    table.add_column("Bridge Modules", style="bold white")
    table.add_column("Planned Semesters", style="bold white")

    if not state.bridge_modules or state.bridge_modules == ["none"]:
        bridge_modules = ["None"]
    else:
        bridge_modules = [name for name, _ in state.bridge_modules]
    for i, bridge in enumerate(bridge_modules):
        if i == 0:
            table.add_row(str(state.current_semester), bridge, str(state.n_semesters))
        else:
            table.add_row("", bridge, "")

    console.print(table)


def select_program(program_arg: str | None) -> str:
    """Select a study program via CLI arg or interactive menu.

    Returns the program_id string.
    """
    available = get_available_programs()

    # If explicitly provided, validate and return
    if program_arg:
        if program_arg not in available:
            console.print(f"[red]Program '{program_arg}' not found.[/red]")
            console.print(f"Available: {', '.join(available)}")
            raise SystemExit(1)
        return program_arg

    # Auto-select if only one program
    if len(available) == 1:
        console.print(f"[dim]Using program: {available[0]}[/dim]\n")
        return available[0]

    if not available:
        console.print("[red]No programs found in setup/programs/.[/red]")
        console.print("Run: python -m setup.build_db --list")
        raise SystemExit(1)

    # Interactive menu
    choices = []
    for prog_id in available:
        try:
            data = load_program_data(prog_id)
            name = data.get("name", prog_id)
        except Exception:
            name = prog_id
        choices.append(questionary.Choice(title=f"{name}  ({prog_id})", value=prog_id))

    selected = questionary.select(
        "Select a study program:",
        choices=choices,
    ).ask()

    if selected is None:
        raise SystemExit(0)

    console.print()
    return selected


# The test fixture's courses are only offered in this semester, so -t pins the
# session to it unless the user names a semester explicitly with -s.
_TEST_SEMESTER = "wise2526"

_TEST_SELECTED_COURSES = [
    ("Advanced Natural Language Processing", ["bm1"]),
    ("Answer Set Programming - ASP", ["bm3"]),
    ("Phonological Cognition", ["am11"]),
    ("Bayesian Statistical Inference 1", ["am21"]),
]


def _build_test_state(program_id: str):
    """Build a pre-populated cogsys test state for semester 1, no bridge modules, 5 semesters."""
    from asp import assign_core, assign_requested_courses, build_initial_program, ground_program

    state = StudentState(
        program_id=program_id,
        current_semester=1,
        bridge_modules=[],
        n_semesters=5,
        core_info_confirmed=True,
        completed_modules_confirmed=True,
        plan_confirmed=True,
        selected_courses=_TEST_SELECTED_COURSES,
    )

    program = build_initial_program(state)
    ctl = ground_program(program)
    ctl = assign_core(ctl, state)
    ctl = assign_requested_courses(ctl, state.selected_courses)

    return state, ctl


def main():
    parser = argparse.ArgumentParser(
        description="UP Study Planner - AI-powered semester planning using ASP + LLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python cli.py                  interactive program selection
  python cli.py -p cogsys        run with CogSys program
  python cli.py -p cogsys -b     brave mode (select courses one at a time)
  python cli.py -v               verbose/debug output
  python cli.py -t               load test state, skip to preference navigation
""",
    )

    program_group = parser.add_argument_group("program")
    program_group.add_argument(
        "-p",
        "--program",
        type=str,
        default=None,
        metavar="ID",
        help="study program ID (e.g. 'cogsys'); shows interactive menu if omitted",
    )

    planner_group = parser.add_argument_group("planner mode")
    planner_group.add_argument(
        "-b",
        "--brave",
        action="store_true",
        help="brave mode: select courses one at a time using ASP brave consequences",
    )
    parser.add_argument(
        "-s",
        "--semester",
        type=str,
        default=None,
        metavar="SEM",
        help="semester to plan for (e.g. 'wise2627'); overrides config.yaml default",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="enable verbose debug output"
    )
    parser.add_argument(
        "-t", "--test", action="store_true", help="load test state and skip to preference navigation"
    )

    args = parser.parse_args()

    # Set global flags
    set_verbose(args.verbose)
    set_brave(args.brave)

    print_logo()

    # Select program and initialize config before importing flows
    program_id = select_program(args.program)
    # -t pins the semester to the one its fixture courses are offered in;
    # an explicit -s still wins so other semesters can be exercised.
    semester = args.semester
    if args.test and semester is None:
        semester = _TEST_SEMESTER
        console.print(
            f"[dim]Test state: pinning semester to {_TEST_SEMESTER}"
            " (use -s to override).[/dim]"
        )
    config = load_config(study_program=program_id, semester=semester)

    try:
        program_name = load_program_data(program_id).get("name", program_id)
    except Exception:
        program_name = program_id
    console.print(
        Panel(
            f"[bold white]Program:[/bold white] {program_name}  [dim]({program_id})[/dim]    "
            f"[bold white]Semester:[/bold white] {config['semester']}",
            border_style="cyan",
            padding=(0, 2),
        )
    )
    console.print()

    # Import flows after config is initialized (they use load_config() internally)
    from core.flows import (
        collect_completed_modules,
        collect_core_info,
        plan_semester_brave,
        plan_semester_opt,
    )
    from core.navigation import plan_preference_navigator

    if args.test:
        state, ctl = _build_test_state(program_id)
        console.print("[dim]Test state loaded — skipping to preference navigation.[/dim]\n")
        print_core_info(state)
        print_progress_panel(3)
        state = plan_preference_navigator(state, ctl)
        return

    state = StudentState(program_id=program_id)
    ctl = None

    # Run workflow phases
    while state.get_current_phase() < 3:
        print_progress_panel(state.get_current_phase())

        phase = state.get_current_phase()
        if phase == 0:
            state = collect_core_info(state)
            print_core_info(state)

        elif phase == 1:
            state = collect_completed_modules(state)
        elif phase == 2:
            if is_brave():
                state, ctl = plan_semester_brave(state)
            else:
                state, ctl = plan_semester_opt(state)

    # Show final summary
    print_progress_panel(3)  # All done

    # Phase 4: Preference-driven study plan navigator
    if ctl is not None:
        state = plan_preference_navigator(state, ctl)


if __name__ == "__main__":
    main()
