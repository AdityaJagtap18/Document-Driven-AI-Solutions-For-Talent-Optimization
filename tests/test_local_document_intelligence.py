"""Tests for local_document_intelligence.py (the free/local fallback for
document_intelligence_client.py) and document_intelligence_client.py's
provider selection between Azure and local extraction."""

import io

import local_document_intelligence as ldi


def _real_pdf_bytes(lines: list) -> bytes:
    """Builds a real, minimal text-layer PDF via reportlab (already a
    project dependency) so tests exercise the actual PyPDF2 extraction
    path, not a mocked one."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 700
    for line in lines:
        c.drawString(100, y, line)
        y -= 20
    c.save()
    return buf.getvalue()


class TestParseFieldsFromText:
    def test_golden_colon_separated_fields_get_higher_confidence(self):
        fields = ldi._parse_fields_from_text("Name: Priya Sharma\nDegree: B.Tech Computer Science")
        assert fields["Name"] == {"value": "Priya Sharma", "confidence": 0.85}
        assert fields["Degree"] == {"value": "B.Tech Computer Science", "confidence": 0.85}

    def test_golden_dash_separated_fields_get_lower_confidence(self):
        fields = ldi._parse_fields_from_text("Institution - IIT Bombay")
        assert fields["Institution"] == {"value": "IIT Bombay", "confidence": 0.6}

    def test_adversarial_lines_without_a_label_value_shape_are_skipped(self):
        fields = ldi._parse_fields_from_text("just a plain sentence with no separator at all")
        assert fields == {}

    def test_edge_empty_text_returns_no_fields(self):
        assert ldi._parse_fields_from_text("") == {}


class TestAnalyzeDocumentLocally:
    def test_golden_extracts_fields_from_a_real_text_layer_pdf(self):
        pdf_bytes = _real_pdf_bytes(["Name: Priya Sharma", "Degree: B.Tech Computer Science"])
        fields = ldi.analyze_document_locally(pdf_bytes)
        assert fields["Name"]["value"] == "Priya Sharma"
        assert fields["Degree"]["value"] == "B.Tech Computer Science"

    def test_adversarial_image_bytes_without_ocr_installed_raises_clear_error(self):
        """pytesseract isn't installed in this environment (confirmed
        during development) -- a scanned/photographed document must fail
        with a clear, actionable message, never silently return {}."""
        import pytest

        fake_image_bytes = b"\x89PNG\r\n\x1a\nnot a real png, just not a %PDF header"
        with pytest.raises(RuntimeError, match="OCR"):
            ldi.analyze_document_locally(fake_image_bytes)


class TestDocumentIntelligenceProviderSelection:
    def test_golden_falls_back_to_local_when_azure_not_configured(self, monkeypatch):
        import document_intelligence_client

        monkeypatch.delenv("DOCUMENT_INTELLIGENCE_ENDPOINT", raising=False)
        monkeypatch.delenv("DOCUMENT_INTELLIGENCE_KEY", raising=False)

        pdf_bytes = _real_pdf_bytes(["Name: Priya Sharma"])
        fields, method = document_intelligence_client.analyze_document(pdf_bytes)
        assert method == "local_text_extraction"
        assert fields["Name"]["value"] == "Priya Sharma"

    def test_adversarial_partial_azure_config_is_treated_as_not_configured(self, monkeypatch):
        import document_intelligence_client

        monkeypatch.setenv("DOCUMENT_INTELLIGENCE_ENDPOINT", "https://fake.cognitiveservices.azure.com/")
        monkeypatch.delenv("DOCUMENT_INTELLIGENCE_KEY", raising=False)
        assert document_intelligence_client._azure_config() is None

    def test_golden_azure_config_resolved_when_both_present(self, monkeypatch):
        import document_intelligence_client

        monkeypatch.setenv("DOCUMENT_INTELLIGENCE_ENDPOINT", "https://fake.cognitiveservices.azure.com/")
        monkeypatch.setenv("DOCUMENT_INTELLIGENCE_KEY", "fake-key")
        assert document_intelligence_client._azure_config() == ("https://fake.cognitiveservices.azure.com/", "fake-key")
