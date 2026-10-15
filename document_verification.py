"""Pure, testable logic for certificate/marksheet verification: cross-
checking Azure AI Document Intelligence's extracted fields against what a
candidate claims on their resume/application, and flagging low-confidence
extractions as worth a closer human look. No Azure dependency here — see
document_intelligence_client.py for the actual OCR call.

Scope, confirmed with the user earlier in this engagement: internal
consistency + tamper/forgery signals only. No external institution/board
verification (e.g. DigiLocker, a university's own records API) — that's
explicitly out of scope for this pass, tracked as future work in the
tracker.
"""

import re


def _normalize(value) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def fields_match(extracted_value, claimed_value) -> bool:
    """Loose match: normalized exact match, or one value contains the
    other (handles e.g. "B.Tech Computer Science" vs "Bachelor of
    Technology in Computer Science" partially overlapping). Deliberately
    conservative, not fuzzy/edit-distance matching — a false "match" here
    is worse than a false "needs review": both empty/missing values count
    as NOT a match, since there's nothing to actually verify."""
    a = _normalize(extracted_value)
    b = _normalize(claimed_value)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def cross_check_document(extracted_fields: dict, claimed: dict, fields_to_check: list) -> dict:
    """extracted_fields / claimed: {field_name: value}, same field names
    expected in both. Returns {"matches": [field, ...], "mismatches":
    [{"field", "extracted", "claimed"}, ...], "match_rate": float 0-1}."""
    matches = []
    mismatches = []
    for field in fields_to_check:
        extracted_value = extracted_fields.get(field)
        claimed_value = claimed.get(field)
        if fields_match(extracted_value, claimed_value):
            matches.append(field)
        else:
            mismatches.append({"field": field, "extracted": extracted_value, "claimed": claimed_value})
    total = len(fields_to_check)
    return {
        "matches": matches,
        "mismatches": mismatches,
        "match_rate": round(len(matches) / total, 2) if total else 0.0,
    }


def confidence_flags(field_confidences: dict, threshold: float = 0.7) -> list:
    """field_confidences: {field_name: confidence_score (0.0-1.0)} from
    Document Intelligence's per-field confidence. Returns field names
    below threshold, sorted. This is a proxy for "hard to read / possibly
    altered", NOT a real forgery detector — Document Intelligence has no
    purpose-built tamper detection, and low OCR confidence is the only
    signal available from a generic field-extraction call without a
    custom-trained model or a dedicated fraud-detection service.
    Documented limitation, not oversold as something it isn't."""
    return sorted(field for field, conf in field_confidences.items() if conf is not None and conf < threshold)


def verification_summary(cross_check_result: dict, confidence_flagged_fields: list) -> str:
    """One human-readable recommendation line for the Human Review Queue."""
    if cross_check_result["mismatches"]:
        mismatched_names = ", ".join(m["field"] for m in cross_check_result["mismatches"])
        return f"REVIEW: field mismatch(es) found — {mismatched_names}"
    if confidence_flagged_fields:
        flagged_names = ", ".join(confidence_flagged_fields)
        return f"REVIEW: low OCR confidence on — {flagged_names} (verify manually, not a confirmed forgery signal)"
    return "OK: all checked fields match, no low-confidence extractions"
