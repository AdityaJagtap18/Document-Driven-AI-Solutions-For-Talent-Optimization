from resume_text_utils import extract_name_from_filename, extract_contact_details


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
