"""ASP program building and grounding."""

from clingo import Control

from asp.helpers import db_to_asp
from core.state import StudentState
from utils.utils import load_config


def build_initial_program(state: StudentState) -> str:
    """
    Build the ASP program based on the user's core information state. Add relevant external atoms.
    Define available courses that can be taken based on user's situation."""
    config = load_config()
    program = []
    ############################################################
    ##### extract semester preference and current semester #####
    program.append(f"n_semesters_preference({state.n_semesters}).")
    program.append(f"current_semester({state.current_semester}).")
    program.append(
        f"#const n = {state.n_semesters}."
    )  # constant necessary for encoding

    # add course titles as facts from db, filtered by current semester
    # filter by semester from config (e.g., "wise2526", "sose26")
    semester_filter = {"semester": config["semester"]}
    courses_string = "\n".join(
        db_to_asp(
            collection=config["courses_collection"],
            predicate="course",
            arguments=["title", "module"],
            filters=semester_filter,
            unique="module",
        )
    )
    program.append(courses_string)  # add semester-filtered courses from DB as facts

    return "\n".join(program)


def ground_program(program: str) -> Control:
    config = load_config()
    ctl = Control(["--warn=none"])

    ctl.load(config["instance"])
    ctl.load(config["encoding"])
    ctl.load("asp/lp/availability.lp")
    ctl.load("asp/lp/external_atoms.lp")
    ctl.load("asp/preferences/diversity_external.lp")

    ctl.add("base", [], program)

    ctl.ground([("base", [])])
    return ctl
