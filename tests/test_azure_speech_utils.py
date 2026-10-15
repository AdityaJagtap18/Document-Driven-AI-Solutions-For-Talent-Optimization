"""Tests for voicebot/azure_speech_utils.py. The real azure-cognitiveservices-
speech SDK needs a live Speech resource and (for listen()) a real
microphone, neither available here, so these tests inject a fake SDK module
into sys.modules before calling speak()/listen() — same idea as mocking
requests.post for whatsapp_client, just one level lower since the import
happens inside the function body."""

import sys
import types

import pytest

import azure_speech_utils as asu


class _FakeCancellationDetails:
    def __init__(self, reason="Error", error_details="boom"):
        self.reason = reason
        self.error_details = error_details


class _FakeResult:
    def __init__(self, reason, text="", cancellation_details=None):
        self.reason = reason
        self.text = text
        self.cancellation_details = cancellation_details


class _FakeFuture:
    def __init__(self, result):
        self._result = result

    def get(self):
        return self._result


def _install_fake_sdk(monkeypatch, synth_result=None, recognize_result=None, raise_on_recognize=None):
    fake_sdk = types.ModuleType("azure.cognitiveservices.speech")
    fake_sdk.ResultReason = types.SimpleNamespace(
        Canceled="Canceled", RecognizedSpeech="RecognizedSpeech", NoMatch="NoMatch",
    )

    class FakeSpeechConfig:
        def __init__(self, subscription=None, region=None):
            self.subscription = subscription
            self.region = region
            self.speech_synthesis_voice_name = None

    class FakeSynthesizer:
        def __init__(self, speech_config):
            self.speech_config = speech_config

        def speak_text_async(self, text):
            self.last_text = text
            return _FakeFuture(synth_result)

    class FakeRecognizer:
        def __init__(self, speech_config, audio_config=None):
            self.speech_config = speech_config
            self.audio_config = audio_config

        def recognize_once_async(self):
            if raise_on_recognize:
                raise raise_on_recognize
            return _FakeFuture(recognize_result)

    fake_sdk.SpeechConfig = FakeSpeechConfig
    fake_sdk.SpeechSynthesizer = FakeSynthesizer
    fake_sdk.SpeechRecognizer = FakeRecognizer

    fake_audio = types.ModuleType("azure.cognitiveservices.speech.audio")
    fake_audio.AudioConfig = lambda use_default_microphone=False: types.SimpleNamespace(use_default_microphone=use_default_microphone)
    fake_sdk.audio = fake_audio

    fake_cognitiveservices = types.ModuleType("azure.cognitiveservices")
    fake_cognitiveservices.speech = fake_sdk
    fake_azure = types.ModuleType("azure")
    fake_azure.cognitiveservices = fake_cognitiveservices

    # The full dotted chain must be in sys.modules (not just the leaf) —
    # `import a.b.c` resolves a, then a.b, then a.b.c via sys.modules
    # lookups, and a real "azure" package is already installed in this
    # environment, so every link needs to be the fake one or Python's
    # import machinery falls through to the real SDK instead.
    monkeypatch.setitem(sys.modules, "azure", fake_azure)
    monkeypatch.setitem(sys.modules, "azure.cognitiveservices", fake_cognitiveservices)
    monkeypatch.setitem(sys.modules, "azure.cognitiveservices.speech", fake_sdk)
    monkeypatch.setitem(sys.modules, "azure.cognitiveservices.speech.audio", fake_audio)
    return fake_sdk


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(
        asu, "get_secret",
        lambda name, default=None: {"AZURE_SPEECH_KEY": "fake-key", "AZURE_SPEECH_REGION": "eastus"}.get(name, default),
    )


class TestSpeak:
    def test_golden_successful_synthesis_does_not_raise(self, monkeypatch):
        fake_sdk = _install_fake_sdk(monkeypatch, synth_result=_FakeResult(reason="SynthesizingAudioCompleted"))
        asu.speak("hello there")  # no exception = success

    def test_adversarial_cancellation_raises_with_details(self, monkeypatch):
        cancel = _FakeCancellationDetails(reason="Error", error_details="quota exceeded")
        result = _FakeResult(reason="Canceled", cancellation_details=cancel)
        _install_fake_sdk(monkeypatch, synth_result=result)
        with pytest.raises(RuntimeError, match="quota exceeded"):
            asu.speak("hello there")

    def test_adversarial_missing_config_raises_before_any_sdk_call(self, monkeypatch):
        monkeypatch.setattr(asu, "get_secret", lambda name, default=None: None)
        with pytest.raises(RuntimeError, match="AZURE_SPEECH_KEY"):
            asu.speak("hello there")


class TestListen:
    def test_golden_recognized_speech_returns_text(self, monkeypatch):
        _install_fake_sdk(monkeypatch, recognize_result=_FakeResult(reason="RecognizedSpeech", text=" hi there "))
        text, save_path = asu.listen()
        assert text == "hi there"
        assert save_path is None

    def test_edge_no_match_returns_empty_string(self, monkeypatch):
        _install_fake_sdk(monkeypatch, recognize_result=_FakeResult(reason="NoMatch"))
        text, save_path = asu.listen()
        assert text == ""
        assert save_path is None

    def test_edge_save_path_accepted_but_not_implemented(self, monkeypatch, capsys):
        _install_fake_sdk(monkeypatch, recognize_result=_FakeResult(reason="RecognizedSpeech", text="ok"))
        text, save_path = asu.listen(save_path="/tmp/out.wav")
        assert text == "ok"
        assert save_path == "/tmp/out.wav"
        assert "not implemented" in capsys.readouterr().out

    def test_adversarial_mic_exception_returns_empty_not_raise(self, monkeypatch):
        _install_fake_sdk(monkeypatch, raise_on_recognize=RuntimeError("no microphone found"))
        text, save_path = asu.listen()
        assert text == ""
        assert save_path is None

    def test_adversarial_missing_config_returns_empty_not_raise(self, monkeypatch):
        monkeypatch.setattr(asu, "get_secret", lambda name, default=None: None)
        text, save_path = asu.listen()
        assert text == ""
        assert save_path is None
