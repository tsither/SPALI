# core/extraction.py
import json
import re

import anthropic as _anthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_anthropic import ChatAnthropic
from pydantic import SecretStr

from core.logging import debug_print
from db.chroma.chroma_store import get_items, search_courses, search_modules
from utils.utils import load_config, read_yaml

def _parse_json(content: str) -> dict:
    """Parse JSON from LLM response, stripping markdown code fences if present."""
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise json.JSONDecodeError("No JSON object found", content, 0)


_anthropic_client = _anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env


def _call_llm(system_prompt: str, user_input: str, schema: dict) -> dict:
    """Call Anthropic API with structured output_config; returns guaranteed-valid JSON dict."""
    response = _anthropic_client.messages.create(
        model=_get_model(),
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_input}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    return json.loads(response.content[0].text)


def _call_llm_unstructured(system_prompt: str, user_input: str) -> dict:
    """Call Anthropic API without structured output; parses JSON from the response text."""
    response = _anthropic_client.messages.create(
        model=_get_model(),
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_input}],
    )
    return _parse_json(response.content[0].text)


def _get_extraction_model():
    config = load_config()
    return config["extraction_model"]


EXTRACTION_MODEL = None  # lazily initialized


def _get_model():
    global EXTRACTION_MODEL
    if EXTRACTION_MODEL is None:
        EXTRACTION_MODEL = _get_extraction_model()
    return EXTRACTION_MODEL


def extract_core_info(user_input: str) -> dict:
    """
    Extract core student info from natural language input using LLM. LLM is prompted to return JSON containing core information.

    Returns dict with keys: current_semester, bridge_modules, n_semesters
    Only includes fields that were successfully extracted.
    """
    prompts = read_yaml("prompts.yaml")
    system_prompt = prompts["prompts"]["extract_core_info"]
    schema = prompts["schemas"]["extract_core_info"]

    parsed = _call_llm(system_prompt, user_input, schema)

    result = {}

    # Extract current_semester
    if parsed.get("current_semester") is not None:
        try:
            result["current_semester"] = int(parsed["current_semester"])
        except (ValueError, TypeError):
            pass

    # Extract n_semesters (mapped from n_semesters_preference in prompt)
    if parsed.get("n_semesters_preference") is not None:
        try:
            result["n_semesters"] = int(parsed["n_semesters_preference"])
        except (ValueError, TypeError):
            pass

    # Extract bridge_modules (no canonicalization here - done separately)
    if parsed.get("bridge_modules") is not None:
        result["bridge_modules"] = parsed["bridge_modules"]

    return result


def canonicalize_modules(
    modules: list,
    filters: dict,
    collection=None,
    bridge_only=False,
) -> list:
    """
    Convert user-provided (course/module) names to canonical (title, code) tuples.

    First tries exact match on module code, then falls back to vector search.

    Returns list of tuples: [(title, module_code), ...]
    Or ["none"] if user indicated no bridge modules.

    Module metadata structure in db:
    {'module': str, 'type': str, 'title': str, 'sem': str}
    """
    if collection is None:
        collection = load_config()["modules_collection"]

    if modules == ["none"] or not modules:
        if bridge_only:
            return modules if modules == ["none"] else []
        else:
            debug_print("Modules list is empty. Error in canonicalization.")
            return []

    canonical = []
    for query in modules:
        # Try exact match on module code first (faster, no embedding needed)
        exact_results = get_items(
            collection=collection,
            module_code=query,
            filters=filters,
        )
        if exact_results:
            # print(f"Exact match found for {query}: {exact_results[0]}")
            title = exact_results[0]["metadata"].get("title", query)
            module_code = exact_results[0]["metadata"].get("module", query)
            canonical.append((title, module_code))
            continue

        # Fall back to vector search
        results = search_modules(
            collection=collection,
            query=query,
            top_k=1,
            filters=filters,
        )
        if results:
            title = results[0]["metadata"].get("title", query)
            module_code = results[0]["metadata"].get("module", query)
            canonical.append((title, module_code))
        else:
            # Keep original if no match found
            canonical.append((query, query))
    return canonical


def canonicalize_courses(
    courses: list,
    filters: dict,
    collection=None,
) -> list:
    """
    Convert user-provided course titles to canonical (title, code) tuples.

    First tries exact match on module code, then falls back to vector search.

    Returns list of tuples: [(title, module_code), ...]

    Course metadata structure in db:
    {'title': str, 'type': str, 'module': str, 'semester': str}

    """

    if collection is None:
        collection = load_config()["courses_collection"]

    canonical = []
    for query in courses:
        # Try exact match on module code first (faster, no embedding needed)
        exact_results = get_items(
            collection=collection,
            module_code=query,
            filters=filters if filters else None,
        )
        if exact_results:
            title = exact_results[0]["metadata"].get("title", query)
            module_code = exact_results[0]["metadata"].get("module", query)
            canonical.append((title, module_code))
            continue

        # Fall back to vector search
        results = search_courses(
            collection=collection,
            query=query,
            top_k=1,
            filters=filters,
        )
        # print(f"Results for {query}: {results}")
        if results:
            title = results[0]["metadata"].get("title", query)
            module_code = results[0]["metadata"].get("module", query)
            canonical.append((title, module_code))
        else:
            # Keep original if no match found
            canonical.append((query, query))
    return canonical


def extract_completed_modules(user_input: str, state_context: str | None = None) -> dict:
    """
    Extract completed modules operations from natural language input using LLM.

    Returns dict with keys: add, remove, clear
    - add: List of [module_name, semester] tuples to add
    - remove: List of module names to remove
    - clear: bool indicating whether to clear all completed modules
    """
    prompts = read_yaml("prompts.yaml")
    system_prompt = prompts["prompts"]["extract_completed_modules"]
    schema = prompts["schemas"]["extract_completed_modules"]

    if state_context:
        system_prompt = system_prompt + f"\n\n---\nSTUDENT CONTEXT:\n{state_context}"

    parsed = _call_llm(system_prompt, user_input, schema)

    result = {
        "add": parsed.get("add", []),
        "remove": parsed.get("remove", []),
        "clear": parsed.get("clear", False),
    }
    debug_print(f"Extracted intent: {result}")

    # Canonicalize module names
    filters = {}

    # Canonicalize modules to add: convert [module_name, semester] -> [(title, code), semester]
    if result["add"]:
        module_names = [item[0] for item in result["add"]]
        canonical_modules = canonicalize_modules(modules=module_names, filters=filters)
        result["add"] = [
            (canonical_modules[i], item[1]) for i, item in enumerate(result["add"])
        ]

    # Canonicalize modules to remove
    if result["remove"]:
        canonical_remove = canonicalize_modules(
            modules=result["remove"], filters=filters
        )
        result["remove"] = canonical_remove

    return result


def extract_course_intent(user_input: str) -> dict:
    """
    Extract course addition/removal intent from natural language input using LLM.

    Returns dict with keys: add, remove, clear
    - add: List of course titles or module codes to add
    - remove: List of course titles or module codes to remove
    - clear: bool indicating whether to clear all courses from the list
    """
    prompts = read_yaml("prompts.yaml")
    system_prompt = prompts["prompts"]["extract_requested_courses"]
    schema = prompts["schemas"]["extract_requested_courses"]

    parsed = _call_llm(system_prompt, user_input, schema)

    result = {
        "add": parsed.get("add", []),
        "remove": parsed.get("remove", []),
        "clear": parsed.get("clear", False),
    }
    # print(f"\nResult: \n {result}\n")

    filters = {}

    # Canonicalize courses to add
    if result["add"]:
        course_names = result["add"]
        canonical_courses = canonicalize_courses(courses=course_names, filters=filters)
        result["add"] = [(canonical_courses[i]) for i, _ in enumerate(result["add"])]

    # Canonicalize courses to remove
    if result["remove"]:
        canonical_remove = canonicalize_courses(
            courses=result["remove"], filters=filters
        )
        result["remove"] = canonical_remove

    # print(f"\nCanonicalized Result: \n {result}\n")
    return result


# def match_course(q):
