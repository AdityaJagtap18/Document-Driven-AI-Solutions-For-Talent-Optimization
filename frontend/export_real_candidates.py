"""Generates frontend/src/data/candidates.json from the real pipeline
output (CandidateMetrics_Folder/candidate_ranking_metrics.csv), so the
frontend's mock-mode Dashboard/Review Queue/Candidate Portal show actual
processed candidates instead of 4 made-up ones -- while still running
entirely on localhost, no Azure deployment needed to demo.

Includes the full recruiter-facing profile (skills, projects,
certifications, culture fit, red flags, HR notes, etc.), not just the
table-summary fields -- Dashboard.jsx's click-to-expand detail view reads
all of it. Fields that the CSV stores as Python-repr'd list/dict strings
(e.g. "['AWS Cert', 'Azure Cert']", not JSON) are parsed with
ast.literal_eval so the frontend gets real arrays/objects instead of
single-quoted string blobs; a value that fails to parse is kept as the
raw string so nothing silently disappears.

candidate_id is generated the same way the real pipeline does
(context-lake/candidate_id.py's deterministic uuid5 from resume_name), so
these ids will match whatever's actually in Cosmos DB once that's
provisioned and context-lake/migrate_from_csv.py has been run -- this
export and that backfill are independent, but consistent.

Re-run this after any ResumeParser.py/ResumeMEtrics.py run that adds or
rescores candidates, to refresh the frontend's snapshot:

    python frontend/export_real_candidates.py
"""

import ast
import json
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "context-lake"))
from candidate_id import candidate_id_for  # noqa: E402

SOURCE_CSV = REPO_ROOT / "CandidateMetrics_Folder" / "candidate_ranking_metrics.csv"
OUT_PATH = Path(__file__).resolve().parent / "src" / "data" / "candidates.json"


def _clean(value):
    """NaN/None -> None, numpy scalars -> plain python, everything else as-is."""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if hasattr(value, "item"):  # numpy scalar (int64/float64/bool_)
        return value.item()
    return value


def _parse_literal(value):
    """For CSV cells holding a Python repr of a list/dict (not JSON) -- e.g.
    ResumeParser.py's Projects/Certifications/Red Flags columns. Falls back
    to the raw (cleaned) value if it's not a parseable literal, so a
    malformed cell degrades to a plain string instead of vanishing."""
    cleaned = _clean(value)
    if not isinstance(cleaned, str):
        return cleaned
    try:
        return ast.literal_eval(cleaned)
    except (ValueError, SyntaxError):
        return cleaned


def main():
    if not SOURCE_CSV.exists():
        print(f"[skip] {SOURCE_CSV} doesn't exist yet -- run ResumeParser.py + ResumeMEtrics.py first.")
        return

    df = pd.read_csv(SOURCE_CSV)
    df = df[df["resume_name"].notna()]

    growth_potential_col = next((c for c in df.columns if "Growth Potential" in c), None)

    candidates = []
    for _, row in df.iterrows():
        resume_name = row["resume_name"]
        cid = candidate_id_for(resume_name)
        phone = row.get("Phone")
        candidates.append({
            "id": cid,
            "candidate_id": cid,
            "resume_name": resume_name,
            "Name": _clean(row.get("Name")),
            "Email": _clean(row.get("Email")),
            "Phone": None if pd.isna(phone) else str(phone).rstrip("0").rstrip(".") if isinstance(phone, float) else str(phone),
            "LinkedIn": _clean(row.get("LinkedIn")),
            "JD Match": _clean(row.get("JD Match")),
            "Candidate Type": _clean(row.get("Candidate Type")),
            "Resume Strength Score": _clean(row.get("Resume Strength Score")),
            "Relevant Experience (yrs)": _clean(row.get("Relevant Experience (yrs)")),
            "Resume Format Quality": _clean(row.get("Resume Format Quality")),
            "Profile Summary": _clean(row.get("Profile Summary")),
            "Top Matching Keywords": _parse_literal(row.get("Top Matching Keywords")),
            "Missing Keywords": _parse_literal(row.get("Missing Keywords")),
            "Projects": _parse_literal(row.get("Projects")),
            "Certifications & Courses": _parse_literal(row.get("Certifications & Courses")),
            "Skills That Will Contribute to the Company": _parse_literal(row.get("Skills That Will Contribute to the Company")),
            "Soft Skills & Leadership Qualities": _parse_literal(row.get("Soft Skills & Leadership Qualities")),
            "Industry Experience": _clean(row.get("Industry Experience")),
            "Culture Fit Assessment": _clean(row.get("Culture Fit Assessment")),
            "Candidate's Growth Potential": _clean(row.get(growth_potential_col)) if growth_potential_col else None,
            "Effort Needed by the Company": _clean(row.get("Effort Needed by the Company")),
            "Potential Concerns": _parse_literal(row.get("Potential Concerns")),
            "Red Flags & Risk Analysis": _parse_literal(row.get("Red Flags & Risk Analysis")),
            "HR Notes": _clean(row.get("HR Notes")),
            "ranking_tier": _clean(row.get("Selection Recommendation")),
            "normalized_weighted_score": _clean(row.get("Normalized Weighted Score")),
            "ingested_at": None,  # not tracked in the CSV pipeline -- only populated once writing through context-lake
            "Employment Gaps Detected": bool(row.get("Employment Gaps Detected")),
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(candidates, f, indent=2)

    print(f"Wrote {len(candidates)} real candidate(s) to {OUT_PATH}")


if __name__ == "__main__":
    main()
