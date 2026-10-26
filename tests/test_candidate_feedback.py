import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import candidate_feedback  # noqa: E402


class TestIsEligibleForFeedbackRequest:
    def test_golden_approved_candidate_is_eligible(self):
        assert candidate_feedback.is_eligible_for_feedback_request({"ranking_tier": "Recommended for Fast-Track"}) is True

    def test_golden_rejected_candidate_is_eligible(self):
        assert candidate_feedback.is_eligible_for_feedback_request({"ranking_tier": "Rejected"}) is True

    def test_edge_to_be_considered_is_not_eligible(self):
        assert candidate_feedback.is_eligible_for_feedback_request({"ranking_tier": "To be Considered"}) is False

    def test_adversarial_missing_ranking_tier_is_not_eligible(self):
        assert candidate_feedback.is_eligible_for_feedback_request({}) is False

    def test_adversarial_none_candidate_is_not_eligible(self):
        assert candidate_feedback.is_eligible_for_feedback_request(None) is False


class TestSanitizeComment:
    def test_golden_plain_text_passes_through(self):
        assert candidate_feedback.sanitize_comment("Great experience overall.") == "Great experience overall."

    def test_edge_none_comment_becomes_empty_string(self):
        assert candidate_feedback.sanitize_comment(None) == ""

    def test_edge_empty_string_stays_empty(self):
        assert candidate_feedback.sanitize_comment("") == ""

    def test_adversarial_control_characters_stripped(self):
        result = candidate_feedback.sanitize_comment("Hello\x00\x07World")
        assert "\x00" not in result and "\x07" not in result

    def test_adversarial_overlong_comment_truncated(self):
        result = candidate_feedback.sanitize_comment("x" * 5000)
        assert len(result) == candidate_feedback.MAX_COMMENT_LENGTH

    def test_golden_newlines_and_tabs_preserved(self):
        result = candidate_feedback.sanitize_comment("Line one\nLine two\tTabbed")
        assert "\n" in result and "\t" in result


def _valid_ratings():
    return {c: 4 for c in candidate_feedback.FEEDBACK_CATEGORIES}


class TestValidateFeedbackSubmission:
    def test_golden_all_categories_rated_is_valid(self):
        assert candidate_feedback.validate_feedback_submission({"ratings": _valid_ratings()}) == []

    def test_golden_optional_comment_is_valid(self):
        errors = candidate_feedback.validate_feedback_submission({"ratings": _valid_ratings(), "comment": "Good."})
        assert errors == []

    def test_adversarial_not_a_dict_is_rejected(self):
        assert candidate_feedback.validate_feedback_submission(None) != []
        assert candidate_feedback.validate_feedback_submission("not a dict") != []

    def test_adversarial_missing_ratings_key_is_rejected(self):
        errors = candidate_feedback.validate_feedback_submission({})
        assert len(errors) == 1

    def test_adversarial_missing_one_category_is_rejected(self):
        ratings = _valid_ratings()
        del ratings["transparency"]
        errors = candidate_feedback.validate_feedback_submission({"ratings": ratings})
        assert any("transparency" in e for e in errors)

    def test_adversarial_rating_out_of_range_is_rejected(self):
        ratings = _valid_ratings()
        ratings["communication"] = 6
        errors = candidate_feedback.validate_feedback_submission({"ratings": ratings})
        assert any("communication" in e for e in errors)

    def test_adversarial_rating_zero_is_rejected(self):
        ratings = _valid_ratings()
        ratings["communication"] = 0
        errors = candidate_feedback.validate_feedback_submission({"ratings": ratings})
        assert any("communication" in e for e in errors)

    def test_adversarial_non_integer_rating_is_rejected(self):
        ratings = _valid_ratings()
        ratings["communication"] = "5"
        errors = candidate_feedback.validate_feedback_submission({"ratings": ratings})
        assert any("communication" in e for e in errors)

    def test_adversarial_boolean_rating_is_rejected(self):
        ratings = _valid_ratings()
        ratings["communication"] = True
        errors = candidate_feedback.validate_feedback_submission({"ratings": ratings})
        assert any("communication" in e for e in errors)

    def test_adversarial_unknown_category_is_rejected(self):
        ratings = _valid_ratings()
        ratings["unknown_category"] = 5
        errors = candidate_feedback.validate_feedback_submission({"ratings": ratings})
        assert any("unknown_category" in e for e in errors)

    def test_adversarial_non_string_comment_is_rejected(self):
        errors = candidate_feedback.validate_feedback_submission({"ratings": _valid_ratings(), "comment": 123})
        assert any("comment" in e for e in errors)


class TestSummarizeFeedback:
    def test_golden_computes_category_averages(self):
        records = [
            {"status": "submitted", "ratings": {"communication": 4, "application_process": 5}},
            {"status": "submitted", "ratings": {"communication": 2, "application_process": 3}},
        ]
        summary = candidate_feedback.summarize_feedback(records)
        assert summary["category_averages"]["communication"] == 3.0
        assert summary["category_averages"]["application_process"] == 4.0
        assert summary["submitted_count"] == 2
        assert summary["requested_count"] == 2
        assert summary["response_rate"] == 1.0

    def test_edge_no_submissions_yet_reports_none_not_zero(self):
        records = [{"status": "requested", "ratings": None}]
        summary = candidate_feedback.summarize_feedback(records)
        assert summary["category_averages"]["communication"] is None
        assert summary["submitted_count"] == 0
        assert summary["pending_count"] == 1

    def test_edge_empty_records_has_no_response_rate(self):
        summary = candidate_feedback.summarize_feedback([])
        assert summary["response_rate"] is None
        assert summary["requested_count"] == 0

    def test_adversarial_mixed_requested_and_submitted(self):
        records = [
            {"status": "submitted", "ratings": {"overall_experience": 5}},
            {"status": "requested", "ratings": None},
            {"status": "requested", "ratings": None},
        ]
        summary = candidate_feedback.summarize_feedback(records)
        assert summary["requested_count"] == 3
        assert summary["submitted_count"] == 1
        assert summary["pending_count"] == 2
        assert summary["response_rate"] == round(1 / 3, 4)
