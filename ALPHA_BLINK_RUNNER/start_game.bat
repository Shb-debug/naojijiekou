@echo off
setlocal
cd /d "%~dp0"
echo Starting EEG Horizon Run at http://localhost:8000/
python -m http.server 8000
pause
