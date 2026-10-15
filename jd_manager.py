"""Multi-JD support: CDPI's pipeline (ResumeMEtrics.py's load_jd_vector())
previously picked a single JD — whichever .txt file in jd_folder/ happened
to be most recently modified — and scored every candidate against only
that one. That's single-tenant: it can't represent "we have 3 open roles
right now, score each candidate against all of them."

This module scores every candidate against every JD currently in
jd_folder/, additively — it doesn't replace the existing "Semantic Match
Score" column (still the best-match vs. whichever JD is newest, kept for
backward compatibility with the existing weighted-score formula and its
tests). It adds a new "JD Matches" column: a ranked list of
{jd_id, jd_filename, score} across all active JDs, which is what actually
makes the Recruiter/Admin Dashboard's "which candidates are good fits for
role X" view meaningful once there's more than one open role.

jd_id is the filename stem (e.g. "ml_developer.txt" -> "ml_developer") —
the simplest stable key available without introducing a JD registry; same
convention as resume_name being used as a key elsewhere in this codebase.
"""

from pathlib import Path


def list_jds(jd_folder: Path) -> list:
    """Returns [{"jd_id", "jd_filename", "path", "text"}] for every .txt
    file in jd_folder, sorted by filename for a stable order. Empty list if
    the folder doesn't exist or has no JD files."""
    if not jd_folder.exists():
        return []
    jds = []
    for jd_path in sorted(jd_folder.glob("*.txt")):
        text = jd_path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        jds.append({
            "jd_id": jd_path.stem,
            "jd_filename": jd_path.name,
            "path": str(jd_path),
            "text": text,
        })
    return jds


def embed_jds(jds: list, embed_texts_fn) -> list:
    """Adds a "vector" key to each JD dict, via one batch call to
    embed_texts_fn (callers pass embeddings.embed_texts — injected here so
    this stays testable with a fake embedder, no real OpenAI calls)."""
    if not jds:
        return []
    vectors = embed_texts_fn([jd["text"] for jd in jds])
    return [{**jd, "vector": vector} for jd, vector in zip(jds, vectors)]


def score_candidate_against_jds(resume_name: str, jds_with_vectors: list, semantic_store, cosine_scores_fn) -> list:
    """Returns [{"jd_id", "jd_filename", "score"}] for every JD in
    jds_with_vectors, sorted by score descending (0-10 scale, same range as
    the existing jd_match_score()/semantic_match_score()). Empty list if the
    resume isn't in the semantic index yet, or no JDs are active — same
    'nothing to show' convention as semantic_match_score() returning None."""
    if not jds_with_vectors:
        return []
    chunks = semantic_store.find_by_metadata("resume_name", resume_name)
    if not chunks:
        return []
    vectors = [c["vector"] for c in chunks]

    matches = []
    for jd in jds_with_vectors:
        best = float(max(cosine_scores_fn(vectors, jd["vector"])))
        matches.append({
            "jd_id": jd["jd_id"],
            "jd_filename": jd["jd_filename"],
            "score": round(max(0.0, best) * 10, 2),
        })
    return sorted(matches, key=lambda m: -m["score"])
