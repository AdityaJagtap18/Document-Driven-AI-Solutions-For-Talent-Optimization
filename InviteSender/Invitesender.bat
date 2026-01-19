@echo off
title 🎯 Cognitive Document Platform - Invite Scheduler
echo ================================================
echo Starting Interview Invite Scheduler Script...
echo ================================================

cd /d "G:\BEProjectResumeParser\InviteSender"

:: Optional: Activate virtual environment if used
:: call ..\venv\Scripts\activate

:: Run the scheduler.py script
python scheduler.py

echo.
echo ================================================
echo Invite Scheduler finished or exited.
pause
