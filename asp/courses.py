"""Course display and retrieval functions."""

from collections import defaultdict

from clingo import Control

from core.logging import ai_print
from asp.solve import compute_brave_atoms


def display_available_courses(ctl: Control):
    "Show available courses given core info"
    sat, brave = compute_brave_atoms(ctl)

    if not sat:
        from rich.console import Console
        Console().print(
            "[red]The ASP program is unsatisfiable. You have input incorrect or conflicting information.[/red]"
        )
        return

    # First pass: collect all module codes per course, grouped by module type
    # Structure: {module_type: {course_title: [module_codes]}}
    type_to_courses = defaultdict(lambda: defaultdict(list))

    for atom in brave:
        course_name = atom.arguments[0].string
        module_code = atom.arguments[1].name  # e.g., "am11", "bm2", "pm1"
        # Derive module type from code (strip digits, uppercase)
        module_type = "".join(c for c in module_code if c.isalpha()).upper()
        type_to_courses[module_type][course_name].append(module_code)

    ai_print("Available courses to take this semester:")

    # Build return structure: {module_type: [(course_title, [module_codes]), ...]}
    grouped = defaultdict(list)

    for module_type in sorted(type_to_courses.keys()):
        ai_print(f"\n{module_type} modules:")
        for course_title in sorted(type_to_courses[module_type].keys()):
            codes = sorted(type_to_courses[module_type][course_title])
            grouped[module_type].append((course_title, codes))
            ai_print(f"  - {course_title} [{', '.join(codes)}]")
    ai_print("\n")
    return grouped


def get_all_courses(ctl: Control) -> dict:
    """Get all courses from grounded program without computing brave consequences.

    Courses are already filtered by semester at the database level.

    Returns:
        Dict of {module_type: [(course_title, [module_codes]), ...]}
    """
    type_to_courses = defaultdict(lambda: defaultdict(list))

    for atom in ctl.symbolic_atoms.by_signature("course", 2):
        symbol = atom.symbol
        course_name = symbol.arguments[0].string
        module_code = symbol.arguments[1].name
        module_type = "".join(c for c in module_code if c.isalpha()).upper()
        type_to_courses[module_type][course_name].append(module_code)

    grouped = defaultdict(list)
    for module_type in sorted(type_to_courses.keys()):
        for course_title in sorted(type_to_courses[module_type].keys()):
            codes = sorted(type_to_courses[module_type][course_title])
            grouped[module_type].append((course_title, codes))

    return grouped


def get_available_courses(ctl: Control) -> dict:
    """Get available courses using brave reasoning, respecting all availability constraints.

    Unlike get_all_courses, this filters FM modules when user has no bridge modules,
    already-completed modules, etc. — same filtering as display_available_courses but
    without printing.
    """
    sat, brave = compute_brave_atoms(ctl)
    ctl.configuration.solve.enum_mode = "auto"  # reset after brave computation

    if not sat:
        return {}

    type_to_courses = defaultdict(lambda: defaultdict(list))
    for atom in brave:
        course_name = atom.arguments[0].string
        module_code = atom.arguments[1].name
        module_type = "".join(c for c in module_code if c.isalpha()).upper()
        type_to_courses[module_type][course_name].append(module_code)

    grouped = defaultdict(list)
    for module_type in sorted(type_to_courses.keys()):
        for course_title in sorted(type_to_courses[module_type].keys()):
            codes = sorted(type_to_courses[module_type][course_title])
            grouped[module_type].append((course_title, codes))

    return grouped


def print_available_courses(grouped: dict, exclude: set | None = None):
    """Display pre-computed available courses, optionally excluding some."""
    exclude = exclude or set()
    ai_print("Available courses to take this semester:")
    for module_type in sorted(grouped.keys()):
        ai_print(f"\n{module_type} modules:")
        for course_title, codes in grouped[module_type]:
            if course_title not in exclude:
                ai_print(f"  - {course_title} [{', '.join(codes)}]")
    ai_print("\n")
