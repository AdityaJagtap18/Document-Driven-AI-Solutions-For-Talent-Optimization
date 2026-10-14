import json
import os
import pandas as pd
import speech_recognition as sr
from elevenlabs.client import ElevenLabs
from elevenlabs import stream

_config_path = os.path.join(os.path.dirname(__file__), "config.json")
with open(_config_path, "r", encoding="utf-8") as _f:
    _config = json.load(_f)

elevenlabs = ElevenLabs(
  api_key=_config["elevenlabs_api_key"],
)

def speak(text):
    print(f"Bot speaking: {text}")

    # Generate stream
    audio_stream = elevenlabs.text_to_speech.stream(
        text=text,
        voice_id=_config.get("voice_id", "JBFqnCBsd6RMkjVDRZzb"),
        model_id="eleven_multilingual_v2"
    )
    stream(audio_stream)
