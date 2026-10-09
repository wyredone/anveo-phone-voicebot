@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
set "VOICEBOT_PYTHON=%~2"
if not defined VOICEBOT_PYTHON (
    if exist "C:\msys64\ucrt64\bin\python3.exe" (
        set "VOICEBOT_PYTHON=C:\msys64\ucrt64\bin\python3.exe"
    ) else (
        set "VOICEBOT_PYTHON=python"
    )
)
if "%~1"=="" (
    echo Usage: run_phase0_tests.bat "F:\anveophone" ["C:\path\to\python.exe"]
    echo.
    echo Running quick portable tests without AnveoPhone path...
    "%VOICEBOT_PYTHON%" run_phase0_tests.py --quick
) else (
    "%VOICEBOT_PYTHON%" run_phase0_tests.py --anveophone "%~1"
)
set "VOICEBOT_EXIT=%ERRORLEVEL%"
echo.
echo Done. Send the ZIP from artifacts\test-runs.
pause
exit /b %VOICEBOT_EXIT%
