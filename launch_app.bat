@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

set "APP_NAME=Anveo Phone VoiceBot"
set "CONFIG_FILE=.local\phase0-launcher.env"
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
if not defined VOICEBOT_PORT set "VOICEBOT_PORT=%DEFAULT_PORT%"

:menu
cls
echo %APP_NAME%
echo.
echo Current settings:
echo   AnveoPhone: %ANVEOPHONE_DIR%
echo   Python:     %VOICEBOT_PYTHON%
echo   Port:       %VOICEBOT_PORT%
echo.
echo (A) Run live Phase 0 call test
echo (B) Run logged test bundle
echo (C) Run diagnostics only
echo (D) Start PCM server only
echo (E) Start AnveoPhone host only
echo (F) Open QuickStart/User Guide
echo (G) Settings
echo (H) Run Phase 1 response test
echo (Q) Quit
echo.
choice /c ABCDEFGHQ /n /m "Choose: "
set "MENU_CHOICE=%ERRORLEVEL%"
if "%MENU_CHOICE%"=="1" goto live_test
if "%MENU_CHOICE%"=="2" goto logged_tests
if "%MENU_CHOICE%"=="3" goto diagnostics
if "%MENU_CHOICE%"=="4" goto server_only
if "%MENU_CHOICE%"=="5" goto host_only
if "%MENU_CHOICE%"=="6" goto docs
if "%MENU_CHOICE%"=="7" goto settings
if "%MENU_CHOICE%"=="8" goto phase1_test
if "%MENU_CHOICE%"=="9" goto end
goto menu

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
if defined VOICEBOT_PIPER_MODEL (
    if defined VOICEBOT_PIPER_CONFIG (
        start "VoiceBot Phase 1 AI PCM" cmd /k ""%VOICEBOT_PYTHON%" -m voicebot serve-ai --port %VOICEBOT_PORT% --piper-model "%VOICEBOT_PIPER_MODEL%" --piper-config "%VOICEBOT_PIPER_CONFIG%""
    ) else (
        start "VoiceBot Phase 1 AI PCM" cmd /k ""%VOICEBOT_PYTHON%" -m voicebot serve-ai --port %VOICEBOT_PORT% --piper-model "%VOICEBOT_PIPER_MODEL%""
    )
) else (
    start "VoiceBot Phase 1 AI PCM" cmd /k ""%VOICEBOT_PYTHON%" -m voicebot serve-ai --port %VOICEBOT_PORT%"
)
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
call :save_config
echo.
echo Settings saved to %CONFIG_FILE%.
pause
goto menu

:end
endlocal
exit /b 0
