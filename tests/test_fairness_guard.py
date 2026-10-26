"""F-16 (Phase 1 Implementation Contract, Task 7): minimal, honestly
scoped fairness guard.

This test file IS the deliverable (per the contract) -- it answers one
narrow question against the REAL repository: could a protected-
attribute-shaped field reach resume_scoring.py's scoring functions, or
does one already exist in the real candidate document shape (the actual
LLM extraction schema, the formally declared Cosmos schema, and the real
field accesses in resume_scoring.py's source)? It is a static structural
guard, not a statistical fairness measurement, and it does not act on any
of the 5 proxy-bias findings already on file in
docs/fde-process/05-bias-fairness-audit.md -- those remain pending the
user's own decision. See fairness_guard.py's module docstring for the
full scope statement.
"""

import json
from pathlib import Path

import fairness_guard
import rag_config

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RESUME_SCORING_SOURCE = (_REPO_ROOT / "resume_scoring.py").read_text(encoding="utf-8")
_CANDIDATE_SCHEMA = json.loads((_REPO_ROOT / "context-lake" / "schema" / "candidate.schema.json").read_text(encoding="utf-8"))


class TestProtectedAttributePatternSensitivity:
    """The guard's own sensitivity, proven BEFORE trusting it against the
    real repo below -- a deliberately-failing fixture alongside the
    passing ones, per the contract's explicit acceptance criterion."""

    def test_adversarial_a_real_protected_attribute_field_is_caught(self):
        # This is the guard "failing loudly" on purpose, against a
        # fixture that should never appear in the real schema/source --
        # proof the guard isn't vacuously true.
        hits = fairness_guard.find_protected_attribute_fields(
            ["Name", "Gender", "Resume Strength Score", "Date of Birth", "Candidate Type"]
        )
        assert set(hits) == {"Gender", "Date of Birth"}

    def test_golden_ordinary_field_names_pass_clean(self):
        assert fairness_guard.find_protected_attribute_fields(
            ["Name", "Email", "Phone", "LinkedIn", "JD Match", "Profile Summary", "Candidate Type"]
        ) == []

    def test_adversarial_catches_every_documented_protected_category(self):
        # Each of these is a realistic field-name spelling for a real
        # protected category -- if any of these stopped being caught,
        # the guard's own coverage would have silently regressed.
        candidates = [
            "Race", "Ethnicity", "National Origin", "Nationality", "Citizenship Status",
            "Gender", "Sex", "Sexual Orientation", "Age", "Date of Birth", "DOB",
            "Religion", "Religious Belief", "Disability Status", "Marital Status",
            "Pregnancy Status", "Caste", "Skin Colour", "Veteran Status",
        ]
        hits = fairness_guard.find_protected_attribute_fields(candidates)
        assert set(hits) == set(candidates)

    def test_golden_substring_coincidence_does_not_false_positive(self):
        # "Essex" contains "sex" as a bare substring but is not the word
        # "sex" -- the pattern is word-boundary matched specifically to
        # avoid this kind of false positive on ordinary text.
        assert fairness_guard.find_protected_attribute_fields(["Essex University", "Ageless Software Inc"]) == []


class TestExtractJsonLikeKeys:
    def test_golden_extracts_every_top_level_and_nested_key(self):
        template = '{"Name": "...", "Projects": [{"Project Name": "...", "Relevance to JD": "..."}]}'
        keys = fairness_guard.extract_json_like_keys(template)
        assert "Name" in keys
        assert "Project Name" in keys
        assert "Relevance to JD" in keys


class TestExtractRowSubscriptFields:
    def test_golden_extracts_bracket_and_get_style_access(self):
        source = 'x = row["Employment Gaps Detected"]\ny = row.get("JD Match")\nz = row[\'Top Matching Keywords\']'
        fields = fairness_guard.extract_row_subscript_fields(source)
        assert set(fields) == {"Employment Gaps Detected", "JD Match", "Top Matching Keywords"}

    def test_edge_no_row_access_returns_empty(self):
        assert fairness_guard.extract_row_subscript_fields("def f(x): return x + 1") == []


# ---------------------------------------------------------------------------
# The real guard, against the real repository.
# ---------------------------------------------------------------------------
class TestResumeScoringReadsNoProtectedAttributeField:
    def test_golden_no_row_subscript_field_is_protected_attribute_shaped(self):
        fields = fairness_guard.extract_row_subscript_fields(_RESUME_SCORING_SOURCE)
        # Fails loudly (not silently skipped) if resume_scoring.py starts
        # reading NO row fields at all -- this guard verifies an absence,
        # and an absence of fields to check is not the same as a verified
        # absence of protected ones.
        assert fields, "resume_scoring.py's row-field accesses changed shape -- update this guard's extraction, don't just let it pass trivially."
        hits = fairness_guard.find_protected_attribute_fields(fields)
        assert hits == [], f"resume_scoring.py reads protected-attribute-shaped field(s): {hits}"


class TestCandidateDocumentShapeHasNoProtectedAttributeField:
    def test_golden_declared_cosmos_schema_has_no_protected_field(self):
        properties = list(_CANDIDATE_SCHEMA.get("properties", {}).keys())
        assert properties, "candidate.schema.json's properties changed shape -- update this guard, don't just let it pass trivially."
        hits = fairness_guard.find_protected_attribute_fields(properties)
        assert hits == [], f"candidate.schema.json declares a protected-attribute-shaped field: {hits}"

    def test_golden_real_llm_extraction_schema_has_no_protected_field(self):
        """The candidate.schema.json file is known to be incomplete (it's
        missing real fields like HR Notes/Red Flags & Risk Analysis/
        Potential Concerns -- confirmed present in real candidate
        documents and in this exact template). This checks the
        AUTHORITATIVE real schema instead: rag_config.
        DEFAULT_MERGE_EXTRACTION_TEMPLATE, verified byte-for-byte against
        the real prompt file per that constant's own comment -- this is
        the actual shape every real candidate document gets its
        structured fields from."""
        keys = fairness_guard.extract_json_like_keys(rag_config.DEFAULT_MERGE_EXTRACTION_TEMPLATE)
        assert keys, "DEFAULT_MERGE_EXTRACTION_TEMPLATE's shape changed -- update this guard, don't just let it pass trivially."
        hits = fairness_guard.find_protected_attribute_fields(keys)
        assert hits == [], f"The real candidate extraction schema includes a protected-attribute-shaped field: {hits}"
