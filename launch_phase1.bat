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
if exist "C:\msys64\ucrt64\bin\python3.exe" set "PATH=C:\msys64\ucrt64\bin;%PATH%"
"%VOICEBOT_PYTHON%" -m voicebot doctor --anveophone "%ANVEOPHONE_DIR%"
if errorlevel 1 (
    echo Diagnostic failed. See artifacts\doctor.json and use AnveoPhone's working Python.
    pause
    exit /b 1
)
set "VOICEBOT_TTS_BACKEND=auto"
if not "%VOICEBOT_SAPI_VOICE%"=="" (
    set "VOICEBOT_SAPI_ARGS=--sapi-voice "%VOICEBOT_SAPI_VOICE%""
) else (
    set "VOICEBOT_SAPI_ARGS="
)
if not "%VOICEBOT_PIPER_MODEL%"=="" (
    if not "%VOICEBOT_PIPER_CONFIG%"=="" (
        start "VoiceBot Phase 1 AI PCM" cmd /k ""%VOICEBOT_PYTHON%" -m voicebot serve-ai --response-mode rules --tts-backend %VOICEBOT_TTS_BACKEND% --stt-backend auto --piper-model "%VOICEBOT_PIPER_MODEL%" --piper-config "%VOICEBOT_PIPER_CONFIG%" %VOICEBOT_SAPI_ARGS%"
    ) else (
        start "VoiceBot Phase 1 AI PCM" cmd /k ""%VOICEBOT_PYTHON%" -m voicebot serve-ai --response-mode rules --tts-backend %VOICEBOT_TTS_BACKEND% --stt-backend auto --piper-model "%VOICEBOT_PIPER_MODEL%" %VOICEBOT_SAPI_ARGS%"
    )
) else (
    start "VoiceBot Phase 1 AI PCM" cmd /k ""%VOICEBOT_PYTHON%" -m voicebot serve-ai --response-mode rules --tts-backend %VOICEBOT_TTS_BACKEND% --stt-backend auto %VOICEBOT_SAPI_ARGS%"
)
echo Answer one incoming test call manually. Speak, pause, and listen for the Phase 1 response.
"%VOICEBOT_PYTHON%" -m voicebot host --anveophone "%ANVEOPHONE_DIR%" --arm-next-call
set "VOICEBOT_EXIT=%ERRORLEVEL%"
if not "%VOICEBOT_EXIT%"=="0" pause
exit /b %VOICEBOT_EXIT%
