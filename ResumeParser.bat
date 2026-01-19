@echo off
:: ==========================================================
::  Cognitive Document Intelligence Platform - Resume Parser
::  Batch Execution Script
:: ==========================================================

echo.
echo ----------------------------------------------------------
echo  Cognitive Document Intelligence Platform
echo  Resume Parser - Batch Execution Started
echo ----------------------------------------------------------
echo  Step 1: Initializing Resume Parsing Process...
echo.

:: Navigate to the working directory
cd /d "C:\Users\adity\BE Project\Stage4"

:: Run the Python script
echo  Step 2: Executing ResumeParser.py...
python ResumeParser.py

:: Post-processing
echo.
echo ----------------------------------------------------------
echo  Resume Parsing Process Completed Successfully
echo  ResumeParser.py execution finished
echo ----------------------------------------------------------
echo  Waiting for 30 seconds before exit...
timeout /t 30 /nobreak >nul
