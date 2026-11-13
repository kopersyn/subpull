@echo off
rem SubPull - creates .venv on first start, then launches the app
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    py -3 -m venv .venv || python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install -e .
)
start "" ".venv\Scripts\pythonw.exe" -m subpull
