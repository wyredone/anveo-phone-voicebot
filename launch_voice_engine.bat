@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
set "ANVEOPHONE_DIR=%~1"
if not defined ANVEOPHONE_DIR set "ANVEOPHONE_DIR=E:\anveophone"
set "VOICEBOT_PYTHON=%~2"
if not defined VOICEBOT_PYTHON (
    if exist "C:\msys64\ucrt64\bin\python3.exe" (
        set "VOICEBOT_PYTHON=C:\msys64\ucrt64\bin\python3.exe"
    ) else (
        set "VOICEBOT_PYTHON=python"
    )
)
if "%VOICEBOT_ENGINE_URL%"=="" if "%VOICEBOT_ENGINE_DIR%"=="" (
    echo Set VOICEBOT_ENGINE_DIR for local Voice-Engine or VOICEBOT_ENGINE_URL for HTTP mode.
    echo Optional: set VOICEBOT_ENGINE_CONFIG, VOICEBOT_ENGINE_API_KEY, and VOICEBOT_ENGINE_PERSONA.
    pause
    exit /b 1
)
if exist "C:\msys64\ucrt64\bin\python3.exe" set "PATH=C:\msys64\ucrt64\bin;%PATH%"
"%VOICEBOT_PYTHON%" -m voicebot doctor --anveophone "%ANVEOPHONE_DIR%"
if errorlevel 1 (
    echo Diagnostic failed. See artifacts\doctor.json and use AnveoPhone's working Python.
    pause
    exit /b 1
)
start "VoiceBot Voice-Engine API PCM" cmd /k ""%VOICEBOT_PYTHON%" -m voicebot serve-ai --response-mode engine --tts-backend auto --stt-backend off --voice-engine-url "%VOICEBOT_ENGINE_URL%" --voice-engine-dir "%VOICEBOT_ENGINE_DIR%" --voice-engine-config "%VOICEBOT_ENGINE_CONFIG%" --voice-engine-persona "%VOICEBOT_ENGINE_PERSONA%""
echo Answer one incoming test call manually. Voice-Engine owns STT and persona/AI; response audio uses engine audio when available or local TTS.
"%VOICEBOT_PYTHON%" -m voicebot host --anveophone "%ANVEOPHONE_DIR%" --arm-next-call
set "VOICEBOT_EXIT=%ERRORLEVEL%"
if not "%VOICEBOT_EXIT%"=="0" pause
exit /b %VOICEBOT_EXIT%
