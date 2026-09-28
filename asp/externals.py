"""External atom assignment for ASP programs."""

from clingo import Control, Function, Number, parse_term

from core.logging import debug_print
from core.state import StudentState


def assign_core(grounded_ctl: Control, state: StudentState) -> Control:
    """
    Input: grounded_ctl - clingo Control object with grounded ASP program
    Assign truth values to externals in the grounded ASP program based on the state.
    1. Current semester
    2. Number of semesters preference (add and ground)
    3. Bridge modules
    4. Completed modules
    Output: grounded_ctl with assigned externals for core information (required bridge modules, completed modules etc).
    """
    # extract bridge modules, assign truth values to atoms and add to program
    for bm in state.bridge_modules:
        # print(f"Assigning bridge module: {bm}")

        if bm[0].lower() != "none":
            grounded_ctl.assign_external(
                Function("bridge_modules", [Function(bm[1])]), True
            )
            grounded_ctl.assign_external(
                Function("in", [Function(bm[1]), Function("e")]), True
            )

    # extract completed modules, assign
    if state.completed_modules:
        for cm in state.completed_modules:
            # print(f"cm: {cm}")
            module_name, module_code = cm[0]

            semester_num = cm[1]

            atom = Function(
                "completed_modules",
                [  # atom looks like: completed_modules(module_code,semester_completed) (e.g. completed_modules(bm1,1).)
                    Function(module_code),
                    Number(int(semester_num)),
                ],
            )
            grounded_ctl.assign_external(atom, True)
    return grounded_ctl


def assign_requested_courses(ctl: Control, requested_courses: list) -> Control:

    for course in requested_courses:
        for i in range(len(course[1])):
            atom_name = f'requested("{course[0]}",{course[1][i]})'

            # Find the atom object from the control object
            symbol = parse_term(atom_name)
            atom = ctl.symbolic_atoms[symbol].symbol  # type: ignore
            ctl.assign_external(atom, True)
            debug_print(f"Assigned external atom to True: {atom}")

    return ctl


def assign_committed_courses(ctl: Control, committed_courses: list) -> Control:
    """Seed previously committed (title, [module]) selections as committed/2
    externals — forced chosen facts that no solve may drop or reassign."""
    for course in committed_courses:
        for module_code in course[1]:
            atom_name = f'committed("{course[0]}",{module_code})'

            symbol = parse_term(atom_name)
            atom = ctl.symbolic_atoms[symbol].symbol  # type: ignore
            ctl.assign_external(atom, True)
            debug_print(f"Assigned external atom to True: {atom}")

    return ctl


def unassign_requested_courses(ctl: Control, courses_to_remove: list) -> Control:
    """
    Unassign (set to False) external atoms for removed courses.

    Args:
        ctl: clingo Control object
        courses_to_remove: List of (course_title, module_code(s)) tuples
                          module_code can be a string (brave mode) or list of strings (opt mode)

    Returns:
        Control object with externals set to False
    """
    for course_title, module_codes in courses_to_remove:
        # Handle both single module (string) and multiple modules (list)
        if isinstance(module_codes, str):
            module_codes = [module_codes]

        for module_code in module_codes:
            atom_name = f'requested("{course_title}",{module_code})'

            symbol = parse_term(atom_name)
            if symbol in ctl.symbolic_atoms:
                atom = ctl.symbolic_atoms[symbol].symbol  # type: ignore
                ctl.assign_external(atom, False)
                debug_print(f"Unassigned external atom (set to False): {atom}")

    return ctl
