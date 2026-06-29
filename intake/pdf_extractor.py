import logging
# pyrefly: ignore [missing-import]
import pdfplumber
# pyrefly: ignore [missing-import]
from pdf2image import convert_from_path
# pyrefly: ignore [missing-import]
import pytesseract

logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_path: str) -> str:
    """Extract raw plain text from a PDF file.

    Attempts text extraction via pdfplumber first. If the resulting text is
    fewer than 30 words, falls back to OCR via pdf2image and pytesseract.

    Args:
        file_path: Absolute or relative path to the PDF file.

    Returns:
        The extracted text as a string, or an empty string on failure.
        Never raises exceptions.
    """
    logger.info(
        "Starting text extraction from PDF: '%s' using pdfplumber",
        file_path,
    )
    try:
        pages_text = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text)

        extracted_text = "\n\n".join(pages_text)

        # Fallback to OCR if extracted text is fewer than 30 words
        if len(extracted_text.strip().split()) < 30:
            logger.warning(
                "Near-empty text (%d words) from pdfplumber for '%s'. Falling back to OCR.",
                len(extracted_text.strip().split()),
                file_path,
            )
            logger.info(
                "Starting text extraction from PDF: '%s' using OCR fallback",
                file_path,
            )

            images = convert_from_path(file_path)
            ocr_pages = []
            for img in images:
                ocr_text = pytesseract.image_to_string(img)
                if ocr_text:
                    ocr_pages.append(ocr_text)

            return "\n\n".join(ocr_pages)

        return extracted_text

    except Exception as exc:
        logger.error(
            "Error extracting text from PDF '%s': %s",
            file_path,
            exc,
        )
        return ""
