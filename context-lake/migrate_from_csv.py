"""Backfill existing CSV/JSON candidate data into the context lake (Cosmos DB).

Reads, without modifying or deleting any source file:
  - Resume_Parsed_CSVs/resume_summary.csv          (ResumeParser.py output)
  - InviteSender/Invitations_Archive/resume_summary_*.csv  (older snapshots —
    resume_summary.csv gets overwritten on every parser run, so candidates
    processed in the past can be missing from the live file entirely; these
    archives are the only remaining record of them. Confirmed in this repo:
    e.g. Nakul Raval has full interview history but isn't in the live CSV.)
  - CandidateMetrics_Folder/candidate_ranking_metrics.csv  (ResumeMEtrics.py output)
  - InterviewData/<Name>/interview_questions.json  (generate_questions.py output)
  - InterviewData/<Name>/scoring/score_summary.json (interview_bot.py output)
  - voicebot/InterviewData/<Name>/{answers,score}.json (voicebot/voice_bot.py output)

Usage:
    python migrate_from_csv.py --dry-run          # writes migration_preview.json, no Azure needed
    python migrate_from_csv.py                    # actually upserts into Cosmos DB (needs .env configured)

IMPORTANT — candidate_id linking: resume_summary.csv and candidate_ranking_metrics.csv
share a `resume_name` column, so joining those two is exact. Interview folders
(InterviewData/<Name>/, voicebot/InterviewData/<Name>/) are only identified by a
folder name, not a resume filename, so this script GUESSES the matching
resume_name as "<FolderName>_resume.pdf". That guess is wrong for any candidate
whose OutputResume_Folder filename doesn't follow that exact pattern (seen in
this repo: a few files have a double underscore, e.g. "Buddha_Vamsi__resume.pdf").
Always inspect the --dry-run preview's "unmatched_transcripts" list before
running for real.
"""

import sys
import os
import ast
import json
import uuid
import argparse
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from candidate_id import candidate_id_for  # shared with ResumeParser.py's dual-write


def safe_literal(value, default):
    """resume_summary.csv/candidate_ranking_metrics.csv store Python repr strings
    (e.g. "['Python', 'SQL']"), not JSON, in several columns — parse with
    ast.literal_eval (safe: no code execution), falling back to the raw value."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value


def clean(value):
    return None if (value is None or (isinstance(value, float) and pd.isna(value))) else value


def _row_to_candidate_doc(row, source_label: str) -> dict:
    resume_name = row["resume_name"]
    cid = candidate_id_for(resume_name)
    return {
        "id": cid,
        "candidate_id": cid,
        "resume_name": resume_name,
        "source_blob_path": f"raw-resumes/{resume_name}",
        "_source_csv": source_label,
        "Name": clean(row.get("Name")),
        "Email": clean(row.get("Email")),
        "Phone": clean(row.get("Phone")),
        "LinkedIn": clean(row.get("LinkedIn")),
        "JD Match": clean(row.get("JD Match")),
        "Missing Keywords": safe_literal(row.get("Missing Keywords"), {}),
        "Top Matching Keywords": safe_literal(row.get("Top Matching Keywords"), []),
        "Profile Summary": clean(row.get("Profile Summary")),
        "Projects": safe_literal(row.get("Projects"), []),
        "Certifications & Courses": safe_literal(row.get("Certifications & Courses"), []),
        "Resume Strength Score": clean(row.get("Resume Strength Score")),
        "Candidate Type": clean(row.get("Candidate Type")),
        "Employment Gaps Detected": bool(row.get("Employment Gaps Detected"))
        if pd.notna(row.get("Employment Gaps Detected"))
        else None,
    }


def build_candidates(parsed_csv: Path, ranked_csv: Path, archive_dir: Path) -> list:
    candidates_by_name = {}

    if parsed_csv.exists():
        parsed_df = pd.read_csv(parsed_csv)
        for _, row in parsed_df.iterrows():
            candidates_by_name[row["resume_name"]] = _row_to_candidate_doc(row, str(parsed_csv))
    else:
        print(f"[skip] {parsed_csv} not found.")

    # resume_summary.csv is overwritten on every ResumeParser.py run, so past
    # candidates can be missing from the live file entirely — recover them
    # from archived snapshots (oldest first, so the newest archive wins if a
    # name appears in more than one, closest to what the live data would be).
    archive_files = sorted(archive_dir.glob("resume_summary_*.csv")) if archive_dir.exists() else []
    recovered = 0
    for archive_csv in archive_files:
        try:
            archive_df = pd.read_csv(archive_csv)
        except Exception as e:
            print(f"[skip] could not read {archive_csv}: {e}")
            continue
        if "resume_name" not in archive_df.columns:
            continue
        for _, row in archive_df.iterrows():
            resume_name = row.get("resume_name")
            if pd.isna(resume_name) or resume_name in candidates_by_name:
                continue
            candidates_by_name[resume_name] = _row_to_candidate_doc(row, str(archive_csv))
            recovered += 1
    if recovered:
        print(f"Recovered {recovered} candidate(s) missing from the live CSV out of {len(archive_files)} archive file(s).")

    ranked_by_name = {}
    if ranked_csv.exists():
        ranked_df = pd.read_csv(ranked_csv)
        for _, row in ranked_df.iterrows():
            ranked_by_name[row["resume_name"]] = row
    else:
        print(f"[skip] {ranked_csv} not found — candidates will have no ranking_tier.")

    candidates = []
    for resume_name, doc in candidates_by_name.items():
        ranked_row = ranked_by_name.get(resume_name)
        if ranked_row is not None:
            doc["ranking_tier"] = clean(ranked_row.get("Selection Recommendation"))
            doc["normalized_weighted_score"] = clean(ranked_row.get("Normalized Weighted Score"))
        candidates.append(doc)
    return candidates


def build_interview_transcripts(interview_data_dir: Path, voicebot_data_dir: Path) -> list:
    transcripts = []

    if interview_data_dir.exists():
        for candidate_dir in sorted(interview_data_dir.iterdir()):
            if not candidate_dir.is_dir():
                continue
            resume_name_guess = f"{candidate_dir.name}_resume.pdf"
            cid = candidate_id_for(resume_name_guess)

            qfile = candidate_dir / "interview_questions.json"
            if qfile.exists():
                try:
                    data = json.loads(qfile.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    data = {}
                transcripts.append({
                    "id": str(uuid.uuid4()),
                    "transcript_id": str(uuid.uuid4()),
                    "candidate_id": cid,
                    "interview_type": "pre_generated_questions",
                    "questions": data.get("questions", []),
                    "_resume_name_guess": resume_name_guess,
                    "_source": str(qfile),
                })

            scoring_file = candidate_dir / "scoring" / "score_summary.json"
            if scoring_file.exists():
                raw_text = scoring_file.read_text(encoding="utf-8")
                try:
                    qa_items = json.loads(raw_text)
                except json.JSONDecodeError:
                    qa_items = []
                scores = [
                    it.get("score") for it in qa_items
                    if isinstance(it, dict) and isinstance(it.get("score"), (int, float))
                ]
                transcripts.append({
                    "id": str(uuid.uuid4()),
                    "transcript_id": str(uuid.uuid4()),
                    "candidate_id": cid,
                    "interview_type": "cli_scored_qna",
                    "qa_pairs": [
                        {"question": it.get("question"), "answer": it.get("answer")}
                        for it in qa_items if isinstance(it, dict)
                    ],
                    "overall_score": (sum(scores) / len(scores)) if scores else None,
                    "score_extraction_raw": raw_text,
                    "_resume_name_guess": resume_name_guess,
                    "_source": str(scoring_file),
                })

    if voicebot_data_dir.exists():
        for candidate_dir in sorted(voicebot_data_dir.iterdir()):
            if not candidate_dir.is_dir():
                continue
            answers_file = candidate_dir / "answers.json"
            if not answers_file.exists():
                continue
            resume_name_guess = f"{candidate_dir.name}_resume.pdf"
            cid = candidate_id_for(resume_name_guess)

            try:
                qa_pairs = json.loads(answers_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                qa_pairs = []

            overall = None
            score_file = candidate_dir / "score.json"
            if score_file.exists():
                try:
                    overall = json.loads(score_file.read_text(encoding="utf-8")).get("score")
                except json.JSONDecodeError:
                    pass

            transcripts.append({
                "id": str(uuid.uuid4()),
                "transcript_id": str(uuid.uuid4()),
                "candidate_id": cid,
                "interview_type": "voicebot_warmup",
                "qa_pairs": qa_pairs,
                "overall_score": overall,
                "_resume_name_guess": resume_name_guess,
                "_source": str(answers_file),
            })

    return transcripts


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Build and write a preview JSON; no Azure connection attempted.")
    parser.add_argument("--out", default="migration_preview.json", help="Dry-run preview output path.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    candidates = build_candidates(
        repo_root / "Resume_Parsed_CSVs" / "resume_summary.csv",
        repo_root / "CandidateMetrics_Folder" / "candidate_ranking_metrics.csv",
        repo_root / "InviteSender" / "Invitations_Archive",
    )
    transcripts = build_interview_transcripts(
        repo_root / "InterviewData",
        repo_root / "voicebot" / "InterviewData",
    )

    known_ids = {c["candidate_id"] for c in candidates}
    unmatched = [t for t in transcripts if t["candidate_id"] not in known_ids]

    print(f"Built {len(candidates)} candidate documents and {len(transcripts)} interview transcript documents.")
    if unmatched:
        print(f"WARNING: {len(unmatched)} transcript(s) have no matching candidate "
              f"(resume_name guess didn't match resume_summary.csv) — check 'unmatched_transcripts' in the preview.")

    if args.dry_run:
        preview = {
            "candidates": candidates,
            "interview_transcripts": transcripts,
            "unmatched_transcripts": [t["_source"] for t in unmatched],
        }
        Path(args.out).write_text(json.dumps(preview, indent=2, default=str), encoding="utf-8")
        print(f"Dry run only — wrote preview to {args.out}. No Azure connection attempted, nothing was sent anywhere.")
        return

    from repositories import upsert_candidate, upsert_interview_transcript

    for doc in candidates:
        doc.pop("_source_csv", None)
        upsert_candidate(doc)
    for doc in transcripts:
        doc.pop("_resume_name_guess", None)
        doc.pop("_source", None)
        upsert_interview_transcript(doc)
    print(f"Migrated {len(candidates)} candidates and {len(transcripts)} interview transcripts to Cosmos DB.")


if __name__ == "__main__":
    main()
