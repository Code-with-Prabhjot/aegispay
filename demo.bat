@echo off
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\run_live_demo.py %*
) else if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" scripts\run_live_demo.py %*
) else (
    python scripts\run_live_demo.py %*
)
