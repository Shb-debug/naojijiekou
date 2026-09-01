@echo off
setlocal
cd /d "%~dp0"
echo ===============================================
echo EEG Horizon Run - OpenBCI / OpenViBE Bridge
echo ===============================================
echo Start OpenBCI GUI first, then enable LSL output.
echo WebSocket page link: ws://127.0.0.1:8765
echo OpenViBE command link: http://127.0.0.1:8766
python brain_bridge.py --config ssvep_config.json
pause
