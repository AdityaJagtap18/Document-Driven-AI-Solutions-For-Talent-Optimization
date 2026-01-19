@echo off
echo ------------------------------------------------------------
echo  Cognitive Document Intelligence Platform
echo  Resume Scoring Matrices Engine - Execution Started
echo ------------------------------------------------------------

cd /d "C:\Users\adity\BE Project\BEProjectResumeParser"

:: Step 1: Generate metrics
echo [INFO] Running ResumeMEtrics.py to generate candidate scores...
python ResumeMEtrics.py

:: Step 2: Send email if metrics are new
echo [INFO] Checking if candidate metrics email needs to be sent...
python SendCandidateMetricsEmail.py

:: Completion
echo ------------------------------------------------------------
echo  Resume scoring and conditional email process completed.
echo ------------------------------------------------------------
echo  Window will close in 30 seconds...
timeout /t 30 /nobreak >nul
