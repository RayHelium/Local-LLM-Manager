@echo off
setlocal
set "SCRIPT_DIR=%~dp0"

rem ============================================================
rem  Optional: set your Python path manually if auto-detect fails
rem  Example: set "PYTHON_EXE=C:\Python314\python.exe"
rem ============================================================

if defined PYTHON_EXE (
    "%PYTHON_EXE%" "%SCRIPT_DIR%local_llm_gui.py"
    goto :eof
)

rem Auto-detect Python: prefer the py launcher, then python on PATH
set "PY_CMD="
where py >nul 2>nul && set "PY_CMD=py -3"
if not defined PY_CMD where python >nul 2>nul && set "PY_CMD=python"

if not defined PY_CMD (
    echo [ERROR] Python not found.
    echo Install Python and add it to PATH, or set PYTHON_EXE to your python.exe path, then re-run.
    pause
    goto :eof
)

%PY_CMD% "%SCRIPT_DIR%local_llm_gui.py"
