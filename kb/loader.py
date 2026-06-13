"""
kb/loader.py — IRIS Knowledge Base I/O layer.

Pure I/O: loads JSON files from disk.
Every public function is @lru_cache so each file is read at most once per process run.
No business logic lives here — just reads, parses, and returns raw dicts/lists.
"""

from functools import lru_cache
import json
import logging
from pathlib import Path

from config import INDEX_FILE, HBP_DIR, STG_DIR, PMJAY_RULES_FILE, QUERY_TAXONOMY_FILE

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def load_index() -> list[dict]:
    """Load data/hbp/_index.json.

    Returns the list of thin index rows used exclusively by the fuzzy searcher.
    Each row contains:
        procedure_code, package_code, specialty_code, specialty,
        package_name, procedure_name, aliases, billing_unit,
        reserved_public_only, procedure_label, auto_approved,
        day_care, base_rate_inr, stg_ref.

    Raises:
        FileNotFoundError: if _index.json does not exist (always a build error).
        json.JSONDecodeError: if the file is malformed (always a build error).
    """
    logger.info("Loading HBP index from %s", INDEX_FILE)
    try:
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("Malformed JSON in HBP index %s: %s", INDEX_FILE, exc)
        raise
    logger.info("HBP index loaded: %d rows", len(data))
    return data


@lru_cache(maxsize=32)
def load_specialty_shard(shard_filename: str) -> dict:
    """Load data/hbp/<shard_filename>.json.

    Args:
        shard_filename: Filename WITHOUT extension, e.g. "burnsmanagement",
            "emergency_room_packages", "general_surgery".

    Returns:
        Top-level shard dict with structure:
            {scheme_id, specialty, specialty_code, packages: [...]}
        Each package:  {package_code, package_name, procedures: [...]}
        Each procedure contains all fields from the KB-2 schema in SYSTEM_DESIGN.md.

    Raises:
        FileNotFoundError: if the shard file does not exist — valid, not all shards are
            built yet, but callers must handle this as an expected gap.
        json.JSONDecodeError: if the file is malformed — always a build bug; logged and
            re-raised so nothing silently swallows it.
    """
    shard_path: Path = HBP_DIR / f"{shard_filename}.json"
    logger.info("Loading specialty shard from %s", shard_path)
    try:
        data = json.loads(shard_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("Specialty shard not found: %s (shard not yet built)", shard_path)
        raise
    except json.JSONDecodeError as exc:
        logger.error("Malformed JSON in shard %s: %s", shard_path, exc)
        raise
    pkg_count = len(data.get("packages", []))
    logger.info(
        "Shard '%s' loaded: specialty=%s, %d packages",
        shard_filename,
        data.get("specialty", "?"),
        pkg_count,
    )
    return data


@lru_cache(maxsize=500)
def load_stg(procedure_code: str) -> dict | None:
    """Load data/stg/<procedure_code>.json.

    Returns None if the file does not exist — expected; many procedures have no
    STG file built yet and callers treat this as a soft gap, not an error.

    Returns the raw STG dict if found. Key fields in the returned dict:
        clinical_indications          — list[str]
        clinical_thresholds           — list[{field, operator, value, note}] (NO unit field)
        min_doctor_qualification      — list[str] (ARRAY, not a single string)
        additional_information.clinical_key_pointers — list[str] (richest clinical detail)
        checklist.ppd_preauth         — list[{q: str, expected: bool}] (bool, not string)
        common_queries                — list[str]
        mandatory_documents.preauth   — list[{key, label}]
        mandatory_documents.claim     — list[{key, label}]

    Args:
        procedure_code: Exact procedure code string, e.g. "ER001A", "MM010B".

    Raises:
        json.JSONDecodeError: if the file exists but is malformed — logged and re-raised.
    """
    stg_path: Path = STG_DIR / f"{procedure_code}.json"
    try:
        raw = stg_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("STG file not found for %s — no STG check will be performed", procedure_code)
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Malformed JSON in STG file %s: %s", stg_path, exc)
        raise
    logger.info("STG loaded for procedure %s", procedure_code)
    return data


@lru_cache(maxsize=1)
def load_pmjay_rules() -> dict:
    """Load data/schemes/pmjay.json.

    Returns:
        Scheme-wide rules dict containing pricing multipliers, combination rules,
        exclusions, enhancement batch sizes, bed rates, and NE states list.

    Raises:
        FileNotFoundError: if pmjay.json does not exist (always a build error).
        json.JSONDecodeError: if the file is malformed (always a build error).
    """
    logger.info("Loading PM-JAY rules from %s", PMJAY_RULES_FILE)
    try:
        data = json.loads(PMJAY_RULES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("Malformed JSON in PM-JAY rules %s: %s", PMJAY_RULES_FILE, exc)
        raise
    logger.info("PM-JAY rules loaded")
    return data


@lru_cache(maxsize=1)
def load_query_taxonomy() -> dict:
    """Load data/query_taxonomy.json.

    Returns:
        Dict containing PPD query reasons (Table 3) and rejection reasons (Table 4)
        from CAM 2026.

    Raises:
        FileNotFoundError: if query_taxonomy.json does not exist (always a build error).
        json.JSONDecodeError: if the file is malformed (always a build error).
    """
    logger.info("Loading query taxonomy from %s", QUERY_TAXONOMY_FILE)
    try:
        data = json.loads(QUERY_TAXONOMY_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("Malformed JSON in query taxonomy %s: %s", QUERY_TAXONOMY_FILE, exc)
        raise
    logger.info("Query taxonomy loaded")
    return data


def get_procedure_from_shard(procedure_code: str, shard: dict) -> dict | None:
    """Find a procedure dict by its procedure_code inside a loaded shard.

    Traverses: shard["packages"] → each package["procedures"] → procedure["procedure_code"].
    Not cached — operates on an already-loaded (and cached) shard dict.

    Args:
        procedure_code: Exact code to match, e.g. "ER001A".
        shard: A shard dict as returned by load_specialty_shard().

    Returns:
        The procedure dict if found, or None.

        The returned dict includes all KB-2 fields:
            billing_unit, medical_or_surgical, day_care, reserved_public_only,
            stratification_required, stratification_criteria (list or null),
            los_indicative (int or str "daycare"),
            enhancement_applicable, auto_approved, procedure_label,
            special_conditions_popup, special_conditions_rule,
            is_addon_to (list or null), addon_type (str or null),
            implant (null or dict or list),
            mandatory_documents.preauth  — list[{key, label}],
            rates_inr (dict or null for per_day packages),
            pricing.base_rate_inr (int or null).
    """
    for package in shard.get("packages", []):
        for procedure in package.get("procedures", []):
            if procedure.get("procedure_code") == procedure_code:
                return procedure
    return None


def get_package_from_shard(package_code: str, shard: dict) -> dict | None:
    """Find a package dict by its package_code inside a loaded shard.

    Traverses shard["packages"] for the first entry whose package_code matches.
    Not cached — operates on an already-loaded (and cached) shard dict.

    Args:
        package_code: Exact code to match, e.g. "ER001".
        shard: A shard dict as returned by load_specialty_shard().

    Returns:
        The package dict (which contains a procedures list) if found, or None.
    """
    for package in shard.get("packages", []):
        if package.get("package_code") == package_code:
            return package
    return None
