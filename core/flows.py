# core/flows.py
from collections import defaultdict

from rich.console import Console
from rich.table import Table

from asp import (
    assign_core,
    assign_requested_courses,
    build_initial_program,
    check_admissibility,
    display_available_courses,
    enumerate_assignments,
    get_all_courses,
    ground_program,
    print_assignments,
    print_available_courses,
    select_assignment,
    unassign_requested_courses,
)
from core.extraction import (
    canonicalize_modules,
    extract_completed_modules,
    extract_core_info,
    extract_course_intent,
)
from core.logging import ai_print
from core.state import StudentState
from db.chroma.chroma_store import get_module_credits
from utils.utils import load_config

console = Console()


def user_input(prompt: str = "") -> str:
    """Get user input."""
    return input(prompt).strip()


def print_completed_modules(completed_modules: list):
    """Display completed modules as a rich table."""
    if not completed_modules:
        console.print("\n[dim]No completed modules recorded yet.[/dim]\n")
        return

    table = Table(title="Completed Modules")
    table.add_column("Title", style="cyan")
    table.add_column("Module", style="magenta")
    table.add_column("Semester", style="green", justify="center")

    for module_info, semester in completed_modules:
        # module_info is (title, module_code) tuple
        if isinstance(module_info, tuple):
            title, module_code = module_info
        else:
            title, module_code = module_info, module_info
        sem_str = str(semester) if semester != -1 else "Unknown"
        table.add_row(title, module_code, sem_str)

    console.print(table)


def print_selected_courses(selected_courses: list):
    """Display currently selected courses as a rich table with credits.

    Args:
        selected_courses: List of (course_title, module_code) tuples
    """
    if not selected_courses:
        console.print("\n[dim]No courses selected yet.[/dim]\n")
        return

    config = load_config()
    table = Table(title="Selected Courses")
    table.add_column("Course", style="cyan")
    table.add_column("Module", style="magenta")
    table.add_column("Credits", style="green", justify="right")

    total_credits = 0
    for course_title, module_code in selected_courses:
        credits = get_module_credits(config["modules_collection"], module_code)
        total_credits += credits
        table.add_row(course_title, module_code, str(credits))

    # Add total row
    table.add_row("", "[bold]Total[/bold]", f"[bold]{total_credits}[/bold]")

    console.print(table)

    # Warning if over 30 credits
    if total_credits > 30:
        console.print(
            f"[yellow]Warning: The maximum credits recommended per semester is 30. "
            f"Your current selection is {total_credits} credits. "
            f"We recommend removing a selected course.[/yellow]\n"
        )


def print_selected_courses_opt(selected_courses: list):
    """Display currently selected courses for opt mode as a rich table with credits.

    Args:
        selected_courses: List of (course_title, [module_codes]) tuples
                         where module_codes is a list of possible module assignments
    """
    if not selected_courses:
        console.print("\n[dim]No courses selected yet.[/dim]\n")
        return

    config = load_config()
    table = Table(title="Selected Courses")
    table.add_column("Course", style="cyan")
    table.add_column("Modules", style="magenta")
    table.add_column("Credits", style="green", justify="right")

    total_credits = 0
    for course_title, module_codes in selected_courses:
        # Use first module's credits (all alternatives have same credits)
        credits = get_module_credits(config["modules_collection"], module_codes[0])
        total_credits += credits
        # Display all module codes comma-separated
        modules_str = ", ".join(module_codes)
        table.add_row(course_title, modules_str, str(credits))

    # Add total row
    table.add_row("", "[bold]Total[/bold]", f"[bold]{total_credits}[/bold]")

    console.print(table)

    # Warning if over 30 credits
    if total_credits > 30:
        console.print(
            f"[yellow]Warning: The maximum credits recommended per semester is 30. "
            f"Your current selection is {total_credits} credits. "
            f"We recommend removing a selected course.[/yellow]\n"
        )


def state_to_asp_dict(state: StudentState) -> dict:
    """Convert StudentState to dict format expected by ASP functions."""
    return {
        "n_semesters_preference": state.n_semesters,  # Note: ASP uses different key name
        "current_semester": state.current_semester,
        "bridge_modules": state.bridge_modules,
        # "completed_modules": state.completed_modules,
    }


def collect_core_info(state: StudentState) -> StudentState:
    """Phase 1: Get semester, bridge modules, planning horizon."""

    ai_print(
        "\nTell me about your situation (semester, bridge modules, how many semesters to plan).\n"
    )
    ai_print("I need all of this information to proceed.\n")

    while not state.has_required_core_fields():
        text = user_input("\nYou: ")

        extracted = extract_core_info(
            text
        )  # LLM -> {current_semester, bridge_modules, n_semesters}

        # Merge extracted values into state
        if extracted.get("current_semester"):
            state.current_semester = extracted["current_semester"]
        if extracted.get("bridge_modules"):
            state.bridge_modules = canonicalize_modules(
                modules=extracted["bridge_modules"],
                filters={"type": "bridge"},
                bridge_only=True,
            )
        if extracted.get("n_semesters"):
            state.n_semesters = extracted["n_semesters"]

        # Tell user what's still missing
        missing = state.get_missing_core_fields()
        if missing:
            ai_print(f"\nStill need: {', '.join(missing)}\n")

    # Confirm
    ai_print("\nExtracted info:")
    ai_print(f"  - Current semester: {state.current_semester}")
    ai_print(
        f"  - Bridge modules: {state.bridge_modules if state.bridge_modules != ['none'] else 'None'}"
    )
    ai_print(f"  - Planning for: {state.n_semesters} semesters")
    if not confirm("\nIs this correct?"):
        state.current_semester = None
        state.bridge_modules = []
        state.n_semesters = None
        return collect_core_info(state)  # Retry

    state.core_info_confirmed = True
    return state


def _completed_modules_are_admissible(state: StudentState) -> bool:
    """Throwaway SAT check: are the declared bridge/completed modules alone
    consistent with the program's ASP constraints (credit sums, mandatory
    subsets, bridge-module limits, etc.)? Builds and discards its own Control —
    does not touch the Control used later in Phase 3."""
    program = build_initial_program(state)
    ctl = ground_program(program)
    ctl = assign_core(ctl, state)
    return check_admissibility(ctl)


def collect_completed_modules(state: StudentState) -> StudentState:
    """Phase 2: Get modules already completed (if not semester 1)."""

    ai_print("\nList your completed modules (e.g., 'FM1 in sem 1, BM3 in sem 1').\n")

    while True:
        text = user_input("You: ")

        if text.lower() == "done":
            if _completed_modules_are_admissible(state):
                break
            ai_print(
                "\nThis combination of completed modules conflicts with the program's "
                "requirements (e.g. credit totals, mandatory modules, or bridge-module "
                "limits). Please review and adjust before continuing.\n"
            )
            print_completed_modules(state.completed_modules)
            continue

        bridge = state.bridge_modules
        bridge_str = (
            ", ".join(b[0] if isinstance(b, tuple) else b for b in bridge)
            if bridge and bridge != ["none"]
            else "none"
        )
        state_context = (
            f"Current semester: {state.current_semester}. "
            f"Planning {state.n_semesters} semesters total. "
            f"Bridge/foundation modules: {bridge_str}."
        )
        intent = extract_completed_modules(
            text, state_context=state_context
        )  # LLM -> {add, remove, clear}

        if intent.get("clear"):
            state.completed_modules = []
            ai_print(
                "\nCleared all completed modules. Please add new ones or say 'done' to continue.\n"
            )
            continue

        for canonical in intent.get("remove", []):
            # canonical is (title, module_code) tuple
            state.completed_modules = [
                (m, s) for m, s in state.completed_modules if m != canonical
            ]
            ai_print(f"\nRemoved {canonical[0]}.\n")

        for canonical, semester in intent.get("add", []):
            # canonical is (title, module_code) tuple
            # LLM returns "N/A" when no semester was given, so ask for it
            while not str(semester).strip().isdigit():
                semester = user_input(
                    f"In which semester did you complete {canonical[0]}? "
                )
            semester = int(semester)
            if semester >= state.current_semester:
                ai_print(
                    f"\n{canonical[0]} can't be marked completed in semester {semester} — "
                    f"it must be earlier than your current semester ({state.current_semester}).\n"
                )
                continue
            state.completed_modules.append((canonical, semester))

        print_completed_modules(state.completed_modules)
        ai_print(
            "Add modules with the 'add' command, modify modules with the 'remove' and 'clear' commands, or enter 'done' to continue.\n"
        )

    state.completed_modules_confirmed = True
    return state


def plan_semester_brave(state: StudentState) -> tuple[StudentState, object]:
    """Phase 3: Select courses one at a time using ASP brave reasoning."""

    # 1. Initialize ASP control object with brave mode
    # asp_state = state_to_asp_dict(state)
    program = build_initial_program(state)
    ctl = ground_program(program)

    # 2. Assign core externals (bridge modules, completed modules)
    ctl = assign_core(ctl, state)

    # Track selected courses: [(course_title, module_code), ...]
    selected_courses = []

    # 3. Main loop
    while True:
        # a. Display selected courses and available courses
        console.print()
        print_selected_courses(selected_courses)
        ai_print("\nSelect courses for your semester plan.")
        ai_print("You can add, remove, or clear courses. Enter 'done' when finished.\n")
        available_grouped = display_available_courses(ctl) or {}

        # b. Get user input
        text = user_input("You: ")
        if not text:
            continue
        if text.lower() == "done":
            break

        # c. Extract intent via LLM
        intent = extract_course_intent(text)

        ### Check 1: Ensure user only adds one course at a time in brave mode
        # d. Validate single course for add (brave mode restriction)
        if len(intent.get("add", [])) > 1:
            ai_print("\nIn 'brave' mode, you can only add one course at a time.")
            ai_print("Please select a single course to add.\n")
            continue

        ### Check 2: Ensure course is in available list of courses, determined by brave reasoning
        # f. Check course is in available list
        if intent.get("add"):
            selected_title, _ = intent["add"][0]

            # Find ALL modules that contain the selected course
            # available_grouped structure: {module_type: [(course_title, [module_codes]), ...]}
            matching_modules = []
            for _, courses in available_grouped.items():
                for course_title, codes in courses:
                    if selected_title == course_title:
                        matching_modules.extend(codes)

            if not matching_modules:
                ai_print(f"\n'{selected_title}' is not currently available.")
                ai_print("Please select a course from the available list.\n")
                continue

            ### Check 3: Disambiguate module if multiple codes exist for same title
            # g. Disambiguate module if needed
            if len(matching_modules) == 1:
                matched_module = matching_modules[0]
            else:
                ai_print(
                    f"\n'{selected_title}' can be assigned to multiple modules: {', '.join(matching_modules)}"
                )
                ai_print("Please enter the exact module code you want to use.\n")

                matched_module = None
                while matched_module is None:
                    choice = user_input("You: ")
                    if choice in matching_modules:
                        matched_module = choice
                    else:
                        ai_print(
                            f"Invalid selection. Please enter one of: {', '.join(matching_modules)}\n"
                        )

            ### Assign selection as True in ASP program
            # h. Confirm and update ASP state (assign truth to selected course)
            selection = [(selected_title, [matched_module])]
            ctl = assign_requested_courses(ctl, selection)

            # Track the selection for display
            selected_courses.append((selected_title, matched_module))

        # j. Handle clear operation
        if intent.get("clear"):
            if selected_courses:
                ctl = unassign_requested_courses(ctl, selected_courses)
                selected_courses = []
                ai_print("\nCleared all selected courses.\n")
            else:
                ai_print("\nNo courses to clear.\n")
            continue

        # k. Handle remove operation
        if intent.get("remove"):
            for course_title, _ in intent["remove"]:
                # Find matching course in selected_courses
                matching = [(t, m) for t, m in selected_courses if t == course_title]
                if matching:
                    course_to_remove = matching[0]
                    ctl = unassign_requested_courses(ctl, [course_to_remove])
                    selected_courses = [
                        (t, m) for t, m in selected_courses if t != course_title
                    ]
                    ai_print(f"\nRemoved '{course_title}' from selection.\n")
                else:
                    ai_print(f"\n'{course_title}' is not in your selected courses.\n")

    state.selected_courses = selected_courses
    ai_print("\nFinal selected courses:\n")
    print_selected_courses(state.selected_courses)
    state.plan_confirmed = True
    return state, ctl


def confirm(prompt: str) -> bool:
    return user_input(f"{prompt} (y/n): ").lower() in ("y", "yes")


def plan_semester_opt(state: StudentState) -> tuple[StudentState, object]:
    """Phase 3: Select courses using ASP optimization mode."""

    # 1. Initialize ASP control object with optimization mode
    program = build_initial_program(state)
    ctl = ground_program(program)

    # 2. Assign core externals (bridge modules, completed modules)
    ctl = assign_core(ctl, state)

    # Track selected courses: [(course_title, [module_codes]), ...] - grouped by course
    selected_courses = []

    # 3. Get all available courses (filtered by semester at DB level)
    available_grouped = get_all_courses(ctl) or {}

    # Filter bridge-set courses (in(M,f)) if user has no bridge modules
    if not state.bridge_modules or state.bridge_modules == ["none"]:
        bridge_codes = {
            atom.symbol.arguments[0].name
            for atom in ctl.symbolic_atoms.by_signature("in", 2)
            if atom.symbol.arguments[1].name == "f"
        }
        available_grouped = {
            mtype: [
                (title, [c for c in codes if c not in bridge_codes])
                for title, codes in courses
                if any(c not in bridge_codes for c in codes)
            ]
            for mtype, courses in available_grouped.items()
            if any(any(c not in bridge_codes for c in codes) for _, codes in courses)
        }

    # 4. Main loop
    while True:
        # a. Display selected courses and available courses
        console.print()
        print_selected_courses_opt(selected_courses)
        ai_print("\nSelect courses for your semester plan.")
        ai_print("You can add, remove, or clear courses. Enter 'done' when finished.\n")
        selected_titles = {title for title, _ in selected_courses}
        print_available_courses(available_grouped, exclude=selected_titles)

        # b. Get user input
        text = user_input("You: ")
        if not text:
            continue
        if text.lower() == "done":
            break

        # c. Extract course additions, removals and clearance via LLM
        intent = extract_course_intent(text)

        ### Check 1: Ensure course is in available list of courses
        # f. Check all courses are in available list
        if intent.get("add"):
            # Process ALL courses in the add list
            all_valid = True
            courses_to_add = []

            for selected_title, _ in intent["add"]:
                # Find ALL modules that contain the selected course
                # available_grouped structure: {module_type: [(course_title, [module_codes]), ...]}
                matching_modules = []
                for _, courses in available_grouped.items():
                    for course_title, codes in courses:
                        if selected_title == course_title:
                            matching_modules.extend(codes)

                if not matching_modules:
                    ai_print(f"\n'{selected_title}' is not currently available.")
                    ai_print("Please select a course from the available list.\n")
                    all_valid = False
                    break

                courses_to_add.append((selected_title, matching_modules))

            if not all_valid:
                continue

            ### Enumerate assignment options from a self-contained copy of the
            # regulation program: its optimal models keep the legal maximum of
            # the batch, distinct in which module each course fulfills. The
            # main ctl is untouched until the user picks.
            valid_assignments = enumerate_assignments(
                state, selected_courses, courses_to_add
            )
            batch_titles = [title for title, _ in courses_to_add]

            if valid_assignments:
                print_assignments(valid_assignments, len(courses_to_add))
                selected_idx = select_assignment(valid_assignments)
                selected_plan = valid_assignments[selected_idx]

                course_modules = defaultdict(list)
                for course_title, module_code in selected_plan:
                    course_modules[course_title].append(module_code)

                # Commit only the chosen module per course
                specific_courses = list(course_modules.items())
                ctl = assign_requested_courses(ctl, specific_courses)

                if check_admissibility(ctl):
                    for course_title, modules in course_modules.items():
                        selected_courses.append((course_title, modules))

                    dropped = [t for t in batch_titles if t not in course_modules]
                    if dropped:
                        ai_print(
                            f"\nCould not fit within study regulations: {', '.join(dropped)}. "
                            "The remaining courses were added.\n"
                        )
                    ai_print(
                        "\nYour selection is valid! Add more courses or enter 'done' when finished.\n"
                    )
                else:
                    # Safety net — the chosen assignment came from a model, so
                    # this should be unreachable; roll back rather than corrupt
                    ctl = unassign_requested_courses(ctl, specific_courses)
                    ai_print(
                        "\nCouldn't determine module assignment for your selection.\n"
                    )
            else:
                # No course of the batch can be legally placed — reject
                # (nothing was assigned on the main ctl, nothing to undo)
                ai_print(
                    "\nThis selection violates study regulations and cannot be added. "
                    "Please choose different courses.\n"
                )

        # j. Handle clear operation
        if intent.get("clear"):
            if selected_courses:
                ctl = unassign_requested_courses(ctl, selected_courses)
                selected_courses = []
                ai_print("\nCleared all selected courses.\n")
            else:
                ai_print("\nNo courses to clear.\n")
            continue

        # k. Handle remove operation
        if intent.get("remove"):
            for course_title, _ in intent["remove"]:
                # Find matching course in selected_courses
                matching = [(t, m) for t, m in selected_courses if t == course_title]
                if matching:
                    course_to_remove = matching[0]
                    ctl = unassign_requested_courses(ctl, [course_to_remove])
                    selected_courses = [
                        (t, m) for t, m in selected_courses if t != course_title
                    ]
                    ai_print(f"\nRemoved '{course_title}' from selection.\n")
                else:
                    ai_print(f"\n'{course_title}' is not in your selected courses.\n")

    state.selected_courses = selected_courses
    ai_print("\nFinal selected courses:\n")
    print_selected_courses_opt(state.selected_courses)
    state.plan_confirmed = True
    return state, ctl



