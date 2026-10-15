"""Same char-based chunking approach as ResumeParser.py's chunk_text()
(max 3000 chars, split on ". "), duplicated intentionally — this library is
standalone for now, not importing from the root scripts. Keep in sync if
ResumeParser.py's chunking logic changes."""


def chunk_text(text: str, max_chars: int = 3000) -> list:
    sentences = text.split(". ")
    chunks = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) < max_chars:
            current += sentence + ". "
        else:
            if current.strip():
                chunks.append(current.strip())
            current = sentence + ". "
    if current.strip():
        chunks.append(current.strip())
    return chunks
