"""Azure AI Speech replacement for voice_bot.py's two non-Azure speech
vendors: ElevenLabs (TTS, elevenlabs_utils.py) and SpeechRecognition's
`recognize_google()` (STT, an unofficial/unsupported free Google Web Speech
endpoint, no API key). Both get replaced by a single Azure Cognitive
Services Speech resource — one fewer vendor in the Vendor_Risk_Register,
same rationale already used for Phase 5's WhatsApp/Document Intelligence
additions.

*** UNTESTED AGAINST THE REAL AZURE SPEECH SERVICE. *** No resource is
provisioned, and unlike Cosmos DB there is no way to exercise this offline
— speech synthesis/recognition needs a live endpoint and (for recognize())
a real microphone, neither available in this environment. What IS done:
the real `azure-cognitiveservices-speech` SDK (1.52.0) is installed and
every class/method used below had its signature confirmed via
`inspect.signature()` — see the module docstring pattern already used for
document_intelligence_client.py.

Known gap, not papered over: the original `listen(save_path=None)` in
voice_bot.py also saves the raw mic audio to a .wav file. Azure Speech's
`recognize_once_async()` doesn't expose the raw audio it captured from the
default microphone — getting that would need a custom
PushAudioInputStream/callback wrapper that intercepts audio frames before
they reach the recognizer. Not built here; `save_path` is accepted for
call-signature compatibility with the function it replaces, but a file is
never written and a `[WARN]` is printed if a caller passes one, so this
doesn't silently drop a feature.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from secrets_loader import get_secret  # noqa: E402


def _require_config():
    key = get_secret("AZURE_SPEECH_KEY")
    region = get_secret("AZURE_SPEECH_REGION")
    missing = [name for name, val in (("AZURE_SPEECH_KEY", key), ("AZURE_SPEECH_REGION", region)) if not val]
    if missing:
        raise RuntimeError(
            f"Missing required secret(s): {', '.join(missing)}. Add them to .env "
            "(see .env.example) or configure AZURE_KEY_VAULT_URL."
        )
    return key, region


def speak(text: str, voice_name: str = "en-US-JennyNeural") -> None:
    """Synthesizes `text` to the default speaker. Raises RuntimeError on
    synthesis failure (cancellation) rather than failing silently — callers
    in voice_bot.py already wrap LLM/speech calls defensively, same
    fail-soft convention as the rest of that module."""
    import azure.cognitiveservices.speech as speechsdk

    key, region = _require_config()
    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    speech_config.speech_synthesis_voice_name = voice_name
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config)

    result = synthesizer.speak_text_async(text).get()
    if result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        raise RuntimeError(f"Azure Speech synthesis canceled: {details.reason} - {details.error_details}")


def listen(save_path: str = None) -> tuple:
    """Listens on the default microphone for one utterance, returns
    (text, save_path) — same shape as voice_bot.py's original listen(), so
    it's a drop-in replacement at call sites. Returns ("", None) on
    no-match or a recognition error (never raises), same fail-soft
    behavior as the function it replaces."""
    import azure.cognitiveservices.speech as speechsdk

    if save_path:
        print("[WARN] azure_speech_utils.listen(): save_path is accepted for "
              "compatibility but not implemented — no audio file will be written.")

    try:
        key, region = _require_config()
    except RuntimeError as e:
        print("Speech config error:", e)
        return "", None

    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    audio_config = speechsdk.audio.AudioConfig(use_default_microphone=True)
    recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

    try:
        result = recognizer.recognize_once_async().get()
    except Exception as e:
        print("Mic Error:", e)
        return "", None

    if result.reason == speechsdk.ResultReason.RecognizedSpeech:
        print("User:", result.text)
        return result.text.strip(), save_path
    elif result.reason == speechsdk.ResultReason.NoMatch:
        return "", None
    else:
        details = result.cancellation_details
        print("API error:", f"{details.reason} - {details.error_details}")
        return "", None
