import os
import time
import json
import asyncio
import pandas as pd
import sounddevice as sd
import soundfile as sf
import whisper
import edge_tts

from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Global Voice Settings
VOICE = "en-IN-NeerjaNeural"
RECORD_DURATION = 30  # seconds
WARMUP_QUESTIONS = [
    "How are you today?",
    "Can you tell me a bit about yourself?",
    "Why are you interested in this role?"
]

# Function to speak using edge-tts
async def speak(text):
    tts = edge_tts.Communicate(text, VOICE)
    await tts.save("temp.mp3")
    os.system("start temp.mp3")  # use ffplay for Linux/Mac
    time.sleep(2)  # brief wait for smoother playback start

# Function to record answer
def record_answer(filepath):
    print(f"🎙️ Recording: {filepath}")
    fs = 44100
    rec = sd.rec(int(RECORD_DURATION * fs), samplerate=fs, channels=1)
    sd.wait()
    sf.write(filepath, rec, fs)
    print("✅ Recorded.")

# Transcribe audio using Whisper
def transcribe(filepath, textpath, model):
    result = model.transcribe(filepath)
    with open(textpath, "w") as f:
        f.write(result["text"])
    print(f"📝 Transcript saved to {textpath}")

# Full voice interview logic for a candidate
async def run_voice_interview(candidate_name):
    print(f"\n🎤 Starting voice interview for {candidate_name}")
    base_path = f"InterviewData/{candidate_name}/QnA"
    question_path = f"InterviewData/{candidate_name}/interview_questions.json"
    os.makedirs(base_path, exist_ok=True)

    model = whisper.load_model("base")
    questions = WARMUP_QUESTIONS.copy()

    if os.path.exists(question_path):
        with open(question_path) as f:
            questions += json.load(f)

    for i, q in enumerate(questions, start=1):
        await speak(q)
        wav_path = os.path.join(base_path, f"q{i}.wav")
        txt_path = os.path.join(base_path, f"q{i}.txt")
        record_answer(wav_path)
        transcribe(wav_path, txt_path, model)

    await speak("Thank you. Please wait while our HR representative joins shortly.")
    print("✅ Voice interview completed.\n")

# Load meeting data
df = pd.read_csv("InviteSender/Meeting_Invites/meeting_details.csv")
df['Interview Start Time'] = pd.to_datetime(df['Interview Start Time'], format='%I:%M %p').dt.time
df['Interview End Time'] = pd.to_datetime(df['Interview End Time'], format='%I:%M %p').dt.time

# Setup Chrome with mic enabled and camera blocked
options = Options()
prefs = {
    "profile.default_content_setting_values.media_stream_mic": 1,
    "profile.default_content_setting_values.media_stream_camera": 2
}
options.add_experimental_option("prefs", prefs)
options.add_argument("--use-fake-device-for-media-stream")
options.add_argument("--start-maximized")

driver = webdriver.Chrome(options=options)
print("✅ Interview bot started...\n")

for index, row in df.iterrows():
    name = row['Name']
    candidate_name = name.replace(" ", "_")
    start_time = row['Interview Start Time']
    end_time = row['Interview End Time']
    link = row['Meeting_Link'] + "#config.startWithVideoMuted=true"

    now = datetime.now().time()
    print(f"\n--- {name} ---")
    print(f"Now:   {now}")
    print(f"Start: {start_time}")
    print(f"End:   {end_time}")

    if now > end_time:
        print("⏩ Missed slot. Skipping...")
        continue

    print("🌐 Opening meeting page...")
    driver.get(link)
    time.sleep(5)

    if now < start_time:
        wait_delta = datetime.combine(datetime.today(), start_time) - datetime.combine(datetime.today(), now)
        wait_seconds = int(wait_delta.total_seconds())
        print(f"⏳ Too early. Waiting {wait_seconds} seconds...")
        time.sleep(wait_seconds)

    now = datetime.now().time()
    print(f"🔁 Re-checking time: {now}")
    if start_time <= now <= end_time:
        print("✅ Inside valid window. Attempting to join...")

        try:
            wait = WebDriverWait(driver, 15)

            try:
                name_input = wait.until(EC.visibility_of_element_located((By.XPATH, "//input[@id='enter-room-name']")))
                name_input.clear()
                name_input.send_keys(name)
                print(f"✍️ Entered candidate name.")
            except:
                print("ℹ️ No name input field shown.")

            join_button_xpath = "/html/body/div/div/div/div/div[3]/div[1]/div/div/div[1]/div[2]/div/div"
            wait_for_moderator_xpath = "/html/body/div/div[2]/div[3]/div/div[3]/button[1]/span"

            join_button = wait.until(EC.element_to_be_clickable((By.XPATH, join_button_xpath)))
            join_button.click()
            print("🟢 Clicked Join.")

            try:
                print("⏳ Checking for 'Wait for moderator'...")
                moderator_btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, wait_for_moderator_xpath))
                )
                moderator_btn.click()
                print("🔔 Clicked 'Wait for moderator'.")
            except:
                print("ℹ️ No 'Wait for moderator' button found.")

        except Exception as e:
            print(f"⚠️ Could not join. Error: {e}")
            print("✅ Assuming auto-joined.")

        print("🎙️ Launching voice Q&A...")
        asyncio.run(run_voice_interview(candidate_name))
    else:
        print("🚫 Time window missed after wait.")

    driver.execute_script("window.open('');")
    driver.close()
    driver.switch_to.window(driver.window_handles[0])

print("\n✅ All interviews completed.")
driver.quit()
