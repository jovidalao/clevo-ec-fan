@echo off
net session >nul 2>&1
if not %errorlevel%==0 (
    powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
cd /d "%~dp0"
start "" pythonw.exe "%~dp0clevo_fan_gui.py"
