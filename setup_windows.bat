@echo off
rem One-click setup for Windows: creates .venv and installs dependencies.
cd /d %~dp0

set PY=python
where py >nul 2>nul && set PY=py -3

echo [1/3] Creating Python virtual environment (.venv) ...
%PY% -m venv .venv
if errorlevel 1 goto :nopython

echo [2/3] Installing packages ...
.venv\Scripts\python.exe -m pip install --upgrade pip >nul
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo [3/3] Installing browser for Playwright (used when Google Chrome is not found) ...
.venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 echo   (skipped - Google Chrome will be used instead)

echo.
echo Setup complete.
echo Next: double-click start_windows.bat, then open http://127.0.0.1:5000
pause
exit /b 0

:nopython
echo.
echo Python was not found. Install Python 3.10 or later from https://www.python.org/downloads/
echo (check "Add python.exe to PATH" during installation), then run this file again.
pause
exit /b 1

:failed
echo.
echo Package installation failed. Check your internet connection and run this file again.
pause
exit /b 1
