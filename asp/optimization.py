"""ASP optimization: assignment enumeration via a self-contained program."""

from clingo import Function

from asp.externals import (
    assign_committed_courses,
    assign_core,
    assign_requested_courses,
)
from asp.program import build_initial_program, ground_program
from core.logging import ai_print


def enumerate_assignments(
    state, selected_courses: list, courses_to_add: list, max_models: int = 5
) -> list:
    """Enumerate up to max_models distinct, regulation-valid module
    assignments that keep the legal maximum of the batch's courses.

    Args:
        state: StudentState — rebuilds the same program as the main Control.
        selected_courses: previously committed (title, [module]) selections;
            their forced chosen/2 atoms rule out cross-batch double-booking.
        courses_to_add: [(title, [candidate_modules]), ...] for the new batch.
        max_models: cap on returned assignments.

    Returns:
        List of assignments, each a sorted list of (title, module) tuples
        over the batch's courses. Assignments shorter than the batch mean the
        regulations force dropping the missing courses; an empty list means
        no course of the batch can be legally placed.
    """
    program = build_initial_program(state)
    throwaway = ground_program(program)
    # Scoped to this throwaway ctl only — #project directives affect
    # clasp's internal brave/cautious consequence computation regardless of
    # the solve.project output-config flag, so this must not live in the
    # shared availability.lp loaded by the main navigation ctl.
    throwaway.add("project_chosen", [], "#project chosen/2.")
    throwaway.ground([("project_chosen", [])])
    assign_core(throwaway, state)
    assign_committed_courses(throwaway, selected_courses)
    assign_requested_courses(throwaway, courses_to_add)
    throwaway.assign_external(Function("allow_partial_assignment"), True)

    solve_conf = throwaway.configuration.solve
    solve_conf.project = "project"
    solve_conf.opt_mode = "optN"
    solve_conf.models = "0"

    titles = {title for title, _ in courses_to_add}
    assignments = []
    with throwaway.solve(yield_=True) as handle:
        for model in handle:
            if not model.optimality_proven:
                continue
            assignment = sorted(
                (atom.arguments[0].string, atom.arguments[1].name)
                for atom in model.symbols(atoms=True)
                if atom.name == "chosen" and atom.arguments[0].string in titles
            )
            if assignment:  # empty = the optimum drops the entire batch
                assignments.append(assignment)
            if len(assignments) >= max_models:
                break
    return assignments


def print_assignments(assignments: list, n_requested: int):
    """Display enumerated module assignments for user selection."""
    ai_print(f"Number of requested courses: {n_requested}")

    all_assigned = any(len(a) == n_requested for a in assignments)
    if all_assigned:
        ai_print("The following are valid assignments for all requested courses.")
    else:
        ai_print(
            "Not all requested courses could be assigned. The following are valid selections for some requested courses."
        )

    ai_print(f"Found {len(assignments)} model(s):")

    ai_print("\nPossible module assignments:")
    for i, assignment in enumerate(assignments, 1):
        ai_print(f"  [{i}] Model {i}:")
        for title, module in assignment:
            ai_print(f'      Course: "{title}", Module: {module}')
        ai_print("")


def select_assignment(assignments: list) -> int:
    """Let user select from enumerated valid assignments. Returns 0-indexed selection. Only used after enumerate_assignments. They run as a pair."""
    if len(assignments) == 1:
        print("Only one valid assignment found - automatically selected.")
        return 0

    while True:
        user_input = input(f"Select an assignment (1-{len(assignments)}): ").strip()
        try:
            selection = int(user_input)
            if 1 <= selection <= len(assignments):
                return selection - 1  # 0-indexed
            print(f"Please enter a number between 1 and {len(assignments)}")
        except ValueError:
            print("Please enter a valid number.")
