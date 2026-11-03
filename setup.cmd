@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "LAB_EXIT=%ERRORLEVEL%"
pause
exit /b %LAB_EXIT%
