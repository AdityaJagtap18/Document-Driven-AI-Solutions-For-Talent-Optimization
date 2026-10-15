# Phase 6 Provisioning: Azure AI Speech

Replaces two non-Azure vendors in `voicebot/`: **ElevenLabs** (text-to-speech) and the unofficial, keyless Google Web Speech endpoint that `SpeechRecognition`'s `recognize_google()` was calling (speech-to-text). Both become a single Azure AI Speech resource — one fewer vendor in the Vendor_Risk_Register.

## Create the resource

Portal → "Speech service" (under Azure AI Services) → Create.
- Resource group: `cdpi-rg` (same one from earlier phases, if you made one)
- Region: pick one that supports Speech (most do) — note it, you need the region name (e.g. `eastus`), not just the resource name
- Pricing tier: **Free (F0)** if available in your region — covers 5 audio hours/month free, enough for development

Once created: resource → **Keys and Endpoint** → copy:
- `KEY 1` → this is `AZURE_SPEECH_KEY`
- `Location/Region` → this is `AZURE_SPEECH_REGION` (the short region code, e.g. `eastus`, not a URL)

Add both to `.env`:
```env
AZURE_SPEECH_KEY=your-key-here
AZURE_SPEECH_REGION=eastus
```

## What changed in the code

- `voicebot/azure_speech_utils.py` (new) replaces `voicebot/elevenlabs_utils.py` (removed) for TTS, and replaces `voice_bot.py`'s local `listen()` function (which wrapped `SpeechRecognition`'s `recognize_google()`) for STT.
- `voicebot/requirements.txt`: dropped `SpeechRecognition`, `elevenlabs`, `pyaudio`; added `azure-cognitiveservices-speech`.
- **Known gap, not fixed here**: the original `listen(save_path=...)` also saved the raw mic audio to a `.wav` file (used by the candidate-folder recording flow). Azure Speech's quick `recognize_once_async()` doesn't expose the raw audio it captured — capturing it too needs a custom audio-stream callback wrapper. `azure_speech_utils.listen()` accepts `save_path` for call-signature compatibility but prints a `[WARN]` and never writes a file. Fixing this for real is follow-up work if the recorded-audio file turns out to matter beyond the transcript.

## Verifying it works

Neither this session nor any provisioned resource has exercised this against the real Azure Speech service — there's no microphone or live endpoint available in this environment. Once configured:
- **TTS**: run `python voicebot/voice_bot.py` (or trigger `speak()` directly) and confirm audio actually plays from the default speaker instead of raising a `RuntimeError`.
- **STT**: during a voice interview run, confirm spoken answers get transcribed (printed as `User: ...`) instead of returning an empty string every time.

This is the real verification this component still needs once provisioned — the test suite (`tests/test_azure_speech_utils.py`) only confirms the code's logic is correct against a faked SDK, not that the live service behaves as expected.
