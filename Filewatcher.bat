@echo off
:: ==========================================================
::  Cognitive Document Intelligence Platform - Resume Automation
::  Batch Script to Run Resume Watcher + Question Generator
:: ==========================================================

echo.
echo ----------------------------------------------------------
echo  Starting Resume File Watcher + Auto Parser Service...
echo ----------------------------------------------------------

cd /d "G:\BEProjectResumeParser"

:: Start Resume Watcher (Parser)
start cmd /k python file_watcher.py

:: Start Question Generator Watcher
start cmd /k python question_watcher.py

echo.
echo ----------------------------------------------------------
echo  All services started in background terminals.
echo  Close this window to terminate all.
echo ----------------------------------------------------------
pause
