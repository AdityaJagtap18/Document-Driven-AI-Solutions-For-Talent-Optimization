import pytest

import whatsapp_client


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {"messages": [{"id": "wamid.fake123"}]}

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code} error", response=self)


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    """Every test gets WhatsApp 'configured' by default via secrets_loader,
    without touching real env vars or .env."""
    monkeypatch.setattr(
        whatsapp_client, "get_secret",
        lambda name, default=None: {"WHATSAPP_PHONE_NUMBER_ID": "123456", "WHATSAPP_ACCESS_TOKEN": "fake-token"}.get(name, default),
    )


class TestSendWhatsappText:
    def test_golden_builds_correct_payload(self, monkeypatch):
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _FakeResponse()

        monkeypatch.setattr(whatsapp_client.requests, "post", fake_post)

        result = whatsapp_client.send_whatsapp_text("15551234567", "Hello from CDPI")

        assert "123456/messages" in captured["url"]
        assert captured["json"]["to"] == "15551234567"
        assert captured["json"]["type"] == "text"
        assert captured["json"]["text"]["body"] == "Hello from CDPI"
        assert captured["headers"]["Authorization"] == "Bearer fake-token"
        assert result["messages"][0]["id"] == "wamid.fake123"

    def test_adversarial_meta_api_error_raises(self, monkeypatch):
        monkeypatch.setattr(whatsapp_client.requests, "post", lambda *a, **kw: _FakeResponse(status_code=400, json_data={"error": "bad request"}))
        with pytest.raises(Exception):
            whatsapp_client.send_whatsapp_text("15551234567", "Hello")

    def test_adversarial_not_configured_raises_clear_error(self, monkeypatch):
        monkeypatch.setattr(whatsapp_client, "get_secret", lambda name, default=None: None)
        with pytest.raises(RuntimeError, match="WHATSAPP_PHONE_NUMBER_ID"):
            whatsapp_client.send_whatsapp_text("15551234567", "Hello")


class TestSendWhatsappTemplate:
    def test_golden_builds_correct_payload_with_parameters(self, monkeypatch):
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["json"] = json
            return _FakeResponse()

        monkeypatch.setattr(whatsapp_client.requests, "post", fake_post)

        whatsapp_client.send_whatsapp_template(
            "15551234567", "interview_invite", parameters=["Priya Sharma", "Oct 20, 3pm"],
        )

        template = captured["json"]["template"]
        assert template["name"] == "interview_invite"
        assert template["language"]["code"] == "en_US"
        body_params = template["components"][0]["parameters"]
        assert body_params == [{"type": "text", "text": "Priya Sharma"}, {"type": "text", "text": "Oct 20, 3pm"}]

    def test_edge_no_parameters_omits_components(self, monkeypatch):
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["json"] = json
            return _FakeResponse()

        monkeypatch.setattr(whatsapp_client.requests, "post", fake_post)

        whatsapp_client.send_whatsapp_template("15551234567", "simple_notice")

        assert captured["json"]["template"]["components"] == []

    def test_golden_custom_language_code(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(whatsapp_client.requests, "post", lambda url, **kw: (captured.update(kw) or _FakeResponse()))

        whatsapp_client.send_whatsapp_template("15551234567", "greeting", language_code="hi_IN")

        assert captured["json"]["template"]["language"]["code"] == "hi_IN"
