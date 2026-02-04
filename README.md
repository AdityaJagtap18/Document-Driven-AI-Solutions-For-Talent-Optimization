# Cognitive Document Intelligence Platform

An AI-powered resume parsing and candidate evaluation system with automated interview scheduling and virtual interview capabilities.

## Overview

This platform leverages advanced AI/LLM technology to automate the entire recruitment pipeline - from resume parsing and candidate scoring to interview scheduling and conducting virtual interviews through a voice-enabled bot.

**Institution:** DY Patil College of Engineering, Charoli
**Academic Year:** 2024-25 (B.E. Final Year Project)

## Features

### Resume Parsing Engine
- **PDF Text Extraction** using PyPDF2
- **Smart Chunking** for large resumes (max 3000 tokens per chunk)
- **Contact Detail Extraction** (Email, Phone, LinkedIn)
- **AI-Powered Summarization** using GPT-3.5-turbo
- **Structured JSON Output** with 20+ candidate attributes including:
  - JD Match percentage
  - Skills analysis (matching & missing keywords)
  - Project relevance scoring
  - Certifications & courses
  - Culture fit assessment
  - Risk analysis & red flags
  - Resume strength score (0-10)

### Candidate Metrics & Ranking System
Implements a weighted scoring algorithm with 12 evaluation dimensions:

| Dimension | Weight |
|-----------|--------|
| JD Match Score | 15% |
| Project Score | 20% |
| Skill Score | 20% |
| Experience Score | 10% |
| Candidate Type Score | 10% |
| Certificate Score | 5% |
| Contribution Score | 5% |
| Soft Skills Score | 5% |
| Cultural Fit Score | 3% |
| Effort Score | 3% |
| Industry Penalty | 2% |
| Penalty Score | 2% |

**Ranking Output:**
- Top 25% → "Recommended for Fast-Track"
- Middle 50% → "To be Considered"
- Bottom 25% → "Rejected"

### Real-Time File Monitoring
- Monitors input folder for new resume uploads
- Gmail integration for automatic attachment downloads
- Background processing with async file queuing
- Auto-triggers parsing pipeline on new files

### Interview Invitation System
- IMAP-based interview schedule retrieval
- Personalized HTML email generation
- ICS calendar file creation for candidates
- Automated distribution to stakeholders
- Archive management for processed data

### Voice Bot for Virtual Interviews (Streamlit)
An interactive voice-enabled interview bot for conducting online virtual interviews:
- **Text-to-Speech (TTS)** for asking interview questions
- **Speech Recognition** for capturing candidate responses
- **Real-time Interview Simulation** for remote candidates
- **Streamlit Web Interface** for easy access and interaction

## Project Structure

```
BEProjectResumeParser/
├── Core Scripts
│   ├── ResumeParser.py           # Main resume parsing engine
│   ├── ResumeMEtrics.py          # Candidate scoring/ranking
│   └── file_watcher.py           # Real-time folder monitoring
├── Interview Management
│   ├── InviteSender/
│   │   ├── send_interview_invites.py
│   │   ├── imap_download.py
│   │   ├── scheduler.py
│   │   └── config/
│   └── SendCandidateMetricsEmail.py
├── Voice Bot
│   └── [Streamlit TTS Interview Bot]
├── Configuration
│   ├── config/
│   │   ├── scoring_config.json
│   │   ├── email_credentials.json
│   │   └── email_templates/
│   └── .env
├── Data Folders
│   ├── jd_folder/                # Job descriptions
│   ├── InputResume_Folder/       # Incoming resumes
│   ├── OutputResume_Folder/      # Processed resumes
│   ├── Resume_Parsed_CSVs/       # Parsed data output
│   ├── CandidateMetrics_Folder/  # Rankings & scores
│   └── Interview_Slots_Data/     # Schedule data
├── Notebooks
│   ├── ResumeParser.ipynb
│   └── ResumeMEtrics.ipynb
└── Batch Scripts
    ├── ResumeParser.bat
    ├── Filewatcher.bat
    └── RunMetrics.bat
```

## Technologies Used

| Component | Technology |
|-----------|-----------|
| LLM & NLP | OpenAI GPT-3.5-turbo, LangChain, Google GenAI |
| PDF Processing | PyPDF2 |
| Data Processing | Pandas, NumPy |
| File Monitoring | Watchdog |
| Email Integration | IMAP4_SSL, SMTP |
| Voice Bot UI | Streamlit |
| Text-to-Speech | TTS Libraries |
| Language | Python 3.x |
| Packaging | PyInstaller |

## Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd BEProjectResumeParser
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/Mac
   # or
   venv\Scripts\activate     # Windows
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**

   Create a `.env` file with:
   ```env
   OPENAI_API_KEY=your_openai_api_key
   INPUT_FOLDER=./InputResume_Folder
   OUTPUT_FOLDER=./OutputResume_Folder
   CSV_FOLDER=./Resume_Parsed_CSVs
   ```

5. **Configure email credentials**

   Update `config/email_credentials.json` with your SMTP/IMAP settings.

## Usage

### Resume Parsing
```bash
python ResumeParser.py
```
Or use the batch file:
```bash
./ResumeParser.bat
```

### File Watcher (Background Monitoring)
```bash
python file_watcher.py
```

### Generate Candidate Metrics
```bash
python ResumeMEtrics.py
```

### Send Interview Invitations
```bash
python InviteSender/scheduler.py
```

### Voice Bot (Virtual Interview)
```bash
streamlit run voice_bot.py
```

## Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│                         RESUME INPUT                                 │
│  (PDF Upload / Email Attachment / Folder Drop)                       │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      FILE WATCHER                                    │
│  (Monitors folder, downloads from Gmail)                             │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     RESUME PARSER                                    │
│  (PDF extraction → Chunking → LLM Analysis → JSON Output)            │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   CANDIDATE METRICS                                  │
│  (12-dimension scoring → Ranking → Selection tiers)                  │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
┌───────────────────────────────┐ ┌───────────────────────────────────┐
│      EMAIL DISTRIBUTION       │ │      INTERVIEW SCHEDULING         │
│  (Reports to stakeholders)    │ │  (ICS invites to candidates)      │
└───────────────────────────────┘ └───────────────────────────────────┘
                                              │
                                              ▼
                            ┌─────────────────────────────────────────┐
                            │          VOICE BOT INTERVIEW            │
                            │  (Streamlit + TTS for virtual interviews)│
                            └─────────────────────────────────────────┘
```

## Configuration

### Scoring Weights (`config/scoring_config.json`)
Customize the scoring algorithm by adjusting weights for each evaluation dimension.

### Email Templates (`config/email_templates/`)
- `talent_acquisition.json` - Reports for TA team
- `technical_evaluation.json` - Technical assessment reports
- `business_lead.json` - Executive summaries
- `interview_invite_template.json` - Candidate invitations

## Voice Bot - Virtual Interview System

The Streamlit-based voice bot enables online virtual interviews for candidates:

### Features
- **Text-to-Speech (TTS)**: Audibly asks interview questions to candidates
- **Speech Recognition**: Captures and transcribes candidate responses
- **Interactive Web Interface**: Easy-to-use Streamlit UI
- **Real-time Processing**: Immediate feedback and response handling
- **Session Management**: Tracks interview progress and responses

### How It Works
1. Candidate accesses the interview portal via web browser
2. Voice bot greets the candidate and explains the process
3. Questions are presented both visually and through TTS audio
4. Candidate responds verbally (speech-to-text captures responses)
5. Responses are recorded and stored for evaluation
6. Interview summary is generated upon completion

## Logs & Monitoring

- **Master Log:** `log.txt`
- **Session Logs:** `logs/log_YYYY-MM-DD_HH-MM-SS.txt`
- **Metrics Logs:** `logs/metrics_log_*.txt`

## Team

- **Group 3** - B.E. Final Year Students
- DY Patil College of Engineering, Charoli

## License

This project is developed for academic purposes as part of the B.E. Final Year curriculum.

---

*Built with AI-powered intelligence for smarter recruitment*
