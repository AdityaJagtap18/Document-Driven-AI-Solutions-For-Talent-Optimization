"""Query the resume-chunks index for the chunks most semantically similar to
a job description — the RAG retrieval step that resume_merge_structured_extraction
(see ../prompts/) should eventually be grounded on instead of the whole resume.

Usage:
    python search.py                          # uses jd_folder's most recent JD, prints top 5 per resume
    python search.py --jd-file path/to/jd.txt --top-k 10
"""

import sys
import os
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from embeddings import embed_text
from vector_store import LocalVectorStore

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR = Path(__file__).resolve().parent / "index"


def search_resumes_for_jd(jd_text: str, top_k: int = 5, index_path: Path = None) -> list:
    """Returns up to top_k {id, score, metadata} resume chunks ranked by
    cosine similarity to the JD. Raises FileNotFoundError if the index
    doesn't exist yet — run build_index.py first."""
    index_path = index_path or (INDEX_DIR / "resume-chunks.json")
    if not index_path.exists():
        raise FileNotFoundError(f"No index at {index_path} — run build_index.py first.")
    store = LocalVectorStore(str(index_path))
    query_vector = embed_text(jd_text)
    return store.search(query_vector, top_k=top_k)


def best_matching_resumes(jd_text: str, top_chunks: int = 20, index_path: Path = None) -> list:
    """Searches a wide pool of chunks, then collapses to one best-chunk-score
    per resume_name — a simple proxy for 'which candidates to look at first',
    pending a real re-ranking step."""
    chunks = search_resumes_for_jd(jd_text, top_k=top_chunks, index_path=index_path)
    best_per_resume = {}
    for c in chunks:
        resume_name = c["metadata"].get("resume_name")
        if resume_name and (resume_name not in best_per_resume or c["score"] > best_per_resume[resume_name]["score"]):
            best_per_resume[resume_name] = c
    return sorted(best_per_resume.values(), key=lambda c: -c["score"])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jd-file", default=None, help="Path to a JD text file; defaults to the most recently modified file in jd_folder/")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    if args.jd_file:
        jd_path = Path(args.jd_file)
    else:
        jd_files = list((REPO_ROOT / "jd_folder").glob("*.txt"))
        if not jd_files:
            print("No JD file found in jd_folder/ and none given via --jd-file.")
            return
        jd_path = max(jd_files, key=lambda f: f.stat().st_mtime)

    jd_text = jd_path.read_text(encoding="utf-8")
    print(f"Query JD: {jd_path.name}\n")

    out_encoding = sys.stdout.encoding or "utf-8"

    def safe(s):
        return s.encode(out_encoding, errors="replace").decode(out_encoding)

    print(f"--- Top {args.top_k} resume CHUNKS by similarity ---")
    for r in search_resumes_for_jd(jd_text, top_k=args.top_k):
        preview = safe(r["metadata"].get("chunk_text", "")[:150].replace("\n", " "))
        print(f"  {r['score']:.4f}  {r['metadata'].get('resume_name')} (chunk {r['metadata'].get('chunk_index')})")
        print(f"           \"{preview}...\"")

    print(f"\n--- Best-matching RESUMES (collapsed from top chunks) ---")
    for r in best_matching_resumes(jd_text, top_chunks=args.top_k * 5)[: args.top_k]:
        print(f"  {r['score']:.4f}  {r['metadata'].get('resume_name')}")


if __name__ == "__main__":
    main()
