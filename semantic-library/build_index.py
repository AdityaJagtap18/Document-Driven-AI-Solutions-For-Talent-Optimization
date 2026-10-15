"""Builds the local resume-chunks and jd-corpus vector indexes from the
repo's real resume PDFs (OutputResume_Folder/*.pdf) and job descriptions
(jd_folder/*.txt). Calls the real OpenAI embeddings API — small real cost
(text-embedding-3-small, ~$0.02 per 1M tokens).

Usage:
    python build_index.py                 # build/refresh both indexes
    python build_index.py --resumes-only
    python build_index.py --jds-only

Indexes are written to index/resume-chunks.json and index/jd-corpus.json
(gitignored — rebuild instead of committing embeddings).
"""

import sys
import os
import argparse
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyPDF2 import PdfReader

from chunking import chunk_resume_text
from embeddings import embed_texts
from vector_store import LocalVectorStore

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR = Path(__file__).resolve().parent / "index"


def extract_text_from_pdf(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    text = ""
    for page in reader.pages:
        extracted = page.extract_text()
        if extracted:
            text += extracted
    return text.strip()


def build_resume_index(resume_dir: Path, out_path: Path):
    pdf_paths = sorted(resume_dir.glob("*.pdf"))
    if not pdf_paths:
        print(f"[skip] no PDFs found in {resume_dir}")
        return

    store = LocalVectorStore(str(out_path))
    skipped = []
    all_chunk_texts = []
    all_chunk_ids = []
    all_chunk_meta = []

    for pdf_path in pdf_paths:
        text = extract_text_from_pdf(pdf_path)
        if not text:
            skipped.append(pdf_path.name)
            continue
        chunks = chunk_resume_text(text)
        for i, chunk in enumerate(chunks):
            all_chunk_ids.append(f"{pdf_path.name}::chunk{i}")
            all_chunk_texts.append(chunk)
            all_chunk_meta.append({
                "resume_name": pdf_path.name,
                "chunk_index": i,
                "chunk_text": chunk[:500],  # preview only; full text not duplicated into metadata
            })

    if not all_chunk_texts:
        print("[skip] no extractable text in any resume PDF.")
        return

    print(f"Embedding {len(all_chunk_texts)} resume chunks from {len(pdf_paths) - len(skipped)} PDFs "
          f"({len(skipped)} skipped — no extractable text)...")
    vectors = embed_texts(all_chunk_texts)

    for doc_id, vector, meta in zip(all_chunk_ids, vectors, all_chunk_meta):
        store.add(doc_id, vector, meta)
    store.save()

    print(f"Saved resume-chunks index: {len(store)} chunks -> {out_path}")
    if skipped:
        print(f"Skipped (no extractable text): {skipped}")


def build_jd_index(jd_dir: Path, out_path: Path):
    jd_paths = sorted(jd_dir.glob("*.txt"))
    if not jd_paths:
        print(f"[skip] no JD .txt files found in {jd_dir}")
        return

    store = LocalVectorStore(str(out_path))
    texts = []
    ids = []
    metas = []
    for jd_path in jd_paths:
        text = jd_path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        texts.append(text)
        ids.append(jd_path.name)
        metas.append({"jd_filename": jd_path.name, "jd_text": text})

    if not texts:
        print("[skip] all JD files were empty.")
        return

    print(f"Embedding {len(texts)} job description(s)...")
    vectors = embed_texts(texts)
    for doc_id, vector, meta in zip(ids, vectors, metas):
        store.add(doc_id, vector, meta)
    store.save()
    print(f"Saved jd-corpus index: {len(store)} JD(s) -> {out_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--resumes-only", action="store_true")
    parser.add_argument("--jds-only", action="store_true")
    args = parser.parse_args()

    INDEX_DIR.mkdir(exist_ok=True)

    if not args.jds_only:
        build_resume_index(REPO_ROOT / "OutputResume_Folder", INDEX_DIR / "resume-chunks.json")
    if not args.resumes_only:
        build_jd_index(REPO_ROOT / "jd_folder", INDEX_DIR / "jd-corpus.json")


if __name__ == "__main__":
    main()
