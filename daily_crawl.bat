@echo off
rem ============================================================================
rem Hardware Price Comparison System - Daily Crawl Scheduled Task (daily_crawl.bat)
rem ============================================================================

chcp 65001 > nul
cd /d "%~dp0"

set PYTHONDONTWRITEBYTECODE=1

rem 自動偵測正確安裝環境 (優先虛擬環境與 Miniforge / Conda)
set "PYTHON_EXE="
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%~dp0venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\miniforge3\python.exe" set "PYTHON_EXE=%USERPROFILE%\miniforge3\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\miniconda3\python.exe" set "PYTHON_EXE=%USERPROFILE%\miniconda3\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\anaconda3\python.exe" set "PYTHON_EXE=%USERPROFILE%\anaconda3\python.exe"
if not defined PYTHON_EXE if exist "C:\Users\berto\miniforge3\python.exe" set "PYTHON_EXE=C:\Users\berto\miniforge3\python.exe"
if not defined PYTHON_EXE set "PYTHON_EXE=python"

echo [Daily Crawl] Using Python: %PYTHON_EXE%
echo [Daily Crawl] Starting daily hardware price sync...

"%PYTHON_EXE%" scripts\daily_sync.py %*
set EXIT_CODE=%ERRORLEVEL%

if %EXIT_CODE% EQU 0 (
    echo [Daily Crawl] Sync completed successfully. Exiting.
) else (
    echo [Daily Crawl] Sync encountered error. Exit code: %EXIT_CODE%
)

exit /b %EXIT_CODE%
