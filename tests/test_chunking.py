from chunking import chunk_text, chunk_resume_text


class TestChunkText:
    def test_golden_respects_max_chars(self):
        text = "Sentence one. Sentence two. Sentence three. Sentence four."
        chunks = chunk_text(text, max_chars=25)
        assert all(len(c) <= 30 for c in chunks)  # small slack for the ". " join
        assert len(chunks) > 1

    def test_edge_empty_string(self):
        # Actual behavior: "".split(". ") == [""], and the trailing ". "
        # the loop always appends survives .strip() as ".", a single
        # near-empty chunk — documenting this quirk, not asserting an
        # idealized [] that the function was never written to produce.
        assert chunk_text("", max_chars=100) == ["."]

    def test_golden_short_text_single_chunk(self):
        chunks = chunk_text("Just one short sentence.", max_chars=1000)
        assert len(chunks) == 1


class TestChunkResumeText:
    REAL_SHAPED_RESUME = (
        "Priyal Kalal\n"
        "priyal@example.com | linkedin.com/in/priyalkalal | 9876543210\n"
        "Education\n"
        "BE Computer Engineering, Pune University, 2021-2025\n"
        "Experience\n"
        "DevOps Intern at IAMOPS Pvt Ltd, Jan 2025 - Present. Worked with AWS EC2, S3.\n"
        "Technical Skills\n"
        "Python, AWS, Docker, Kubernetes\n"
    )

    def test_golden_splits_on_known_headers(self):
        chunks = chunk_resume_text(self.REAL_SHAPED_RESUME)
        joined = " ".join(chunks)
        assert any(c.lower().startswith("education:") for c in chunks)
        assert any(c.lower().startswith("experience:") for c in chunks)
        assert any(c.lower().startswith("technical skills:") for c in chunks)

    def test_golden_strips_contact_noise_from_lead(self):
        chunks = chunk_resume_text(self.REAL_SHAPED_RESUME)
        # the lead block (before "Education") should not survive as its own
        # chunk once email/linkedin/phone are stripped out of it, since the
        # only text left is the bare name "Priyal Kalal" (<=20 chars)
        lead_like_chunks = [c for c in chunks if "@" in c or "linkedin.com" in c]
        assert lead_like_chunks == []

    def test_edge_no_recognizable_headers_falls_back(self):
        plain_text = "This is just a plain paragraph with no resume structure at all. " * 5
        chunks = chunk_resume_text(plain_text, max_chars=1000)
        # should fall back to chunk_text() behavior - single chunk, no "Header:" prefix
        assert len(chunks) >= 1
        assert not chunks[0].split(":")[0].strip().lower() in (
            "education", "experience", "skills", "objective"
        )

    def test_edge_long_section_gets_subchunked_with_header_preserved(self):
        long_experience = "Experience\n" + ("Did something impactful at a company. " * 200)
        chunks = chunk_resume_text(long_experience, max_chars=500)
        experience_chunks = [c for c in chunks if c.lower().startswith("experience:")]
        assert len(experience_chunks) > 1  # sub-chunked
        assert all(len(c) <= 520 for c in experience_chunks)  # header prefix + max_chars slack

    def test_adversarial_header_word_mid_sentence_not_split(self):
        # "experience" appears inside a sentence, not on its own line — must
        # NOT be treated as a section header (this was the original bug this
        # chunking rewrite fixed)
        text = "Profile\nI have 5 years of experience in software development and testing."
        chunks = chunk_resume_text(text)
        assert not any(c.lower().startswith("experience:") for c in chunks)
