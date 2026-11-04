"""Free/local fallback for document_intelligence_client.py's OCR + field
extraction, used when DOCUMENT_INTELLIGENCE_ENDPOINT/KEY aren't
configured, so candidates/{id}/verify-document keeps working without
Azure AI Document Intelligence.

HONEST LIMITATION, not papered over: Azure's "prebuilt-document" model is
a trained document-understanding model; this is a generic "label: value"
line parser over OCR'd/extracted text. It will do reasonably well on a
cleanly formatted text-layer PDF (a typed marksheet/certificate exported
to PDF, where "Name: John Doe" appears as literal text), and noticeably
worse on a photographed/scanned document with no text layer, an
unusual layout, or OCR errors -- there is no free local substitute for a
trained extraction model. Confidence scores here are a heuristic (how
clean the "label: value" match was), not a calibrated OCR confidence.

Two input paths:
- A PDF with an extractable text layer: parsed via PyPDF2 (already a
  dependency, same library ResumeParser.py/generate_questions.py use) --
  no extra install needed, works out of the box.
- A scanned/photographed document (image bytes, or an image-only PDF
  page with no text layer): needs a local OCR engine. Uses pytesseract +
  Pillow (and pdf2image for image-only PDFs) IF installed; otherwise
  raises a clear RuntimeError naming exactly what to install, rather
  than silently returning nothing.
"""

import io
import re


_FIELD_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 ./'()-]{1,40}?)\s*([:\-])\s*(\S.{0,200})\s*$")


def _parse_fields_from_text(text: str) -> dict:
    """Generic 'label: value' / 'label - value' line parser -- the local
    equivalent of Azure's generic key-value field detection. A colon
    separator is a cleaner, less ambiguous signal than a dash (which can
    also be a bullet or a date range), so it gets a higher heuristic
    confidence."""
    fields = {}
    for line in text.splitlines():
        match = _FIELD_LINE_RE.match(line)
        if not match:
            continue
        label, separator, value = match.group(1).strip(), match.group(2), match.group(3).strip()
        if not label or not value:
            continue
        fields[label] = {"value": value, "confidence": 0.85 if separator == ":" else 0.6}
    return fields


def _extract_text_from_pdf(file_bytes: bytes) -> str:
    from PyPDF2 import PdfReader

    reader = PdfReader(io.BytesIO(file_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_text_via_ocr(file_bytes: bytes, is_pdf: bool) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        raise RuntimeError(
            "This document has no extractable text layer (looks like a scanned/photographed "
            "document), which needs real OCR -- not installed in this environment. Install "
            "`pip install pytesseract pillow` plus the Tesseract OCR system binary "
            "(https://github.com/tesseract-ocr/tesseract#installing-tesseract), or configure "
            "DOCUMENT_INTELLIGENCE_ENDPOINT/DOCUMENT_INTELLIGENCE_KEY to use Azure instead."
        )

    if not is_pdf:
        image = Image.open(io.BytesIO(file_bytes))
        return pytesseract.image_to_string(image)

    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        raise RuntimeError(
            "This PDF has no extractable text layer (looks like a scanned image saved as PDF), "
            "which needs `pip install pdf2image` (plus the poppler system binary) to rasterize "
            "pages for OCR, or configure DOCUMENT_INTELLIGENCE_ENDPOINT/DOCUMENT_INTELLIGENCE_KEY "
            "to use Azure instead."
        )
    pages = convert_from_bytes(file_bytes)
    return "\n".join(pytesseract.image_to_string(page) for page in pages)


def analyze_document_locally(file_bytes: bytes) -> dict:
    """Returns the same {field_name: {"value": str, "confidence": float}}
    shape as document_intelligence_client.analyze_document(), computed
    entirely locally -- zero Azure dependency, zero cost. See module
    docstring for the real, honest accuracy trade-off versus Azure AI
    Document Intelligence."""
    is_pdf = file_bytes[:4] == b"%PDF"
    text = _extract_text_from_pdf(file_bytes) if is_pdf else ""
    if not text.strip():
        text = _extract_text_via_ocr(file_bytes, is_pdf)
    return _parse_fields_from_text(text)
