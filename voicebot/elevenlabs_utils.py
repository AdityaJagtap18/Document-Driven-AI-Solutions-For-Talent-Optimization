import pandas as pd
import speech_recognition as sr
from elevenlabs.client import ElevenLabs
from elevenlabs import stream
from elevenlabs.client import ElevenLabs


elevenlabs = ElevenLabs(
  api_key='sk_51f7709dfcf642e8395f254ff6a9f548ad354d02be043cb8',
)

def speak(text):
    print(f"Bot speaking: {text}")
    
    # Generate stream
    audio_stream = elevenlabs.text_to_speech.stream(
        text=text,
        voice_id="JBFqnCBsd6RMkjVDRZzb",
        model_id="eleven_multilingual_v2"
    )
    stream(audio_stream)  
