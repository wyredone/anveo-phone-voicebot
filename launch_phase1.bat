@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
if "%~1"=="" (
    echo Usage: launch_phase1.bat "F:\anveophone" ["C:\path\to\python.exe"]
    echo Optional Piper model: set VOICEBOT_PIPER_MODEL=C:\path\voice.onnx
    echo Optional Piper config: set VOICEBOT_PIPER_CONFIG=C:\path\voice.onnx.json
    echo Optional Windows voice: set VOICEBOT_SAPI_VOICE=Microsoft Zira Desktop
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
"%VOICEBOT_PYTHON%" -m voicebot host --anveophone "%~1" --arm-next-call
set "VOICEBOT_EXIT=%ERRORLEVEL%"
if not "%VOICEBOT_EXIT%"=="0" pause
exit /b %VOICEBOT_EXIT%
