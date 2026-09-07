@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo The private Python environment is missing.
    echo Run setup.cmd first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m streamlit run "ui\app.py"
set "APP_EXIT=%ERRORLEVEL%"

if not "%APP_EXIT%"=="0" (
    echo.
    echo The simulator stopped with an error.
    pause
)

exit /b %APP_EXIT%
