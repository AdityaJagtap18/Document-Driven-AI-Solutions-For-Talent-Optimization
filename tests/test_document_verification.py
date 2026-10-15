from document_verification import (
    fields_match, cross_check_document, confidence_flags, verification_summary,
)


class TestFieldsMatch:
    def test_golden_exact_match_case_insensitive(self):
        assert fields_match("Priya Sharma", "priya sharma") is True

    def test_golden_whitespace_normalized(self):
        assert fields_match("B.Tech  Computer   Science", "B.Tech Computer Science") is True

    def test_golden_one_contains_the_other(self):
        assert fields_match("B.Tech Computer Science", "Bachelor of Technology in Computer Science, B.Tech Computer Science specialization") is True

    def test_edge_both_empty_not_a_match(self):
        assert fields_match("", "") is False
        assert fields_match(None, None) is False

    def test_edge_one_empty_not_a_match(self):
        assert fields_match("Priya Sharma", "") is False
        assert fields_match(None, "Priya Sharma") is False

    def test_adversarial_genuinely_different_values(self):
        assert fields_match("8.9 CGPA", "6.2 CGPA") is False
        assert fields_match("Mumbai University", "Delhi University") is False


class TestCrossCheckDocument:
    def test_golden_all_match(self):
        extracted = {"Name": "Priya Sharma", "Degree": "B.Tech Computer Science", "CGPA": "8.9"}
        claimed = {"Name": "Priya Sharma", "Degree": "B.Tech Computer Science", "CGPA": "8.9"}
        result = cross_check_document(extracted, claimed, ["Name", "Degree", "CGPA"])
        assert result["matches"] == ["Name", "Degree", "CGPA"]
        assert result["mismatches"] == []
        assert result["match_rate"] == 1.0

    def test_golden_partial_mismatch(self):
        extracted = {"Name": "Priya Sharma", "CGPA": "6.2"}
        claimed = {"Name": "Priya Sharma", "CGPA": "8.9"}
        result = cross_check_document(extracted, claimed, ["Name", "CGPA"])
        assert result["matches"] == ["Name"]
        assert len(result["mismatches"]) == 1
        assert result["mismatches"][0]["field"] == "CGPA"
        assert result["mismatches"][0]["extracted"] == "6.2"
        assert result["mismatches"][0]["claimed"] == "8.9"
        assert result["match_rate"] == 0.5

    def test_edge_field_missing_from_extraction_counts_as_mismatch(self):
        extracted = {"Name": "Priya Sharma"}  # Degree never extracted
        claimed = {"Name": "Priya Sharma", "Degree": "B.Tech"}
        result = cross_check_document(extracted, claimed, ["Name", "Degree"])
        assert "Degree" in [m["field"] for m in result["mismatches"]]

    def test_edge_no_fields_to_check(self):
        result = cross_check_document({}, {}, [])
        assert result == {"matches": [], "mismatches": [], "match_rate": 0.0}


class TestConfidenceFlags:
    def test_golden_flags_below_threshold(self):
        confidences = {"Name": 0.95, "CGPA": 0.42, "Degree": 0.68}
        assert confidence_flags(confidences, threshold=0.7) == ["CGPA", "Degree"]

    def test_edge_none_below_threshold(self):
        assert confidence_flags({"Name": 0.95, "CGPA": 0.88}, threshold=0.7) == []

    def test_edge_none_confidence_value_excluded(self):
        # a field DI didn't return a confidence for at all shouldn't crash or false-flag
        assert confidence_flags({"Name": None, "CGPA": 0.9}, threshold=0.7) == []

    def test_golden_results_sorted(self):
        confidences = {"Z": 0.1, "A": 0.2}
        assert confidence_flags(confidences, threshold=0.5) == ["A", "Z"]


class TestVerificationSummary:
    def test_golden_all_ok(self):
        result = cross_check_document({"Name": "A"}, {"Name": "A"}, ["Name"])
        assert verification_summary(result, []) == "OK: all checked fields match, no low-confidence extractions"

    def test_golden_mismatch_takes_priority_over_confidence_flags(self):
        result = cross_check_document({"CGPA": "6.2"}, {"CGPA": "8.9"}, ["CGPA"])
        summary = verification_summary(result, ["Name"])
        assert summary.startswith("REVIEW: field mismatch(es) found")
        assert "CGPA" in summary

    def test_golden_confidence_only_flag(self):
        result = cross_check_document({"Name": "A"}, {"Name": "A"}, ["Name"])
        summary = verification_summary(result, ["CGPA"])
        assert "low OCR confidence" in summary
        assert "not a confirmed forgery signal" in summary
