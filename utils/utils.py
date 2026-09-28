import yaml
import os

_config_cache = {}


def load_config(path="config.yaml", study_program=None, semester=None):
    """Load config with optional program and semester overrides. Result is cached per (program, semester)."""
    global _config_cache
    cache_key = (study_program, semester)
    if cache_key in _config_cache:
        return _config_cache[cache_key]
    # Return default cache entry if no overrides specified
    if not study_program and not semester and (None, None) in _config_cache:
        return _config_cache[(None, None)]

    with open(path) as f:
        config = yaml.safe_load(f)

    if study_program:
        config["study_program"] = study_program
    if semester:
        config["semester"] = semester

    # Auto-derive program-dependent fields
    config["api_key"] = os.getenv("ANTHROPIC_API_KEY")
    config["modules_collection"] = f"{config['study_program']}_modules"
    config["courses_collection"] = f"{config['study_program']}_courses"
    config["instance"] = f"asp/study_program_instances/{config['study_program']}.lp"

    _config_cache[cache_key] = config
    # Also store as default if this is the first load
    if (None, None) not in _config_cache:
        _config_cache[(None, None)] = config
    return config


def read_yaml(file_path: str) -> dict:
    with open(file_path, 'r') as file:
        return yaml.safe_load(file)


def core_info(result):
    """Extract core information from the result into a dictionary."""
    core = {}
    core["current_semester"] = result.get("current_semester")
    core["bridge_modules"] = result.get("bridge_modules")
    core["n_semesters_preference"] = result.get("n_semesters_preference")
    core["completed_modules"] = result.get("completed_modules", [])
    return core
