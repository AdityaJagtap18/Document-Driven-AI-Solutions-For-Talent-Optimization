"""Real-time, per-candidate tiering for the live blob-triggered pipeline.

ResumeMEtrics.py's existing tiering (add_selection_recommendation()) assigns
tiers by RELATIVE RANK across the whole candidate pool (top 25% of however
many candidates exist today = Fast-Track, etc.) -- a batch operation that
can't classify one new candidate in isolation without re-ranking everyone
else's already-persisted tier too.

This module is a deliberately simpler, ABSOLUTE-THRESHOLD policy for the
real-time path only: it looks at the two already-0-10-scaled signals the AI
profile already carries (JD Match Score, via resume_scoring.jd_match_score()
on the LLM's own self-reported "JD Match" percentage, and Resume Strength
Score, the LLM's own self-reported 0-10 rating) and classifies a single
candidate against fixed cutoffs. It does not use the full weighted
multi-factor formula in ResumeMEtrics.py (that formula's per-column
normalization is itself a batch min/max over the current pool, which is
undefined for a single row) and does not touch any other candidate's
persisted tier.

Both paths remain real -- this is not a mock score. A full batch re-run of
ResumeMEtrics.py against the whole Cosmos candidate pool (not just the CSV)
would still be the correct way to get the original relative-rank tiering
for everyone; that is intentionally out of scope here (see
docs/E2E_CANDIDATE_LIFECYCLE_REPORT.md)."""

from resume_scoring import jd_match_score

FAST_TRACK = "Recommended for Fast-Track"
TO_BE_CONSIDERED = "To be Considered"
REJECTED = "Rejected"

FAST_TRACK_CUTOFF = 7.0
CONSIDER_CUTOFF = 4.5


def compute_realtime_tier(candidate: dict) -> dict:
    """Returns {"ranking_tier", "realtime_score", "realtime_score_detail"}.
    Never raises -- a missing/malformed field just contributes 0 to that
    half of the score rather than failing the whole candidate out of the
    pipeline."""
    jd_match_raw = candidate.get("JD Match", "0%")
    try:
        jd_score = jd_match_score(jd_match_raw)
    except Exception:
        jd_score = 0.0

    try:
        strength_score = float(str(candidate.get("Resume Strength Score", 0)).strip())
    except (TypeError, ValueError):
        strength_score = 0.0

    combined = round(0.5 * jd_score + 0.5 * strength_score, 2)

    if combined >= FAST_TRACK_CUTOFF:
        tier = FAST_TRACK
    elif combined >= CONSIDER_CUTOFF:
        tier = TO_BE_CONSIDERED
    else:
        tier = REJECTED

    return {
        "ranking_tier": tier,
        "realtime_score": combined,
        "realtime_score_detail": {
            "jd_match_score": jd_score,
            "resume_strength_score": strength_score,
            "policy": "absolute-threshold (real-time); see realtime_tiering.py module docstring",
        },
    }
