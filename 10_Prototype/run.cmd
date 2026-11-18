@echo off
REM Fleet Planning Console — start the prototype (Windows).
REM Opens http://127.0.0.1:8051 in the default browser.

setlocal
cd /d "%~dp0"

set "PYTHON_EXE="
where python >nul 2>nul
if not errorlevel 1 set "PYTHON_EXE=python"
if not defined PYTHON_EXE if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYTHON_EXE (
  echo Python 3.13 was not found on PATH or in the standard per-user location.
  pause
  exit /b 1
)

set PYTHONIOENCODING=utf-8
"%PYTHON_EXE%" backend\server.py %*

endlocal
