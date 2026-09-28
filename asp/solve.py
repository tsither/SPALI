"""ASP solving, explanation, and study plan generation."""

from clingo import Control

from core.state import StudentState


def compute_brave_atoms(ctl: Control) -> tuple[bool, set]:
    """
    Compute the set of all brave consequences of the current program.
    Returns (is_satisfiable, brave_atoms).

    - is_satisfiable: True if there is at least one model, False if UNSAT.
    - brave_atoms: set of all atoms appearing in at least one model.
    """
    ctl.configuration.solve.enum_mode = "brave"  # type: ignore

    brave = set()
    saw_model = False
    with ctl.solve(yield_=True) as handle:
        for model in handle:
            saw_model = True
            brave |= set(model.symbols(shown=True))

    return saw_model, brave


def check_admissibility(ctl):
    result = ctl.solve()
    return result.satisfiable


def generate_study_plans(
    ctl: Control, state: StudentState, n_plans: int = 5
) -> tuple[bool, list[list]]:
    """Enumerate up to n_plans complete study plans from the existing grounded ctl.

    Reuses the existing grounded ctl.
    Adds constraints to pin the user's current-semester selections in place.

    Returns:
        (is_satisfiable, plans) where each plan is a list of (module_code, semester_int)
        tuples sorted by semester.
    """
    ctl.configuration.solve.enum_mode = "auto"  # type: ignore
    ctl.configuration.solve.models = str(n_plans)  # type: ignore

    plans: list[list] = []
    is_satisfiable = False

    with ctl.solve(yield_=True) as handle:
        for model in handle:
            is_satisfiable = True
            plan: list[tuple[str, int]] = []
            for atom in model.symbols(atoms=True):
                if atom.name == "in" and len(atom.arguments) == 2:
                    second = atom.arguments[1]
                    if second.name == "s" and len(second.arguments) == 1:
                        try:
                            sem_int = second.arguments[0].number
                            module_code = atom.arguments[0].name
                            if module_code:  # skip empty-named functors
                                plan.append((module_code, sem_int))
                        except RuntimeError:
                            continue
            plan.sort(key=lambda x: x[1])
            plans.append(plan)
            if len(plans) >= n_plans:
                break

    return is_satisfiable, plans
