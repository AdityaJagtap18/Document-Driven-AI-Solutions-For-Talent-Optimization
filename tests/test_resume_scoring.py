import pytest

from resume_scoring import (
    certificate_score, project_score, skill_score, contribute_score,
    soft_skill_score, industry_penalty, culture_score, effort_score,
    penalty_score, jd_match_score, experience_score, candidate_type_score,
    classify_candidate, semantic_match_score, semantic_match_score_or_fallback,
)

CONFIG = {
    "skill_rules": {"match": 1.0, "missing": -0.5},
    "experience_score": {"thresholds": [3, 7], "scores": [1, 2.5, 4]},
    "candidate_type_score": {"junior": 1.5, "mid-level": 4, "senior": 5},
    "culture_fit_score": {"low": -0.25, "medium": 1.25, "high": 3},
    "effort_needed_score": {"high": -0.25, "medium": 1, "low": 3},
}


class TestCertificateScore:
    def test_golden_valid_list(self):
        assert certificate_score("['AWS Cert', 'Azure Cert']") == 2

    def test_edge_empty_list(self):
        assert certificate_score("[]") == 0

    def test_adversarial_malformed_syntax(self):
        assert certificate_score("[") == 0

    def test_adversarial_not_a_list(self):
        assert certificate_score("'just a string'") == 0


class TestProjectScore:
    def test_golden_high_relevance_counted(self):
        projects = "[{'Project Name': 'A', 'Relevance to JD': 'High'}, {'Project Name': 'B', 'Relevance to JD': 'Medium'}]"
        assert project_score(projects) == 5

    def test_edge_no_high_relevance(self):
        projects = "[{'Project Name': 'A', 'Relevance to JD': 'Low'}]"
        assert project_score(projects) == 0

    def test_adversarial_missing_relevance_key(self):
        projects = "[{'Project Name': 'A'}]"
        assert project_score(projects) == 0

    def test_adversarial_malformed_string(self):
        assert project_score("not valid python") == 0


class TestSkillScore:
    def test_golden_matched_and_missing(self):
        row = {
            "Top Matching Keywords": "['Python', 'SQL']",
            "Missing Keywords": "{'Technical Skills': ['Java'], 'Tools & Technologies': [], 'Concepts & Methodologies': []}",
        }
        # (1.0 * 2) + (-0.5 * 1) = 1.5
        assert skill_score(row, CONFIG) == 1.5

    def test_adversarial_malformed_both_fields(self):
        row = {"Top Matching Keywords": "not a list", "Missing Keywords": "also not a dict"}
        assert skill_score(row, CONFIG) == 0


class TestContributeAndSoftSkillScore:
    def test_golden_contribute(self):
        assert contribute_score("['A', 'B', 'C']") == 9

    def test_golden_soft_skill(self):
        assert soft_skill_score("['Communication', 'Leadership']") == 4

    def test_adversarial_malformed(self):
        assert contribute_score("{broken") == 0
        assert soft_skill_score("{broken") == 0


class TestIndustryPenalty:
    @pytest.mark.parametrize("industry", ["Technology", "tech startup", "IT Services", "Computer Science", "CS research"])
    def test_golden_tech_industries_no_penalty(self, industry):
        assert industry_penalty(industry) == 0

    def test_golden_non_tech_penalized(self):
        assert industry_penalty("Healthcare") == -0.25

    def test_adversarial_non_string(self):
        assert industry_penalty(None) == -0.25
        assert industry_penalty(float("nan")) == -0.25


class TestCultureAndEffortScore:
    def test_golden_culture_high(self):
        assert culture_score("High - strong alignment", CONFIG) == 3

    def test_edge_culture_unknown_level(self):
        assert culture_score("Unknown - no data", CONFIG) == 0

    def test_adversarial_culture_non_string(self):
        assert culture_score(None, CONFIG) == 0

    def test_golden_effort_low(self):
        assert effort_score("Low - minimal onboarding needed", CONFIG) == 3


class TestPenaltyScore:
    def test_golden_no_issues(self):
        row = {"Employment Gaps Detected": "False", "Red Flags & Risk Analysis": "[]", "Potential Concerns": "[]"}
        assert penalty_score(row) == 0

    def test_golden_gap_and_flags(self):
        row = {
            "Employment Gaps Detected": "True",
            "Red Flags & Risk Analysis": "['issue1', 'issue2']",
            "Potential Concerns": "['concern1']",
        }
        # -5 (gap) + -0.5*2 (flags) + -0.1*1 (concerns) = -6.1
        assert penalty_score(row) == -6.1

    def test_adversarial_malformed_lists(self):
        row = {"Employment Gaps Detected": "False", "Red Flags & Risk Analysis": "broken", "Potential Concerns": "broken"}
        assert penalty_score(row) == 0


class TestJdMatchScore:
    def test_golden(self):
        assert jd_match_score("90%") == 9.0

    def test_adversarial_malformed(self):
        assert jd_match_score("not a percent") == 0

    def test_adversarial_none(self):
        assert jd_match_score(None) == 0


class TestExperienceScore:
    def test_edge_exactly_at_lower_threshold(self):
        # exp > 3 required for the middle bucket; exactly 3 falls to the lowest bucket
        assert experience_score("3", CONFIG) == 1

    def test_edge_just_above_lower_threshold(self):
        assert experience_score("3.1", CONFIG) == 2.5

    def test_edge_exactly_at_upper_threshold(self):
        assert experience_score("7", CONFIG) == 2.5

    def test_edge_just_above_upper_threshold(self):
        assert experience_score("7.1", CONFIG) == 4

    def test_adversarial_malformed(self):
        assert experience_score("not a number", CONFIG) == 0


class TestCandidateTypeScore:
    def test_golden_case_insensitive(self):
        assert candidate_type_score("Senior", CONFIG) == 5
        assert candidate_type_score("JUNIOR", CONFIG) == 1.5

    def test_edge_unknown_type(self):
        assert candidate_type_score("Intern", CONFIG) == 0


class TestClassifyCandidate:
    def test_golden_tiers(self):
        # n=20 -> top_cutoff=5, mid_cutoff=15
        assert classify_candidate(0, 5, 15) == "Recommended for Fast-Track"
        assert classify_candidate(4, 5, 15) == "Recommended for Fast-Track"
        assert classify_candidate(5, 5, 15) == "To be Considered"
        assert classify_candidate(14, 5, 15) == "To be Considered"
        assert classify_candidate(15, 5, 15) == "Rejected"
        assert classify_candidate(19, 5, 15) == "Rejected"


class _FakeStore:
    """Minimal stand-in for LocalVectorStore.find_by_metadata, avoids
    pulling in the real vector_store module / numpy for these tests."""

    def __init__(self, chunks_by_resume):
        self._chunks_by_resume = chunks_by_resume

    def find_by_metadata(self, key, value):
        assert key == "resume_name"
        return self._chunks_by_resume.get(value, [])


class TestSemanticMatchScore:
    def test_golden_best_chunk_wins(self):
        store = _FakeStore({
            "candidate.pdf": [
                {"vector": [1.0, 0.0]},  # identical to JD vector -> similarity 1.0
                {"vector": [0.0, 1.0]},  # orthogonal -> similarity 0.0
            ]
        })
        score = semantic_match_score("candidate.pdf", [1.0, 0.0], store)
        assert score == 10.0  # best chunk cosine similarity 1.0 * 10

    def test_edge_resume_not_in_index(self):
        store = _FakeStore({})
        assert semantic_match_score("unknown.pdf", [1.0, 0.0], store) is None

    def test_edge_no_jd_vector(self):
        store = _FakeStore({"candidate.pdf": [{"vector": [1.0, 0.0]}]})
        assert semantic_match_score("candidate.pdf", None, store) is None

    def test_golden_fallback_when_not_indexed(self):
        store = _FakeStore({})
        row = {"resume_name": "unknown.pdf", "JD Match": "80%"}
        assert semantic_match_score_or_fallback(row, [1.0, 0.0], store) == 8.0

    def test_golden_fallback_when_no_jd_vector(self):
        store = _FakeStore({"candidate.pdf": [{"vector": [1.0, 0.0]}]})
        row = {"resume_name": "candidate.pdf", "JD Match": "75%"}
        assert semantic_match_score_or_fallback(row, None, store) == 7.5
