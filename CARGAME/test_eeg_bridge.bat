@echo off
setlocal
cd /d "%~dp0"
echo Starting a simulated EEG bridge on ws://127.0.0.1:8765
python brain_bridge.py --config ssvep_config.json --simulate
pause
