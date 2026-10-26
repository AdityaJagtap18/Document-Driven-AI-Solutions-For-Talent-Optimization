"""Tests for candidate_erasure.py -- F-14's minimal slice (Cosmos-scoped
candidate data erasure). All repositories.* calls are monkeypatched --
these never touch a real network, same convention as every other test
file in this repo."""

import candidate_erasure
import repositories


class TestLocateCandidateData:
    def test_golden_reports_real_counts_for_every_container(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: {"candidate_id": cid} if cid == "c-1" else None)
        monkeypatch.setattr(repositories, "list_interview_transcripts", lambda cid: [{"id": "t-1"}, {"id": "t-2"}])
        monkeypatch.setattr(repositories, "list_scoring_history", lambda cid: [{"id": "s-1"}])
        monkeypatch.setattr(repositories, "list_comms_deliveries", lambda cid, limit=50: [])
        monkeypatch.setattr(repositories, "list_audit_log", lambda cid: [{"id": "a-1"}, {"id": "a-2"}, {"id": "a-3"}])
        monkeypatch.setattr(repositories, "find_bulk_decisions_referencing_candidate", lambda cid: [])

        report = candidate_erasure.locate_candidate_data("c-1")
        assert report["will_erase"] == {"candidates": 1, "interview_transcripts": 2, "scoring_history": 1, "comms_delivery_log": 0}
        assert report["retained"]["audit_log"]["count"] == 3
        assert "reason" in report["retained"]["audit_log"]
        assert report["found_entangled_not_modified"] == {}

    def test_golden_nonexistent_candidate_reports_all_zeros_not_an_error(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: None)
        monkeypatch.setattr(repositories, "list_interview_transcripts", lambda cid: [])
        monkeypatch.setattr(repositories, "list_scoring_history", lambda cid: [])
        monkeypatch.setattr(repositories, "list_comms_deliveries", lambda cid, limit=50: [])
        monkeypatch.setattr(repositories, "list_audit_log", lambda cid: [])
        monkeypatch.setattr(repositories, "find_bulk_decisions_referencing_candidate", lambda cid: [])

        report = candidate_erasure.locate_candidate_data("does-not-exist")
        assert report["will_erase"]["candidates"] == 0

    def test_golden_entangled_bulk_decision_is_reported_not_hidden(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: None)
        monkeypatch.setattr(repositories, "list_interview_transcripts", lambda cid: [])
        monkeypatch.setattr(repositories, "list_scoring_history", lambda cid: [])
        monkeypatch.setattr(repositories, "list_comms_deliveries", lambda cid, limit=50: [])
        monkeypatch.setattr(repositories, "list_audit_log", lambda cid: [])
        monkeypatch.setattr(repositories, "find_bulk_decisions_referencing_candidate", lambda cid: [{"id": "bd-1", "decision": "accept", "status": "pending"}])

        report = candidate_erasure.locate_candidate_data("c-1")
        assert report["found_entangled_not_modified"]["bulk_decisions"] == [{"id": "bd-1", "decision": "accept", "status": "pending"}]

    def test_golden_out_of_scope_stores_always_disclosed(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: None)
        monkeypatch.setattr(repositories, "list_interview_transcripts", lambda cid: [])
        monkeypatch.setattr(repositories, "list_scoring_history", lambda cid: [])
        monkeypatch.setattr(repositories, "list_comms_deliveries", lambda cid, limit=50: [])
        monkeypatch.setattr(repositories, "list_audit_log", lambda cid: [])
        monkeypatch.setattr(repositories, "find_bulk_decisions_referencing_candidate", lambda cid: [])

        report = candidate_erasure.locate_candidate_data("c-1")
        store_names = [s["store"] for s in report["out_of_scope_stores"]]
        assert any("Blob Storage" in s for s in store_names)
        assert any("semantic-library" in s for s in store_names)


class TestEraseCandidate:
    def test_golden_erases_every_covered_container_and_verifies(self, monkeypatch):
        deleted = {"candidates": False, "transcripts": False, "scoring": False, "comms": False}

        def fake_delete_candidate(cid):
            deleted["candidates"] = True
            return True

        monkeypatch.setattr(repositories, "delete_candidate", fake_delete_candidate)
        monkeypatch.setattr(repositories, "delete_interview_transcripts_for_candidate", lambda cid: 2)
        monkeypatch.setattr(repositories, "delete_scoring_history_for_candidate", lambda cid: 3)
        monkeypatch.setattr(repositories, "delete_comms_deliveries_for_candidate", lambda cid: 1)
        # Post-deletion verification re-queries -- simulate everything now empty.
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: None)
        monkeypatch.setattr(repositories, "list_interview_transcripts", lambda cid: [])
        monkeypatch.setattr(repositories, "list_scoring_history", lambda cid: [])
        monkeypatch.setattr(repositories, "list_comms_deliveries", lambda cid, limit=50: [])
        monkeypatch.setattr(repositories, "list_audit_log", lambda cid: [{"id": "a-1"}])
        monkeypatch.setattr(repositories, "find_bulk_decisions_referencing_candidate", lambda cid: [])

        report = candidate_erasure.erase_candidate("c-1")
        assert report["erased"] == {"candidates": 1, "interview_transcripts": 2, "scoring_history": 3, "comms_delivery_log": 1}
        assert report["verification"]["candidates"] == "0 remaining"
        assert report["verification"]["interview_transcripts"] == "0 remaining"
        assert report["retained"]["audit_log"]["count"] == 1
        assert deleted["candidates"] is True

    def test_adversarial_verification_catches_an_incomplete_erasure(self, monkeypatch):
        """If a real delete silently failed to remove everything, the
        verification step must say so -- never report success it didn't
        actually confirm."""
        monkeypatch.setattr(repositories, "delete_candidate", lambda cid: True)
        monkeypatch.setattr(repositories, "delete_interview_transcripts_for_candidate", lambda cid: 1)
        monkeypatch.setattr(repositories, "delete_scoring_history_for_candidate", lambda cid: 0)
        monkeypatch.setattr(repositories, "delete_comms_deliveries_for_candidate", lambda cid: 0)
        # Simulate a real record STILL present despite the delete call succeeding.
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: {"candidate_id": cid})
        monkeypatch.setattr(repositories, "list_interview_transcripts", lambda cid: [])
        monkeypatch.setattr(repositories, "list_scoring_history", lambda cid: [])
        monkeypatch.setattr(repositories, "list_comms_deliveries", lambda cid, limit=50: [])
        monkeypatch.setattr(repositories, "list_audit_log", lambda cid: [])
        monkeypatch.setattr(repositories, "find_bulk_decisions_referencing_candidate", lambda cid: [])

        report = candidate_erasure.erase_candidate("c-1")
        assert "STILL PRESENT" in report["verification"]["candidates"]

    def test_golden_nonexistent_candidate_is_a_safe_no_op(self, monkeypatch):
        monkeypatch.setattr(repositories, "delete_candidate", lambda cid: False)
        monkeypatch.setattr(repositories, "delete_interview_transcripts_for_candidate", lambda cid: 0)
        monkeypatch.setattr(repositories, "delete_scoring_history_for_candidate", lambda cid: 0)
        monkeypatch.setattr(repositories, "delete_comms_deliveries_for_candidate", lambda cid: 0)
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: None)
        monkeypatch.setattr(repositories, "list_interview_transcripts", lambda cid: [])
        monkeypatch.setattr(repositories, "list_scoring_history", lambda cid: [])
        monkeypatch.setattr(repositories, "list_comms_deliveries", lambda cid, limit=50: [])
        monkeypatch.setattr(repositories, "list_audit_log", lambda cid: [])
        monkeypatch.setattr(repositories, "find_bulk_decisions_referencing_candidate", lambda cid: [])

        report = candidate_erasure.erase_candidate("does-not-exist")
        assert report["erased"]["candidates"] == 0

    def test_golden_entangled_bulk_decision_still_reported_after_erasure(self, monkeypatch):
        monkeypatch.setattr(repositories, "delete_candidate", lambda cid: True)
        monkeypatch.setattr(repositories, "delete_interview_transcripts_for_candidate", lambda cid: 0)
        monkeypatch.setattr(repositories, "delete_scoring_history_for_candidate", lambda cid: 0)
        monkeypatch.setattr(repositories, "delete_comms_deliveries_for_candidate", lambda cid: 0)
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: None)
        monkeypatch.setattr(repositories, "list_interview_transcripts", lambda cid: [])
        monkeypatch.setattr(repositories, "list_scoring_history", lambda cid: [])
        monkeypatch.setattr(repositories, "list_comms_deliveries", lambda cid, limit=50: [])
        monkeypatch.setattr(repositories, "list_audit_log", lambda cid: [])
        monkeypatch.setattr(repositories, "find_bulk_decisions_referencing_candidate", lambda cid: [{"id": "bd-1"}])

        report = candidate_erasure.erase_candidate("c-1")
        assert report["found_entangled_not_modified"]["bulk_decisions"] == [{"id": "bd-1"}]

    def test_golden_out_of_scope_stores_disclosed_in_erasure_report_too(self, monkeypatch):
        monkeypatch.setattr(repositories, "delete_candidate", lambda cid: True)
        monkeypatch.setattr(repositories, "delete_interview_transcripts_for_candidate", lambda cid: 0)
        monkeypatch.setattr(repositories, "delete_scoring_history_for_candidate", lambda cid: 0)
        monkeypatch.setattr(repositories, "delete_comms_deliveries_for_candidate", lambda cid: 0)
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: None)
        monkeypatch.setattr(repositories, "list_interview_transcripts", lambda cid: [])
        monkeypatch.setattr(repositories, "list_scoring_history", lambda cid: [])
        monkeypatch.setattr(repositories, "list_comms_deliveries", lambda cid, limit=50: [])
        monkeypatch.setattr(repositories, "list_audit_log", lambda cid: [])
        monkeypatch.setattr(repositories, "find_bulk_decisions_referencing_candidate", lambda cid: [])

        report = candidate_erasure.erase_candidate("c-1")
        assert len(report["out_of_scope_stores"]) >= 5
