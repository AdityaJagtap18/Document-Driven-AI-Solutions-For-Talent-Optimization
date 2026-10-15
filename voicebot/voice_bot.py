import pandas as pd
import speech_recognition as sr
import json
import sys
import time
from pathlib import Path
from elevenlabs_utils import speak
import openai
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage
import os
import json as jsonlib
import uuid
import wave
import re
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm_utils import call_with_retry  # noqa: E402
from secrets_loader import get_secret  # noqa: E402

_openai_key = get_secret("OPENAI_API_KEY")
if not _openai_key:
    raise RuntimeError("OPENAI_API_KEY not set. Add it to your .env file (see .env.example) or configure AZURE_KEY_VAULT_URL.")
os.environ["OPENAI_API_KEY"] = _openai_key

llm = ChatOpenAI(
    temperature=0.7,
    max_tokens=1000,
    model="gpt-3.5-turbo",
)


def ask_openai(prompt, label="voicebot_call"):
    try:
        messages = [
            SystemMessage(content="You are a friendly and helpful interview assistant and your only here to warm up the candidate your job is to only make the candidate feel good till the actuall interviewer arives. Do not ask leading questions"),
            HumanMessage(content=prompt)
        ]
        response = call_with_retry(
            llm, messages,
            on_retry=lambda attempt, e: print(f"[LangChain/OpenAI Retry {attempt}]: {e}"),
            label=label, model="gpt-3.5-turbo",
        )
        return response.content
    except Exception as e:
        print(f"[LangChain/OpenAI Error]: {e}")
        return "Thanks for your response! Let's continue."


def listen(save_path=None):
    r = sr.Recognizer()
    r.energy_threshold = 300
    r.pause_threshold = 2.0
    r.dynamic_energy_threshold = True

    with sr.Microphone() as source:
        print("Listening... Speak freely. Stop when you're done.")
        try:
            audio = r.listen(source)
        except Exception as e:
            print("Mic Error:", e)
            return "", None

    try:
        text = r.recognize_google(audio)
        print("User:", text)
        # Save audio
        if save_path:
            with open(save_path, "wb") as f:
                f.write(audio.get_wav_data())
        return text.strip(), save_path
    except sr.UnknownValueError:
        return "", None
    except sr.RequestError as e:
        print("API error:", e)
        return "", None

def run_voice_interview():
    df = pd.read_csv("resume_summary.csv")
    candidate = df.iloc[0]
    default_name = candidate["Name"]

    with open("jd.txt", "r", encoding="utf-8") as f:
        job_description = f.read()

    yield "🎤 Bot: Hello! I'm your virtual assistant for today's interview."
    speak("Hello! I'm your virtual assistant for today's interview.")
    yield "🎤 Bot: May I know your name, please?"
    speak("May I know your name, please?")
    spoken_name, _ = listen()

    matched_candidate = None
    for _, row in df.iterrows():
        if spoken_name.lower() in row["Name"].lower():
            matched_candidate = row
            break

    if matched_candidate is not None:
        default_name = matched_candidate["Name"]
        yield f"🎤 Bot: Great, {default_name}! You're all set. Let’s begin."
        speak(f"Great, {default_name}! You're all set. Let’s begin.")

        candidate_folder = f"InterviewData/{default_name.replace(' ', '_')}"
        os.makedirs(candidate_folder, exist_ok=True)

        prompt = (
            f"Generate 3 friendly and warm-up interview questions for a candidate named {default_name}, "
            f"who has {matched_candidate['Relevant Experience (yrs)']} experience and skills in {matched_candidate['Top Matching Keywords']}. "
            "Keep the tone warm and introductory. These are not technical questions, just ice-breakers."
        )

        try:
            question_list = ask_openai(prompt, label="voicebot_warmup_questions").split("\n")
            questions = [q.strip("- ").strip() for q in question_list if q.strip()]
        except:
            questions = ["How are you feeling today?", "Tell me about yourself.", "What attracted you to this role?"]

        with open(os.path.join(candidate_folder, "questions.json"), "w", encoding="utf-8") as f:
            jsonlib.dump(questions, f, indent=2)

        yield f"🎤 Bot: Nice to meet you, {default_name}. Let’s start with a few warm-up questions."
        speak(f"Nice to meet you, {default_name}. Let’s start with a few warm-up questions.")

        answers = []
        for q in questions:
            yield f"🎤 Bot: {q}"
            speak(q)
            answer, _ = listen()

            if answer:
                yield f"🗣️ You: {answer}"
                try:
                    ai_reply = ask_openai(f"The candidate said: '{answer}'. Respond supportively as a warm interviewer.", label="voicebot_followup_response")
                except:
                    ai_reply = "Thank you for your response!"
                speak(ai_reply)
                yield f"🎤 Bot: {ai_reply}"
            else:
                yield f"🗣️ You: (No response)"
                try:
                    ai_reply = ask_openai(f"The candidate did not respond to: '{q}'. Say something gentle and supportive.", label="voicebot_followup_no_response")
                except:
                    ai_reply = "It's okay, take your time. Let's move to the next one."
                speak(ai_reply)
                yield f"🎤 Bot: {ai_reply}"

            answers.append({"question": q, "answer": answer or "(No response)"})


        with open(os.path.join(candidate_folder, "answers.json"), "w", encoding="utf-8") as f:
            jsonlib.dump(answers, f, indent=2)

        scoring_prompt = (
            f"Job Description:\n{job_description}\n\n"
            f"Evaluate the candidate's answers below based on relevance, clarity, and enthusiasm. "
            f"Return an overall score out of 10.\n\n"
        )
        for i, qa in enumerate(answers, 1):
            scoring_prompt += f"Q{i}: {qa['question']}\nA{i}: {qa['answer']}\n"


        try:
            score_text = ask_openai(scoring_prompt, label="voicebot_transcript_scoring")
            
            # Extract the first valid number (integer or decimal) from the text
            score_match = re.search(r"\b(\d+(\.\d+)?)\b", score_text)
            
            if score_match:
                score = float(score_match.group(1))
            else:
                raise ValueError("No valid score found in response.")
                
        except Exception as e:
            print("Scoring error:", e)
            score = 0


        with open(os.path.join(candidate_folder, "score.json"), "w") as f:
            json.dump({"score": score}, f, indent=2)

        master_csv = "InterviewScores.csv"
        if not os.path.exists(master_csv):
            pd.DataFrame(columns=["Candidate", "Score"]).to_csv(master_csv, index=False)
        df_scores = pd.read_csv(master_csv)
        df_scores = pd.concat([df_scores, pd.DataFrame([{"Candidate": default_name, "Score": score}])])
        df_scores.to_csv(master_csv, index=False)

        yield f"🎤 Bot: Thanks for sharing. You did great!"
        speak("Thanks for sharing. You did great!")
        yield f"🎤 Bot: The interviewer will now join and take over the interview. Best of luck, {default_name}!"
        speak(f"The interviewer will now join and take over the interview. Best of luck, {default_name}!")
        return default_name

    else:
        yield f"🎤 Bot: Thank you, {spoken_name}."
        speak(f"Thank you, {spoken_name}.")
        yield "🎤 Bot: Unfortunately, you are not in our registered candidate list."
        speak("Unfortunately, you are not in our registered candidate list.")
        yield "🎤 Bot: Please register first on our platform and then we’ll schedule your interview."
        speak("Please register first on our platform and then we’ll schedule your interview.")
        return spoken_name
    


