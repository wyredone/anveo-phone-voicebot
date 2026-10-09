@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
if "%~1"=="" (
    echo Usage: launch_phase0.bat "F:\anveophone" ["C:\path\to\python.exe"]
    echo Close the normal AnveoPhone app first. Disable its Auto-answer setting.
    pause
    exit /b 1
)
set "VOICEBOT_PYTHON=%~2"
if not defined VOICEBOT_PYTHON (
    if exist "C:\msys64\ucrt64\bin\python3.exe" (
        set "VOICEBOT_PYTHON=C:\msys64\ucrt64\bin\python3.exe"
    ) else (
        set "VOICEBOT_PYTHON=python"
    )
)
if exist "C:\msys64\ucrt64\bin\python3.exe" set "PATH=C:\msys64\ucrt64\bin;%PATH%"
"%VOICEBOT_PYTHON%" -m voicebot doctor --anveophone "%~1"
if errorlevel 1 (
    echo Diagnostic failed. See artifacts\doctor.json and use AnveoPhone's working Python.
    pause
    exit /b 1
)
rem Separate process owns PCM capture and the test response; host owns SIP.
start "VoiceBot Phase 0 PCM" cmd /k ""%VOICEBOT_PYTHON%" -m voicebot serve"
echo Answer one incoming test call manually. F8 arms another; F9 restores manual audio.
echo Caller should hear a one-second tone. Close the server window after testing.
"%VOICEBOT_PYTHON%" -m voicebot host --anveophone "%~1" --arm-next-call
set "VOICEBOT_EXIT=%ERRORLEVEL%"
if not "%VOICEBOT_EXIT%"=="0" pause
exit /b %VOICEBOT_EXIT%
