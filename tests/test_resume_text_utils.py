from resume_text_utils import extract_name_from_filename, extract_contact_details, is_placeholder_name


class TestExtractNameFromFilename:
    def test_golden_underscore_separated(self):
        assert extract_name_from_filename("John_Doe_resume.pdf") == "John Doe"

    def test_golden_hyphen_separated(self):
        assert extract_name_from_filename("jane-smith-cv.pdf") == "Jane Smith"

    def test_edge_filler_words_only(self):
        assert extract_name_from_filename("resume_final_updated.pdf") == ""

    def test_edge_single_word(self):
        assert extract_name_from_filename("Xavier.pdf") == "Xavier"

    def test_golden_mixed_case_input(self):
        assert extract_name_from_filename("JOHN_DOE_Resume.PDF") == "John Doe"


class TestExtractContactDetails:
    def test_golden_all_fields_present(self):
        text = "John Doe\njohn.doe@example.com\n9876543210\nlinkedin.com/in/johndoe"
        email, phone, linkedin = extract_contact_details(text)
        assert email == "john.doe@example.com"
        assert phone == "9876543210"
        assert linkedin == "linkedin.com/in/johndoe"

    def test_edge_empty_string(self):
        assert extract_contact_details("") == ("", "", "")

    def test_adversarial_non_string_input(self):
        assert extract_contact_details(None) == ("", "", "")
        assert extract_contact_details(12345) == ("", "", "")

    def test_golden_multiple_emails_takes_first(self):
        text = "Contact: primary@example.com or secondary@example.com"
        email, _, _ = extract_contact_details(text)
        assert email == "primary@example.com"

    def test_golden_phone_with_country_code(self):
        # Actual behavior: only spaces/hyphens are stripped, not the "+91"
        # prefix itself — documenting the real output, not an idealized one.
        text = "Call me at +91-9876543210"
        _, phone, _ = extract_contact_details(text)
        assert phone == "+919876543210"

    def test_golden_phone_without_country_code(self):
        text = "Call me at 9876543210"
        _, phone, _ = extract_contact_details(text)
        assert phone == "9876543210"

    def test_edge_no_phone_present(self):
        text = "email@example.com with no phone number"
        _, phone, _ = extract_contact_details(text)
        assert phone == ""

    def test_golden_linkedin_with_https_and_www(self):
        text = "Profile: https://www.linkedin.com/in/jane-doe-123"
        _, _, linkedin = extract_contact_details(text)
        assert "linkedin.com/in/jane-doe-123" in linkedin

    def test_adversarial_malformed_email(self):
        text = "not-an-email-at-all, missing-at-sign.com"
        email, _, _ = extract_contact_details(text)
        assert email == ""


class TestIsPlaceholderName:
    """Real bug this exists to prevent recurring: a model answering
    "Not Provided" for a resume's Name field is a real, observed output
    (confirmed via logs/llm_response_cache.json) that previously slipped
    past ResumeParser.py's fallback-trigger check entirely -- it's
    non-empty, doesn't contain "candidate", and doesn't start with "<"."""

    def test_golden_not_provided_is_a_placeholder(self):
        assert is_placeholder_name("Not Provided") is True

    def test_golden_case_insensitive(self):
        assert is_placeholder_name("NOT PROVIDED") is True
        assert is_placeholder_name("not provided") is True

    def test_golden_trailing_period_still_matches(self):
        assert is_placeholder_name("N/A.") is True

    def test_golden_every_known_placeholder_phrase(self):
        for phrase in ("Not Specified", "N/A", "NA", "Unknown", "None", "Unspecified", "Not Available", "Not Mentioned", "Not Found"):
            assert is_placeholder_name(phrase) is True, f"{phrase!r} should be recognized as a placeholder"

    def test_adversarial_real_name_is_not_a_placeholder(self):
        assert is_placeholder_name("Priya Raman") is False
        assert is_placeholder_name("Xavier") is False

    def test_edge_empty_string_is_not_a_placeholder(self):
        # Deliberately false -- an empty string is caught by ResumeParser.py's
        # existing `not gpt_name` check already, this function's job is
        # only the NON-empty placeholder phrases that check misses.
        assert is_placeholder_name("") is False

    def test_adversarial_non_string_input(self):
        assert is_placeholder_name(None) is False
        assert is_placeholder_name(123) is False

    def test_adversarial_whitespace_padding(self):
        assert is_placeholder_name("  Not Provided  ") is True
