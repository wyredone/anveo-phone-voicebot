@echo off
setlocal
cd /d "%~dp0"
python -m unittest discover -s tests -v
set "VOICEBOT_EXIT=%ERRORLEVEL%"
pause
exit /b %VOICEBOT_EXIT%
