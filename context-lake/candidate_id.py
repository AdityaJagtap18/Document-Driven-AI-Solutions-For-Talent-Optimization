"""Deterministic candidate_id generation from resume_name, shared by the
live pipeline (ResumeParser.py, via a dual-write into the context lake) and
the one-time backfill script (migrate_from_csv.py), so the same resume
always maps to the same Cosmos DB document id regardless of which path
wrote it first — upserts from either path land on the same document
instead of creating duplicates."""

import uuid

NAMESPACE = uuid.UUID("c6a7a2d0-6e9b-4b7a-9c7a-2f6b1d9a4b10")


def candidate_id_for(resume_name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, resume_name))
