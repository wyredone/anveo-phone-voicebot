@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

set "APP_NAME=Anveo Phone VoiceBot"
set "CONFIG_FILE=.local\phase0-launcher.env"
set "DEFAULT_ANVEOPHONE_DIR=E:\anveophone"
set "DEFAULT_VOICE_ENGINE_DIR=E:\Voice-Engine-main"
set "DEFAULT_PORT=8765"
set "VOICEBOT_PORT=%DEFAULT_PORT%"

if exist "%CONFIG_FILE%" call "%CONFIG_FILE%"
if "%~1" neq "" set "ANVEOPHONE_DIR=%~1"
if "%~2" neq "" set "VOICEBOT_PYTHON=%~2"

if not defined VOICEBOT_PYTHON (
    if exist "C:\msys64\ucrt64\bin\python3.exe" (
        set "VOICEBOT_PYTHON=C:\msys64\ucrt64\bin\python3.exe"
    ) else (
        set "VOICEBOT_PYTHON=python"
    )
)

if exist "C:\msys64\ucrt64\bin\python3.exe" set "PATH=C:\msys64\ucrt64\bin;%PATH%"
if not defined ANVEOPHONE_DIR set "ANVEOPHONE_DIR=%DEFAULT_ANVEOPHONE_DIR%"
if not defined VOICEBOT_ENGINE_DIR set "VOICEBOT_ENGINE_DIR=%DEFAULT_VOICE_ENGINE_DIR%"
if not defined VOICEBOT_ENGINE_CONFIG set "VOICEBOT_ENGINE_CONFIG=%VOICEBOT_ENGINE_DIR%\config.json"
if not defined VOICEBOT_ENGINE_TIMEOUT set "VOICEBOT_ENGINE_TIMEOUT=20"
if not defined VOICEBOT_PORT set "VOICEBOT_PORT=%DEFAULT_PORT%"

:menu
cls
echo %APP_NAME%
echo.
echo Current settings:
echo   AnveoPhone: %ANVEOPHONE_DIR%
echo   Python:     %VOICEBOT_PYTHON%
echo   Port:       %VOICEBOT_PORT%
echo   VoiceEngine:%VOICEBOT_ENGINE_DIR%
echo   VE Config:  %VOICEBOT_ENGINE_CONFIG%
echo   VE Persona: %VOICEBOT_ENGINE_PERSONA%
echo.
echo (A) Start Voice-Engine call test
echo (B) Start local appointment fallback
echo (C) Run diagnostics
echo (D) Settings
echo (E) Open QuickStart/User Guide
echo (X) Advanced tools
echo (Q) Quit
echo.
choice /c ABCDEXQ /n /m "Choose: "
set "MENU_CHOICE=%ERRORLEVEL%"
if "%MENU_CHOICE%"=="1" goto engine_test
if "%MENU_CHOICE%"=="2" goto phase3_test
if "%MENU_CHOICE%"=="3" goto diagnostics
if "%MENU_CHOICE%"=="4" goto settings
if "%MENU_CHOICE%"=="5" goto docs
if "%MENU_CHOICE%"=="6" goto advanced_menu
if "%MENU_CHOICE%"=="7" goto end
goto menu

:advanced_menu
cls
echo %APP_NAME% - Advanced Tools
echo.
echo These are older phase/debug tools. Use the main menu for normal testing.
echo.
echo (1) Phase 0 tone/capture bridge test
echo (2) Phase 1 local speech response test
echo (3) PCM server only
echo (4) AnveoPhone host only
echo (5) Logged test bundle
echo (B) Back
echo (Q) Quit
echo.
choice /c 12345BQ /n /m "Choose: "
set "ADV_CHOICE=%ERRORLEVEL%"
if "%ADV_CHOICE%"=="1" goto live_test
if "%ADV_CHOICE%"=="2" goto phase1_test
if "%ADV_CHOICE%"=="3" goto server_only
if "%ADV_CHOICE%"=="4" goto host_only
if "%ADV_CHOICE%"=="5" goto logged_tests
if "%ADV_CHOICE%"=="6" goto menu
if "%ADV_CHOICE%"=="7" goto end
goto advanced_menu

:require_anveophone
if defined ANVEOPHONE_DIR exit /b 0
echo.
echo Enter the AnveoPhone source folder.
echo Example: F:\anveophone
set /p "ANVEOPHONE_DIR=AnveoPhone folder: "
if not defined ANVEOPHONE_DIR (
    echo AnveoPhone folder is required for this option.
    pause
    exit /b 1
)
call :save_config
exit /b 0

:save_config
if not exist ".local" mkdir ".local"
(
    echo @echo off
    echo set "ANVEOPHONE_DIR=%ANVEOPHONE_DIR%"
    echo set "VOICEBOT_PYTHON=%VOICEBOT_PYTHON%"
    echo set "VOICEBOT_PORT=%VOICEBOT_PORT%"
    echo set "VOICEBOT_ENGINE_DIR=%VOICEBOT_ENGINE_DIR%"
    echo set "VOICEBOT_ENGINE_CONFIG=%VOICEBOT_ENGINE_CONFIG%"
    echo set "VOICEBOT_ENGINE_PERSONA=%VOICEBOT_ENGINE_PERSONA%"
    echo set "VOICEBOT_ENGINE_URL=%VOICEBOT_ENGINE_URL%"
    echo set "VOICEBOT_ENGINE_TIMEOUT=%VOICEBOT_ENGINE_TIMEOUT%"
    echo set "VOICEBOT_SAPI_VOICE=%VOICEBOT_SAPI_VOICE%"
    echo set "VOICEBOT_PIPER_MODEL=%VOICEBOT_PIPER_MODEL%"
    echo set "VOICEBOT_PIPER_CONFIG=%VOICEBOT_PIPER_CONFIG%"
) > "%CONFIG_FILE%"
exit /b 0

:doctor
"%VOICEBOT_PYTHON%" -m voicebot doctor --anveophone "%ANVEOPHONE_DIR%" --report "artifacts\doctor.json"
exit /b %ERRORLEVEL%

:live_test
call :require_anveophone
if errorlevel 1 goto menu
echo.
echo Close the normal AnveoPhone app before continuing.
echo Disable AnveoPhone Auto-answer before this test.
echo.
pause
call :doctor
if errorlevel 1 (
    echo.
    echo Diagnostic failed. See artifacts\doctor.json.
    pause
    goto menu
)
echo.
echo Starting PCM server window on port %VOICEBOT_PORT%...
start "VoiceBot Phase 0 PCM" cmd /k ""%VOICEBOT_PYTHON%" -m voicebot serve --port %VOICEBOT_PORT%"
echo.
echo Starting AnveoPhone host. Answer one incoming test call manually.
echo F8 arms another call. F9 restores manual audio.
echo.
"%VOICEBOT_PYTHON%" -m voicebot host --anveophone "%ANVEOPHONE_DIR%" --arm-next-call --port %VOICEBOT_PORT%
echo.
pause
goto menu

:logged_tests
if defined ANVEOPHONE_DIR (
    "%VOICEBOT_PYTHON%" run_phase0_tests.py --anveophone "%ANVEOPHONE_DIR%"
) else (
    "%VOICEBOT_PYTHON%" run_phase0_tests.py --quick
)
echo.
echo Send the ZIP from artifacts\test-runs.
pause
goto menu

:diagnostics
call :require_anveophone
if errorlevel 1 goto menu
call :doctor
echo.
pause
goto menu

:server_only
echo.
echo Starting PCM server only on port %VOICEBOT_PORT%.
echo Close this server window when finished.
echo.
"%VOICEBOT_PYTHON%" -m voicebot serve --port %VOICEBOT_PORT%
echo.
pause
goto menu

:host_only
call :require_anveophone
if errorlevel 1 goto menu
echo.
echo Starting host only on port %VOICEBOT_PORT%.
echo Make sure the PCM server is already running on the same port.
echo.
"%VOICEBOT_PYTHON%" -m voicebot host --anveophone "%ANVEOPHONE_DIR%" --arm-next-call --port %VOICEBOT_PORT%
echo.
pause
goto menu

:phase1_test
call :require_anveophone
if errorlevel 1 goto menu
echo.
echo Close the normal AnveoPhone app before continuing.
echo Disable AnveoPhone Auto-answer before this test.
echo Speak after answering, then pause to trigger the generated response.
echo.
pause
call :doctor
if errorlevel 1 (
    echo.
    echo Diagnostic failed. See artifacts\doctor.json.
    pause
    goto menu
)
echo.
echo Starting Phase 1 response server window on port %VOICEBOT_PORT%...
if not "%VOICEBOT_SAPI_VOICE%"=="" (
    set "VOICEBOT_SAPI_ARGS=--sapi-voice "%VOICEBOT_SAPI_VOICE%""
) else (
    set "VOICEBOT_SAPI_ARGS="
)
if not "%VOICEBOT_PIPER_MODEL%"=="" (
    if not "%VOICEBOT_PIPER_CONFIG%"=="" (
        start "VoiceBot Phase 1 AI PCM" cmd /k ""%VOICEBOT_PYTHON%" -m voicebot serve-ai --port %VOICEBOT_PORT% --response-mode rules --tts-backend auto --stt-backend auto --piper-model "%VOICEBOT_PIPER_MODEL%" --piper-config "%VOICEBOT_PIPER_CONFIG%" %VOICEBOT_SAPI_ARGS%"
    ) else (
        start "VoiceBot Phase 1 AI PCM" cmd /k ""%VOICEBOT_PYTHON%" -m voicebot serve-ai --port %VOICEBOT_PORT% --response-mode rules --tts-backend auto --stt-backend auto --piper-model "%VOICEBOT_PIPER_MODEL%" %VOICEBOT_SAPI_ARGS%"
    )
) else (
    start "VoiceBot Phase 1 AI PCM" cmd /k ""%VOICEBOT_PYTHON%" -m voicebot serve-ai --port %VOICEBOT_PORT% --response-mode rules --tts-backend auto --stt-backend auto %VOICEBOT_SAPI_ARGS%"
)
echo.
echo Starting AnveoPhone host. Answer one incoming test call manually.
echo F8 arms another call. F9 restores manual audio.
echo.
"%VOICEBOT_PYTHON%" -m voicebot host --anveophone "%ANVEOPHONE_DIR%" --arm-next-call --port %VOICEBOT_PORT%
echo.
pause
goto menu

:phase3_test
call :require_anveophone
if errorlevel 1 goto menu
echo.
echo Close the normal AnveoPhone app before continuing.
echo Disable AnveoPhone Auto-answer before this test.
echo Say hello, ask for a dental appointment, then answer the follow-up questions.
echo.
pause
call :doctor
if errorlevel 1 (
    echo.
    echo Diagnostic failed. See artifacts\doctor.json.
    pause
    goto menu
)
echo.
echo Starting Phase 3 appointment server window on port %VOICEBOT_PORT%...
if not "%VOICEBOT_SAPI_VOICE%"=="" (
    set "VOICEBOT_SAPI_ARGS=--sapi-voice "%VOICEBOT_SAPI_VOICE%""
) else (
    set "VOICEBOT_SAPI_ARGS="
)
if not "%VOICEBOT_PIPER_MODEL%"=="" (
    if not "%VOICEBOT_PIPER_CONFIG%"=="" (
        start "VoiceBot Phase 3 Appointment PCM" cmd /k ""%VOICEBOT_PYTHON%" -m voicebot serve-ai --port %VOICEBOT_PORT% --response-mode appointment --tts-backend auto --stt-backend auto --piper-model "%VOICEBOT_PIPER_MODEL%" --piper-config "%VOICEBOT_PIPER_CONFIG%" %VOICEBOT_SAPI_ARGS%"
    ) else (
        start "VoiceBot Phase 3 Appointment PCM" cmd /k ""%VOICEBOT_PYTHON%" -m voicebot serve-ai --port %VOICEBOT_PORT% --response-mode appointment --tts-backend auto --stt-backend auto --piper-model "%VOICEBOT_PIPER_MODEL%" %VOICEBOT_SAPI_ARGS%"
    )
) else (
    start "VoiceBot Phase 3 Appointment PCM" cmd /k ""%VOICEBOT_PYTHON%" -m voicebot serve-ai --port %VOICEBOT_PORT% --response-mode appointment --tts-backend auto --stt-backend auto %VOICEBOT_SAPI_ARGS%"
)
echo.
echo Starting AnveoPhone host. Answer one incoming test call manually.
echo F8 arms another call. F9 restores manual audio.
echo.
"%VOICEBOT_PYTHON%" -m voicebot host --anveophone "%ANVEOPHONE_DIR%" --arm-next-call --port %VOICEBOT_PORT%
echo.
pause
goto menu

:engine_test
call :require_anveophone
if errorlevel 1 goto menu
if "%VOICEBOT_ENGINE_URL%"=="" if "%VOICEBOT_ENGINE_DIR%"=="" (
    echo.
    echo Set VOICEBOT_ENGINE_DIR for local Voice-Engine or VOICEBOT_ENGINE_URL for HTTP mode.
    echo Optional: set VOICEBOT_ENGINE_CONFIG, VOICEBOT_ENGINE_API_KEY, and VOICEBOT_ENGINE_PERSONA.
    echo.
    pause
    goto menu
)
echo.
echo Close the normal AnveoPhone app before continuing.
echo Disable AnveoPhone Auto-answer before this test.
echo Voice-Engine dir: %VOICEBOT_ENGINE_DIR%
echo Voice-Engine config: %VOICEBOT_ENGINE_CONFIG%
echo Voice-Engine URL: %VOICEBOT_ENGINE_URL%
echo Voice-Engine persona: %VOICEBOT_ENGINE_PERSONA%
echo Voice-Engine timeout: %VOICEBOT_ENGINE_TIMEOUT%
echo.
pause
call :doctor
if errorlevel 1 (
    echo.
    echo Diagnostic failed. See artifacts\doctor.json.
    pause
    goto menu
)
echo.
echo Starting Voice-Engine API server bridge window on port %VOICEBOT_PORT%...
start "VoiceBot Voice-Engine API PCM" cmd /k ""%VOICEBOT_PYTHON%" -m voicebot serve-ai --port %VOICEBOT_PORT% --response-mode engine --tts-backend auto --stt-backend off --voice-engine-url "%VOICEBOT_ENGINE_URL%" --voice-engine-dir "%VOICEBOT_ENGINE_DIR%" --voice-engine-config "%VOICEBOT_ENGINE_CONFIG%" --voice-engine-persona "%VOICEBOT_ENGINE_PERSONA%" --voice-engine-timeout %VOICEBOT_ENGINE_TIMEOUT%"
echo.
echo Starting AnveoPhone host. Answer one incoming test call manually.
echo F8 arms another call. F9 restores manual audio.
echo.
"%VOICEBOT_PYTHON%" -m voicebot host --anveophone "%ANVEOPHONE_DIR%" --arm-next-call --port %VOICEBOT_PORT%
echo.
pause
goto menu

:docs
if exist "docs\QUICKSTART.html" start "" "docs\QUICKSTART.html"
if exist "docs\USER_GUIDE.html" start "" "docs\USER_GUIDE.html"
goto menu

:settings
cls
echo Settings
echo.
echo Current AnveoPhone folder: %ANVEOPHONE_DIR%
set /p "NEW_ANVEO=New AnveoPhone folder, or press Enter to keep current: "
if defined NEW_ANVEO set "ANVEOPHONE_DIR=%NEW_ANVEO%"
echo.
echo Current Python: %VOICEBOT_PYTHON%
set /p "NEW_PY=New Python executable, or press Enter to keep current: "
if defined NEW_PY set "VOICEBOT_PYTHON=%NEW_PY%"
echo.
echo Current port: %VOICEBOT_PORT%
set /p "NEW_PORT=New port, or press Enter to keep current: "
if defined NEW_PORT set "VOICEBOT_PORT=%NEW_PORT%"
echo.
echo Current Voice-Engine folder: %VOICEBOT_ENGINE_DIR%
set /p "NEW_ENGINE_DIR=New Voice-Engine folder, or press Enter to keep current: "
if defined NEW_ENGINE_DIR (
    set "VOICEBOT_ENGINE_DIR=%NEW_ENGINE_DIR%"
    if not defined VOICEBOT_ENGINE_CONFIG set "VOICEBOT_ENGINE_CONFIG=%NEW_ENGINE_DIR%\config.json"
)
echo.
echo Current Voice-Engine config: %VOICEBOT_ENGINE_CONFIG%
set /p "NEW_ENGINE_CONFIG=New Voice-Engine config, or press Enter to keep current: "
if defined NEW_ENGINE_CONFIG set "VOICEBOT_ENGINE_CONFIG=%NEW_ENGINE_CONFIG%"
echo.
echo Current Voice-Engine persona: %VOICEBOT_ENGINE_PERSONA%
set /p "NEW_ENGINE_PERSONA=New persona name or ID, or press Enter to keep current: "
if defined NEW_ENGINE_PERSONA set "VOICEBOT_ENGINE_PERSONA=%NEW_ENGINE_PERSONA%"
echo.
echo Current Voice-Engine HTTP URL: %VOICEBOT_ENGINE_URL%
set /p "NEW_ENGINE_URL=New HTTP URL, or press Enter to keep current/blank: "
if defined NEW_ENGINE_URL set "VOICEBOT_ENGINE_URL=%NEW_ENGINE_URL%"
echo.
echo Current Voice-Engine timeout: %VOICEBOT_ENGINE_TIMEOUT%
set /p "NEW_ENGINE_TIMEOUT=New timeout seconds, or press Enter to keep current: "
if defined NEW_ENGINE_TIMEOUT set "VOICEBOT_ENGINE_TIMEOUT=%NEW_ENGINE_TIMEOUT%"
echo.
echo Current Windows SAPI voice: %VOICEBOT_SAPI_VOICE%
set /p "NEW_SAPI=New SAPI voice, or press Enter to keep current/auto: "
if defined NEW_SAPI set "VOICEBOT_SAPI_VOICE=%NEW_SAPI%"
echo.
echo Current Piper model: %VOICEBOT_PIPER_MODEL%
set /p "NEW_PIPER_MODEL=New Piper model path, or press Enter to keep current/blank: "
if defined NEW_PIPER_MODEL set "VOICEBOT_PIPER_MODEL=%NEW_PIPER_MODEL%"
echo.
echo Current Piper config: %VOICEBOT_PIPER_CONFIG%
set /p "NEW_PIPER_CONFIG=New Piper config path, or press Enter to keep current/blank: "
if defined NEW_PIPER_CONFIG set "VOICEBOT_PIPER_CONFIG=%NEW_PIPER_CONFIG%"
call :save_config
echo.
echo Settings saved to %CONFIG_FILE%.
pause
goto menu

:end
endlocal
exit /b 0
