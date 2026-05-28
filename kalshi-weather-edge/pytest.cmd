@echo off
setlocal
set "REPO_ROOT=%~dp0"
set "VENV_PYTHON=%REPO_ROOT%.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
  >&2 echo Missing repo-local .venv Python: %VENV_PYTHON%
  >&2 echo Run .\scripts\setup-dev.ps1 from the kalshi-weather-edge directory.
  exit /b 1
)

"%VENV_PYTHON%" -m pytest %*
exit /b %ERRORLEVEL%
