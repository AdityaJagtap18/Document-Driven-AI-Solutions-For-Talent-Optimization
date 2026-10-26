import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tokenomics  # noqa: E402


class TestValidateConfig:
    def test_golden_valid_config(self):
        assert tokenomics.validate_config({"monthly_budget_usd": 100, "warning_threshold_pct": 80}) == []

    def test_golden_budget_disabled_is_valid(self):
        assert tokenomics.validate_config({"monthly_budget_usd": None, "warning_threshold_pct": 50}) == []

    def test_edge_threshold_boundaries_valid(self):
        assert tokenomics.validate_config({"monthly_budget_usd": 1, "warning_threshold_pct": 1}) == []
        assert tokenomics.validate_config({"monthly_budget_usd": 1, "warning_threshold_pct": 100}) == []

    def test_adversarial_negative_budget_rejected(self):
        errors = tokenomics.validate_config({"monthly_budget_usd": -5, "warning_threshold_pct": 80})
        assert len(errors) == 1
        assert "monthly_budget_usd" in errors[0]

    def test_adversarial_zero_budget_rejected(self):
        errors = tokenomics.validate_config({"monthly_budget_usd": 0, "warning_threshold_pct": 80})
        assert len(errors) == 1

    def test_adversarial_threshold_out_of_range_rejected(self):
        assert len(tokenomics.validate_config({"monthly_budget_usd": 10, "warning_threshold_pct": 0})) == 1
        assert len(tokenomics.validate_config({"monthly_budget_usd": 10, "warning_threshold_pct": 101})) == 1

    def test_adversarial_threshold_non_integer_rejected(self):
        errors = tokenomics.validate_config({"monthly_budget_usd": 10, "warning_threshold_pct": 80.5})
        assert len(errors) == 1

    def test_adversarial_threshold_missing_rejected(self):
        errors = tokenomics.validate_config({"monthly_budget_usd": 10})
        assert len(errors) == 1

    def test_adversarial_both_fields_invalid_reports_both(self):
        errors = tokenomics.validate_config({"monthly_budget_usd": -1, "warning_threshold_pct": 200})
        assert len(errors) == 2


class TestIsCurrentUtcMonth:
    def test_golden_same_month_matches(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        assert tokenomics._is_current_utc_month("2026-10-01T00:00:00+00:00", now) is True

    def test_edge_different_month_does_not_match(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        assert tokenomics._is_current_utc_month("2026-09-30T23:59:59+00:00", now) is False

    def test_edge_naive_timestamp_treated_as_utc(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        assert tokenomics._is_current_utc_month("2026-10-01T00:00:00", now) is True

    def test_adversarial_malformed_timestamp_returns_false(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        assert tokenomics._is_current_utc_month("not-a-timestamp", now) is False

    def test_adversarial_missing_timestamp_returns_false(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        assert tokenomics._is_current_utc_month(None, now) is False


class TestComputeMonthlyUsage:
    NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)

    def test_golden_aggregates_current_month_records(self):
        records = [
            {"timestamp": "2026-10-01T00:00:00+00:00", "total_tokens": 100, "cost_usd": 0.01, "label": "scoring"},
            {"timestamp": "2026-10-20T00:00:00+00:00", "total_tokens": 200, "cost_usd": 0.02, "label": "scoring"},
        ]
        usage = tokenomics.compute_monthly_usage(records, now=self.NOW)
        assert usage["period"] == "2026-10"
        assert usage["call_count"] == 2
        assert usage["total_tokens"] == 300
        assert usage["total_cost_usd"] == 0.03
        assert usage["unknown_cost_call_count"] == 0
        assert usage["by_label"]["scoring"]["calls"] == 2

    def test_edge_excludes_other_month_records(self):
        records = [
            {"timestamp": "2026-09-30T23:59:59+00:00", "total_tokens": 999, "cost_usd": 9.99, "label": "scoring"},
            {"timestamp": "2026-10-01T00:00:00+00:00", "total_tokens": 100, "cost_usd": 0.01, "label": "scoring"},
        ]
        usage = tokenomics.compute_monthly_usage(records, now=self.NOW)
        assert usage["call_count"] == 1
        assert usage["total_tokens"] == 100

    def test_golden_unknown_cost_tracked_separately_not_as_zero(self):
        records = [
            {"timestamp": "2026-10-05T00:00:00+00:00", "total_tokens": 50, "cost_usd": None, "label": "embedding"},
            {"timestamp": "2026-10-06T00:00:00+00:00", "total_tokens": 50, "cost_usd": 0.05, "label": "embedding"},
        ]
        usage = tokenomics.compute_monthly_usage(records, now=self.NOW)
        assert usage["unknown_cost_call_count"] == 1
        assert usage["total_cost_usd"] == 0.05
        assert usage["by_label"]["embedding"]["unknown_cost_calls"] == 1

    def test_edge_empty_records_returns_zeroed_usage(self):
        usage = tokenomics.compute_monthly_usage([], now=self.NOW)
        assert usage["call_count"] == 0
        assert usage["total_cost_usd"] == 0.0
        assert usage["unknown_cost_call_count"] == 0
        assert usage["by_label"] == {}

    def test_adversarial_missing_label_falls_back_to_unknown(self):
        records = [{"timestamp": "2026-10-05T00:00:00+00:00", "total_tokens": 10, "cost_usd": 0.001}]
        usage = tokenomics.compute_monthly_usage(records, now=self.NOW)
        assert "unknown" in usage["by_label"]

    def test_adversarial_missing_total_tokens_treated_as_zero(self):
        records = [{"timestamp": "2026-10-05T00:00:00+00:00", "cost_usd": 0.01, "label": "scoring"}]
        usage = tokenomics.compute_monthly_usage(records, now=self.NOW)
        assert usage["total_tokens"] == 0
        assert usage["call_count"] == 1


class TestCheckBudgetStatus:
    def test_golden_not_configured_when_no_budget(self):
        usage = {"total_cost_usd": 5.0, "unknown_cost_call_count": 0}
        result = tokenomics.check_budget_status(usage, {"monthly_budget_usd": None, "warning_threshold_pct": 80})
        assert result["configured"] is False
        assert result["status"] == "not_configured"
        assert result["percent_used"] is None
        assert result["spent_usd"] == 5.0

    def test_golden_ok_status_below_threshold(self):
        usage = {"total_cost_usd": 10.0, "unknown_cost_call_count": 0}
        result = tokenomics.check_budget_status(usage, {"monthly_budget_usd": 100, "warning_threshold_pct": 80})
        assert result["configured"] is True
        assert result["status"] == "ok"
        assert result["percent_used"] == 10.0

    def test_edge_warning_at_exact_threshold(self):
        usage = {"total_cost_usd": 80.0, "unknown_cost_call_count": 0}
        result = tokenomics.check_budget_status(usage, {"monthly_budget_usd": 100, "warning_threshold_pct": 80})
        assert result["status"] == "warning"

    def test_edge_exceeded_at_exactly_100_percent(self):
        usage = {"total_cost_usd": 100.0, "unknown_cost_call_count": 0}
        result = tokenomics.check_budget_status(usage, {"monthly_budget_usd": 100, "warning_threshold_pct": 80})
        assert result["status"] == "exceeded"

    def test_adversarial_spend_far_exceeds_budget(self):
        usage = {"total_cost_usd": 999.0, "unknown_cost_call_count": 0}
        result = tokenomics.check_budget_status(usage, {"monthly_budget_usd": 100, "warning_threshold_pct": 80})
        assert result["status"] == "exceeded"
        assert result["percent_used"] == 999.0

    def test_adversarial_missing_threshold_uses_default(self):
        usage = {"total_cost_usd": 85.0, "unknown_cost_call_count": 0}
        result = tokenomics.check_budget_status(usage, {"monthly_budget_usd": 100})
        assert result["status"] == "warning"

    def test_golden_unknown_cost_count_passed_through(self):
        usage = {"total_cost_usd": 10.0, "unknown_cost_call_count": 3}
        result = tokenomics.check_budget_status(usage, {"monthly_budget_usd": 100, "warning_threshold_pct": 80})
        assert result["unknown_cost_call_count"] == 3
