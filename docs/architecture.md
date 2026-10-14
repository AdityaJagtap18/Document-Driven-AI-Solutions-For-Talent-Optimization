# CDPI Architecture: Current vs. Target

See also the interactive version: `private/CDPI_FDE_Tracker.xlsx` (Azure_Service_Mapping sheet) for cost tiers and Azure-for-Students availability per component. This file is gitignored (local-only) — ask whoever has it for a copy if you don't.

## Current (Brownfield)

Seven standalone Python scripts chained by subprocess calls and folder polling. No shared data store, no frontend, zero automated tests. The n8n WhatsApp flow (dashed, below) is lost — access was lost and it isn't recoverable from this repo.

```mermaid
flowchart LR
  GM[Gmail inbox<br/>IMAP poll, 60s] --> FW[file_watcher.py<br/>watchdog + subprocess.call]
  PDF[Local folder drop] --> FW
  FW -->|"shell out"| RP[ResumeParser.py<br/>PyPDF2 + LangChain + GPT-3.5]
  RP --> CSV1[(resume_summary.csv)]
  CSV1 --> RM[ResumeMEtrics.py<br/>12-dim weighted scoring]
  RM --> CSV2[(candidate_ranking_metrics.csv)]
  CSV2 -->|email| EM[SMTP: TA / Business Lead / Tech Eval]
  CSV1 --> GQ[generate_questions.py<br/>GPT-3.5]
  GQ --> JSON1[(InterviewData/*.json)]
  CSV2 --> IS[InviteSender<br/>IMAP + SMTP + .ics]
  IS -->|"email only"| CAND[Candidate inbox]
  VB[voicebot/voice_bot.py<br/>Streamlit + GPT-3.5] --> ELV[ElevenLabs TTS]
  VB --> GSR[Google Speech STT]
  LOG[(log.txt freeform text)]
  RP -.-> LOG
  N8N[n8n WhatsApp flow<br/>ACCESS LOST] -.->|undocumented| CAND
```

## Target (Azure-Native)

Event-driven pipeline, one data layer (the context lake), semantic resume-to-JD matching, and a frontend dashboard replacing five scattered file trees and a subprocess chain. Green-stroked nodes in the rendered diagram are net-new capability with no brownfield equivalent today.

```mermaid
flowchart LR
  GRAPH[Microsoft Graph / Logic Apps<br/>Gmail connector] --> BLOB[(Azure Blob Storage<br/>raw-resumes)]
  UP[Dashboard upload] --> BLOB
  BLOB -->|Event Grid trigger| FUNC1[Azure Functions<br/>parse + chunk]
  FUNC1 --> AOAI[Azure AI Foundry<br/>GPT-4o-mini / embeddings]
  AOAI --> SEARCH[(Azure AI Search<br/>vector + hybrid index)]
  FUNC1 --> COSMOS[(Azure Cosmos DB<br/>candidates / interviews / scoring_history / audit_log)]
  SEARCH -.->|RAG context| FUNC1
  COSMOS --> FUNC2[Azure Functions<br/>scoring + tiering]
  FUNC2 --> COSMOS
  COSMOS --> API[Azure Functions API]
  API --> SWA[Azure Static Web Apps<br/>Recruiter Dashboard]
  SWA --> AAD[Microsoft Entra ID<br/>auth + RBAC]
  FUNC2 -->|invite| ACS[Azure Communication Services: Email]
  FUNC2 -->|notify| LOGIC[Azure Logic Apps]
  LOGIC --> WA[WhatsApp Business Cloud API]
  VB2[Voicebot app<br/>Container Apps] --> SPEECH[Azure AI Speech<br/>TTS + STT]
  VB2 --> AOAI
  FUNC1 --> MON[(Azure Monitor / App Insights)]
  FUNC2 --> MON
  MON --> SWA
  KV[(Azure Key Vault)] -.->|secrets, no plaintext| FUNC1
  KV -.-> FUNC2
  KV -.-> VB2
```

Related design docs: [`../prompts/README.md`](../prompts/README.md) (prompt library), [`../semantic-library/README.md`](../semantic-library/README.md) (vector search), [`../context-lake/README.md`](../context-lake/README.md) (Cosmos DB / Blob data layer).
