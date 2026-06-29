import json
import logging
import os
import tempfile
from pathlib import Path

# pyrefly: ignore [missing-import]
from intake.pdf_extractor import extract_text_from_pdf
# pyrefly: ignore [missing-import]
from intake.docx_extractor import extract_text_from_docx
# pyrefly: ignore [missing-import]
from intake.discharge_parser import parse_discharge_from_documents
# pyrefly: ignore [missing-import]
from intake.schema_validator import validate_discharge_schema

logger = logging.getLogger(__name__)


class IntakeError(Exception):
    """Custom exception raised during intake layer processing."""

    pass


def run_intake(folder_path: str) -> dict:
    """Orchestrates folder scanning, text extraction, schema mapping, and validation.

    Args:
        folder_path: Path to directory containing discharge PDF/DOCX files.

    Returns:
        Structured discharge JSON dictionary.

    Raises:
        IntakeError: If any validation, extraction, or parsing logic fails.
    """
    try:
        # Step 1: Derive case_id and resolve preauth_reference
        folder = Path(folder_path)
        case_id = folder.name
        if not case_id:
            # Fallback if path has a trailing separator that makes name empty
            case_id = Path(str(folder_path).rstrip("/\\")).name

        preauth_cache_path = Path("tests/outputs") / f"{case_id}_output.json"
        preauth_reference = case_id
        preauth_doc_keys = []

        if preauth_cache_path.exists():
            try:
                with open(preauth_cache_path, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
                session_id = cache_data.get("session_id")
                if session_id:
                    preauth_reference = session_id
                else:
                    logger.warning(
                        "session_id is missing or null in pre-auth cache file: '%s'. "
                        "Falling back to case_id='%s' as preauth_reference.",
                        preauth_cache_path,
                        case_id,
                    )

                # Extract procedure-specific doc keys from preauth cache
                preauth_doc_keys = []
                try:
                    selected = cache_data.get("selected_packages") or []
                    if selected:
                        procedure_code = (
                            selected[0]
                            .get("validated", {})
                            .get("procedure_code", "")
                        )
                        specialty_code = (
                            selected[0]
                            .get("validated", {})
                            .get("specialty_code", "")
                        )
                        if procedure_code and specialty_code:
                            from phases.phase3_validator import SPECIALTY_CODE_TO_SHARD
                            from kb.loader import load_specialty_shard, get_procedure_from_shard
                            shard_filename = SPECIALTY_CODE_TO_SHARD.get(specialty_code)
                            if shard_filename:
                                shard_dict = load_specialty_shard(shard_filename)
                                proc_entry = get_procedure_from_shard(procedure_code, shard_dict)
                                if proc_entry:
                                    for doc_type in ("preauth", "claim"):
                                        for doc in proc_entry.get("mandatory_documents", {}).get(doc_type, []):
                                            key = doc.get("key")
                                            if key and key not in preauth_doc_keys:
                                                preauth_doc_keys.append(key)
                except Exception as e:
                    logger.warning(
                        "Could not extract procedure doc keys from preauth cache: %s. "
                        "Parser will use generic doc list.", e
                    )
            except Exception as e:
                logger.warning(
                    "Error reading pre-auth cache file '%s': %s. "
                    "Falling back to case_id='%s' as preauth_reference.",
                    preauth_cache_path,
                    e,
                    case_id,
                )
        else:
            logger.warning(
                "Pre-auth cache file not found at: '%s'. "
                "Falling back to case_id='%s' as preauth_reference.",
                preauth_cache_path,
                case_id,
            )

        # Step 2: Scan top level for files
        if not folder.is_dir():
            raise IntakeError(f"Provided path is not a folder: {folder_path}")

        eligible_files = []
        for item in list(folder.rglob("*.pdf")) + list(folder.rglob("*.docx")):
            if item.name.startswith("."):
                continue
            eligible_files.append(item)

        logger.info(
            "Starting intake for folder '%s'. Found %d file(s).",
            folder_path,
            len(eligible_files),
        )

        if not eligible_files:
            raise IntakeError(
                f"No PDF or DOCX files found in folder: {folder_path}"
            )

        # Step 3: Extract text
        files_text = {}
        for file_path_obj in eligible_files:
            filename = file_path_obj.name
            suffix = file_path_obj.suffix.lower()

            logger.info(
                "Extracting file '%s' using %s extractor",
                filename,
                "PDF" if suffix == ".pdf" else "DOCX",
            )

            try:
                if suffix == ".pdf":
                    extracted_text = extract_text_from_pdf(str(file_path_obj))
                else:
                    extracted_text = extract_text_from_docx(str(file_path_obj))

                if not extracted_text.strip():
                    logger.warning("Extracted text from file '%s' is empty.", filename)
                    continue

                files_text[filename] = extracted_text
            except Exception as e:
                logger.warning(
                    "Error extracting text from file '%s': %s. Skipping file.",
                    filename,
                    e,
                )
                continue

        if not files_text:
            raise IntakeError(
                "All files produced empty text — cannot parse discharge."
            )

        # Step 4: Parse via LLM
        parsed_dict = parse_discharge_from_documents(
            files_text, preauth_reference, case_id, preauth_doc_keys
        )

        if parsed_dict is None:
            logger.error(
                "Discharge parser returned no result for case %s", case_id
            )
            raise IntakeError(
                f"Discharge parser returned no result for case {case_id}."
            )

        parsed_dict["preauth_input_path"] = str(
            Path("tests/inputs") / f"{case_id}.json"
        )
        logger.info(
            "Injected preauth_input_path: 'tests/inputs/%s.json'", case_id
        )

        # Step 5: Save parsed dict to disk always
        output_dir = Path("tests/inputs")
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        output_file_path = output_dir / f"{case_id}_discharge_parsed.json"
        try:
            with open(output_file_path, "w", encoding="utf-8") as f:
                json.dump(parsed_dict, f, indent=2, ensure_ascii=False)
            logger.info(
                "Saved parsed discharge dictionary to: '%s'", output_file_path
            )
        except Exception as e:
            logger.error(
                "Failed to save parsed discharge dictionary to '%s': %s",
                output_file_path,
                e,
            )

        # Step 6: Validate schema
        is_valid, missing_fields = validate_discharge_schema(parsed_dict)
        if not is_valid:
            logger.error(
                "Discharge schema validation failed. Missing fields: %s",
                missing_fields,
            )
            raise IntakeError(
                f"Discharge schema validation failed. Missing fields: {missing_fields}"
            )

        logger.info(
            "Intake successfully completed for case_id='%s'. Saved to '%s'",
            case_id,
            output_file_path,
        )
        return parsed_dict

    except IntakeError:
        raise
    except Exception as exc:
        logger.error(
            "Unexpected error in run_intake for folder '%s': %s",
            folder_path,
            exc,
        )
        raise IntakeError(f"Unexpected error in run_intake: {exc}") from exc


def run_intake_from_bytes(
    uploaded_files: list[dict],
    case_id: str,
    preauth_output_dict: dict | None = None,
) -> dict:
    """Orchestrates in-memory files text extraction, schema mapping, and validation.

    Args:
        uploaded_files: List of dicts, each with keys 'filename', 'bytes', 'suffix'.
        case_id: Case identifier string.
        preauth_output_dict: Optional pre-auth output baseline dict.

    Returns:
        Structured discharge JSON dictionary.

    Raises:
        IntakeError: If any validation, extraction, or parsing logic fails.
    """
    try:
        # STEP 1 — Resolve preauth_reference:
        preauth_reference = case_id
        if preauth_output_dict is not None:
            session_id = preauth_output_dict.get("session_id")
            if session_id:
                preauth_reference = session_id
            else:
                logger.warning(
                    "session_id missing from preauth_output_dict, "
                    "falling back to case_id='%s' as preauth_reference.",
                    case_id,
                )

        # STEP 2 — Extract preauth_doc_keys from preauth_output_dict:
        preauth_doc_keys = []
        if preauth_output_dict is not None:
            try:
                selected = preauth_output_dict.get("selected_packages") or []
                if selected:
                    procedure_code = (
                        selected[0].get("validated", {}).get("procedure_code", "")
                    )
                    specialty_code = (
                        selected[0].get("validated", {}).get("specialty_code", "")
                    )
                    if procedure_code and specialty_code:
                        from phases.phase3_validator import SPECIALTY_CODE_TO_SHARD
                        from kb.loader import load_specialty_shard, get_procedure_from_shard
                        shard_filename = SPECIALTY_CODE_TO_SHARD.get(specialty_code)
                        if shard_filename:
                            shard_dict = load_specialty_shard(shard_filename)
                            proc_entry = get_procedure_from_shard(
                                procedure_code, shard_dict
                            )
                            if proc_entry:
                                for doc_type in ("preauth", "claim"):
                                    for doc in (
                                        proc_entry
                                        .get("mandatory_documents", {})
                                        .get(doc_type, [])
                                    ):
                                        key = doc.get("key")
                                        if key and key not in preauth_doc_keys:
                                            preauth_doc_keys.append(key)
            except Exception as e:
                logger.warning(
                    "Could not extract doc keys from preauth_output_dict: %s",
                    e,
                )

        # STEP 3 — Extract text from each uploaded file:
        files_text = {}
        for file in uploaded_files:
            filename = file["filename"]
            suffix = file["suffix"]
            raw_bytes = file["bytes"]
            try:
                if suffix == ".pdf":
                    temp_file = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
                    temp_file_path = temp_file.name
                    try:
                        temp_file.write(raw_bytes)
                        temp_file.close()
                        extracted_text = extract_text_from_pdf(temp_file_path)
                    finally:
                        try:
                            os.unlink(temp_file_path)
                        except Exception:
                            pass
                elif suffix == ".docx":
                    temp_file = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
                    temp_file_path = temp_file.name
                    try:
                        temp_file.write(raw_bytes)
                        temp_file.close()
                        extracted_text = extract_text_from_docx(temp_file_path)
                    finally:
                        try:
                            os.unlink(temp_file_path)
                        except Exception:
                            pass
                else:
                    logger.warning(
                        "Unsupported file type for filename '%s', skipping.",
                        filename,
                    )
                    continue

                if not extracted_text.strip():
                    logger.warning(
                        "Empty extraction for filename '%s', skipping.",
                        filename,
                    )
                    continue

                files_text[filename] = extracted_text
            except Exception as e:
                logger.warning(
                    "Extraction failed for filename '%s': %s, skipping.",
                    filename,
                    e,
                )

        if not files_text:
            raise IntakeError("All uploaded files produced empty text.")

        # STEP 4 — Parse via LLM:
        parsed_dict = parse_discharge_from_documents(
            files_text, preauth_reference, case_id, preauth_doc_keys
        )
        if parsed_dict is None:
            raise IntakeError(f"Discharge parser returned no result for case {case_id}.")

        # STEP 5 — Inject system fields:
        parsed_dict["preauth_input_path"] = f"tests/inputs/{case_id}.json"
        logger.info(
            "Injected preauth_input_path: 'tests/inputs/%s.json'", case_id
        )

        # STEP 6 — Save parsed dict to disk:
        output_dir = Path("tests/inputs")
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        output_file_path = output_dir / f"{case_id}_discharge_parsed.json"
        try:
            with open(output_file_path, "w", encoding="utf-8") as f:
                json.dump(parsed_dict, f, indent=2, ensure_ascii=False)
            logger.info(
                "Saved parsed discharge dictionary to: '%s'", output_file_path
            )
        except Exception as e:
            logger.error(
                "Failed to save parsed discharge dictionary to '%s': %s",
                output_file_path,
                e,
            )

        # STEP 7 — Validate schema:
        is_valid, missing_fields = validate_discharge_schema(parsed_dict)
        if not is_valid:
            raise IntakeError(
                f"Discharge schema validation failed. Missing fields: {missing_fields}"
            )

        # STEP 8 — Return parsed_dict
        return parsed_dict

    except IntakeError:
        raise
    except Exception as exc:
        logger.error(
            "Unexpected error in run_intake_from_bytes for case '%s': %s",
            case_id,
            exc,
        )
        raise IntakeError(f"Unexpected error in run_intake_from_bytes: {exc}") from exc
