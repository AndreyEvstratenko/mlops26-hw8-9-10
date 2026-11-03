@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" launch_api.py %*
set "LAB_EXIT=%ERRORLEVEL%"
if not "%LAB_EXIT%"=="0" pause
exit /b %LAB_EXIT%
