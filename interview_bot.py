import os
import json
from pathlib import Path
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage
from dotenv import load_dotenv

# === CONFIG ===
load_dotenv()
if not os.environ.get("OPENAI_API_KEY"):
    raise RuntimeError("OPENAI_API_KEY not set. Add it to your .env file (see .env.example).")
interview_data_root = Path("InterviewData")
llm = ChatOpenAI(
    temperature=0.5, max_tokens=1000, model="gpt-3.5-turbo",
    model_kwargs={"response_format": {"type": "json_object"}},
)


def run_interview_for_candidate(candidate_folder: Path):
    print(f" Running interview bot for: {candidate_folder.name}")
    
    questions_file = candidate_folder / "interview_questions.json"
    qna_folder = candidate_folder / "QnA"
    scoring_folder = candidate_folder / "scoring"
    qna_folder.mkdir(parents=True, exist_ok=True)
    scoring_folder.mkdir(parents=True, exist_ok=True)

    with open(questions_file, "r", encoding="utf-8") as f:
        questions_data = json.load(f)

    questions = questions_data.get("questions", [])
    answers = []

    for idx, question in enumerate(questions, start=1):
        print(f"\nQ{idx}: {question}")
        answer = input("Your Answer: ").strip()

        with open(qna_folder / f"question{idx}.txt", "w", encoding="utf-8") as fq:
            fq.write(question)
        with open(qna_folder / f"answer{idx}.txt", "w", encoding="utf-8") as fa:
            fa.write(answer)

        answers.append({"question": question, "answer": answer})

    scoring_prompt = f"""
You are a technical interviewer. Score the following candidate's answers out of 10, with brief justification for each.

Return a JSON object with this exact shape:
{{
  "scores": [
    {{
      "question": "...",
      "answer": "...",
      "score": ...,
      "feedback": "..."
    }},
    ...
  ]
}}

Candidate Responses:
{json.dumps(answers, indent=2)}
"""

    print("\nScoring in progress...")

    try:
        response = llm([HumanMessage(content=scoring_prompt)])
        parsed = json.loads(response.content)
        score_data = parsed["scores"]

        with open(scoring_folder / "score_summary.json", "w", encoding="utf-8") as f:
            json.dump(score_data, f, indent=2)

        print("Score summary saved at:", scoring_folder / "score_summary.json")
    except Exception as e:
        print("ERROR during scoring:", e)


def get_latest_candidate_folder():
    folders = [f for f in interview_data_root.iterdir() if f.is_dir()]
    if not folders:
        raise Exception("No candidate folders found.")
    return max(folders, key=os.path.getctime)


if __name__ == "__main__":
    run_interview_for_candidate(get_latest_candidate_folder())
