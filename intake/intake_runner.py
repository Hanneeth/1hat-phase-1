import json
import logging
import os
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
        for item in folder.iterdir():
            if item.is_file():
                if item.name.startswith("."):
                    continue
                suffix = item.suffix.lower()
                if suffix in (".pdf", ".docx"):
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
            files_text, preauth_reference, case_id
        )

        if parsed_dict is None:
            logger.error(
                "Discharge parser returned no result for case %s", case_id
            )
            raise IntakeError(
                f"Discharge parser returned no result for case {case_id}."
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
