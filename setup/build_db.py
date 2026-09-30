"""
Generic ChromaDB builder for any study program.

Usage:
    python -m setup.build_db cogsys          # Build collections for cogsys
    python -m setup.build_db --all           # Build for all programs
    python -m setup.build_db cogsys --replace  # Rebuild (delete + recreate)
"""

import argparse
import sys
from pathlib import Path

import yaml
import voyageai

from db.chroma.chroma_store import (
    CourseDocument,
    ModuleDocument,
    add_courses,
    add_modules,
    delete_collection,
    update_module_credits,
    upsert_courses,
)

PROGRAMS_DIR = Path(__file__).parent / "programs"
EMBED_MODEL = "voyage-3-large"


def get_available_programs() -> list[str]:
    """Scan setup/programs/ for directories containing a program.yaml."""
    programs = []
    if not PROGRAMS_DIR.exists():
        return programs
    for d in sorted(PROGRAMS_DIR.iterdir()):
        if d.is_dir() and (d / "program.yaml").exists():
            programs.append(d.name)
    return programs


def load_program_data(program_id: str) -> dict:
    """Load and validate a program's YAML data."""
    program_file = PROGRAMS_DIR / program_id / "program.yaml"
    if not program_file.exists():
        raise FileNotFoundError(
            f"No program.yaml found for '{program_id}' at {program_file}"
        )
    with open(program_file) as f:
        data = yaml.safe_load(f)

    # Basic validation
    required_keys = ["id", "name", "modules", "courses", "credits"]
    missing = [k for k in required_keys if k not in data]
    if missing:
        raise ValueError(f"program.yaml for '{program_id}' missing keys: {missing}")

    return data


def get_embeddings(texts: list[str]) -> list[list[float]]:
    """Generate Voyage AI embeddings for a list of texts."""
    client = voyageai.Client()  # reads VOYAGE_API_KEY from env
    result = client.embed(texts, model=EMBED_MODEL)
    return [list(map(float, emb)) for emb in result.embeddings]


def build_program(program_id: str, replace: bool = False, semester: str = None):
    """Build ChromaDB collections for a single program from its YAML data.

    If semester is provided, only adds courses for that semester (incremental add).
    Course IDs are scoped as '{id}_{semester}' to allow multiple semesters to coexist.
    """
    data = load_program_data(program_id)
    modules_collection = f"{program_id}_modules"
    courses_collection = f"{program_id}_courses"

    if semester:
        # Incremental: only add courses for the given semester, skip modules
        all_courses = data["courses"]
        filtered = [c for c in all_courses if str(c["semester"]) == semester]
        if not filtered:
            print(f"No courses found for semester '{semester}' in '{program_id}'.")
            return

        courses = [
            CourseDocument(
                id=f"{c['id']}_{semester}",
                title=c["title"],
                type=c["type"],
                document=c["document"],
                module=str(c["module"]),
                module_type=str(c["module_type"]),
                semester=str(c["semester"]),
            )
            for c in filtered
        ]

        print(f"Embedding {len(courses)} courses for semester '{semester}'...")
        course_embeddings = get_embeddings([c.document for c in courses])
        upsert_courses(courses_collection, courses, course_embeddings)
        print(f"  Upserted {len(courses)} courses into '{courses_collection}'")
        print(f"Done adding semester '{semester}' for '{program_id}'.")
        return

    if replace:
        print(f"Deleting existing collections for '{program_id}'...")
        delete_collection(modules_collection)
        delete_collection(courses_collection)

    # Build modules
    modules = [
        ModuleDocument(
            id=m["id"],
            title=m["title"],
            type=m["type"],
            document=m["document"],
            module=str(m["module"]),
            sem=str(m["sem"]),
        )
        for m in data["modules"]
    ]

    print(f"Embedding {len(modules)} modules...")
    module_docs = [m.document for m in modules]
    module_embeddings = get_embeddings(module_docs)
    add_modules(modules_collection, modules, module_embeddings)
    print(f"  Added {len(modules)} modules to '{modules_collection}'")

    # Update credits
    credits_map = data["credits"]
    update_module_credits(modules_collection, credits_map)

    # Build courses
    courses = [
        CourseDocument(
            id=f"{c['id']}_{c['semester']}",
            title=c["title"],
            type=c["type"],
            document=c["document"],
            module=str(c["module"]),
            module_type=str(c["module_type"]),
            semester=str(c["semester"]),
        )
        for c in data["courses"]
    ]

    print(f"Embedding {len(courses)} courses...")
    course_docs = [c.document for c in courses]
    course_embeddings = get_embeddings(course_docs)
    add_courses(courses_collection, courses, course_embeddings)
    print(f"  Added {len(courses)} courses to '{courses_collection}'")

    print(f"Done building '{program_id}'.")


def main():
    parser = argparse.ArgumentParser(description="Build ChromaDB collections for study programs")
    parser.add_argument(
        "program",
        nargs="?",
        help="Program ID to build (e.g., 'cogsys'). Use --all for all programs.",
    )
    parser.add_argument(
        "--all", action="store_true", help="Build collections for all available programs"
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete and rebuild existing collections",
    )
    parser.add_argument(
        "--list", action="store_true", help="List available programs and exit"
    )
    parser.add_argument(
        "--semester",
        type=str,
        default=None,
        metavar="SEM",
        help="Add only courses for this semester (e.g. 'wise2627'). Skips modules rebuild.",
    )

    args = parser.parse_args()

    if args.list:
        programs = get_available_programs()
        if programs:
            print("Available programs:")
            for p in programs:
                print(f"  - {p}")
        else:
            print("No programs found in setup/programs/")
        return

    if args.all:
        programs = get_available_programs()
        if not programs:
            print("No programs found in setup/programs/")
            sys.exit(1)
        for p in programs:
            print(f"\n=== Building '{p}' ===")
            build_program(p, replace=args.replace, semester=args.semester)
    elif args.program:
        build_program(args.program, replace=args.replace, semester=args.semester)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
