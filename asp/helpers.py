"""Helper functions for converting database entries to ASP facts."""

from db.chroma.chroma_store import get_items
from utils.utils import load_config


def db_to_asp(collection: str, predicate: str, arguments: list, filters: dict = None, unique: str = None) -> list:
    """
        Convert database entries to ASP facts.
        get items returns list: [
        {"id": id, "metadata": meta, "document": doc}
        for id, meta, doc in zip(
            results["ids"],
            results["metadatas"],
            results["documents"]
        )
    ]

        Args:
            collection (str): The name of the collection to query.
            predicate (str): The predicate name to use in the ASP facts e.g. 'course' in course("Title",ModuleCode).
            arguments (list): List of fields to include as arguments in the ASP facts.
            filters (dict, optional): Filters to apply when querying the database.
            unique (str, optional): Field name to expand into separate facts. If this field
                contains multiple values (list), a separate fact is generated for each value.
        Returns:
            list: ASP facts as a list of strings.
    """
    config = load_config()
    quoted_fields = config.get("asp", {}).get("quoted_fields", [])
    results = get_items(collection=collection, filters=filters)
    items_list = extract_fields(results, arguments)

    asp_facts = []
    for item in items_list:
        asp_facts.extend(build_asp_facts(predicate, item, arguments, quoted_fields, unique))

    return asp_facts


def extract_fields(results: list, arguments: list) -> list:
    """Extract specified fields from result metadata into a list of dicts."""
    return [
        {arg: item["metadata"].get(arg) for arg in arguments if arg in item["metadata"]}
        for item in results
    ]


def format_asp_value(val, field: str = None, quoted_fields: list = None) -> list:
    """Format a value for ASP: quoted fields get quotes, others become atoms."""
    if val is None:
        return []
    if isinstance(val, str):
        if quoted_fields and field in quoted_fields:
            return [f'"{val}"']
        return [str(val)]  # unquoted atom
    if isinstance(val, list):
        return [str(v) for v in val]
    return [str(val)]


def build_asp_fact(predicate: str, item: dict, arguments: list, quoted_fields: list = None) -> str:
    """Build a single ASP fact from extracted arguments."""
    formatted_args = []
    for arg in arguments:
        formatted_args.extend(format_asp_value(item.get(arg), field=arg, quoted_fields=quoted_fields))

    if not formatted_args:
        return None
    return f'{predicate}({",".join(formatted_args)}).'


def normalize_to_list(val) -> list:
    """Normalize a value to a list for expansion.

    Handles: lists, comma-separated strings, single values.
    """
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str) and ',' in val:
        return [v.strip() for v in val.split(',')]
    return [val]


def build_asp_facts(predicate: str, item: dict, arguments: list, quoted_fields: list = None, unique: str = None) -> list:
    """Build ASP facts, expanding unique field into separate facts if it has multiple values.

    Args:
        predicate: The predicate name for the ASP fact.
        item: Dict containing field values.
        arguments: List of field names to include as arguments.
        quoted_fields: List of field names that should be quoted in ASP.
        unique: Field name to expand. If this field has multiple values (list or
                comma-separated string), a separate fact is generated for each value.
                (e.g. unique=module --> module: 'am21,am22' -> module(am21). module(am22).)
    Returns:
        List of ASP fact strings.
    """
    if unique is None or unique not in item:
        fact = build_asp_fact(predicate, item, arguments, quoted_fields)
        return [fact] if fact else []

    unique_values = normalize_to_list(item.get(unique))

    if len(unique_values) <= 1:
        fact = build_asp_fact(predicate, item, arguments, quoted_fields)
        return [fact] if fact else []

    facts = []
    for val in unique_values:
        modified_item = {**item, unique: val}
        fact = build_asp_fact(predicate, modified_item, arguments, quoted_fields)
        if fact:
            facts.append(fact)

    return facts
