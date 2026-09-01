@echo off
setlocal
cd /d "%~dp0"
echo ===============================================
echo EEG Horizon Run - Install Python Dependencies
echo ===============================================
echo The NO_PROXY setting avoids the old pip / local HTTPS proxy bug.
set "NO_PROXY=*"
set "no_proxy=*"
python -m pip install --no-cache-dir --upgrade pip
if errorlevel 1 goto failed
python -m pip install --no-cache-dir -r requirements.txt
if errorlevel 1 goto failed
echo.
echo Installation completed successfully.
python -c "import numpy, pylsl, websockets; print('numpy', numpy.__version__); print('pylsl', getattr(pylsl, '__version__', 'ok')); print('websockets', websockets.__version__)"
pause
exit /b 0
:failed
echo.
echo Installation failed. Check your network connection and run this file again.
pause
exit /b 1
