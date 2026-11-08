@echo off
rem ============================================================================
rem Hardware Price Comparison System - Start Project (start_project.bat)
rem ============================================================================

chcp 65001 > nul
cd /d "%~dp0"

echo ============================================================
echo   [Start Project] Hardware Price Comparison System
echo ============================================================
echo Project Path : %~dp0
echo Hot Reload   : ENABLED - Auto reloads web UI when code is saved
echo ------------------------------------------------------------

set PYTHONDONTWRITEBYTECODE=1

rem 自動略過 Streamlit 首次執行之 Email 訂閱提示
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit" >nul 2>&1
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
    echo [general]> "%USERPROFILE%\.streamlit\credentials.toml"
    echo email = "">> "%USERPROFILE%\.streamlit\credentials.toml"
)

rem 自動偵測正確安裝環境 (優先虛擬環境與 Miniforge / Conda)
set "PYTHON_EXE="
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%~dp0venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\miniforge3\python.exe" set "PYTHON_EXE=%USERPROFILE%\miniforge3\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\miniconda3\python.exe" set "PYTHON_EXE=%USERPROFILE%\miniconda3\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\anaconda3\python.exe" set "PYTHON_EXE=%USERPROFILE%\anaconda3\python.exe"
if not defined PYTHON_EXE if exist "C:\Users\berto\miniforge3\python.exe" set "PYTHON_EXE=C:\Users\berto\miniforge3\python.exe"
if not defined PYTHON_EXE set "PYTHON_EXE=python"

echo Using Python : %PYTHON_EXE%

rem 驗證 streamlit 模組是否存在
"%PYTHON_EXE%" -c "import streamlit" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] 在選定的 Python 直譯器中找不到 streamlit 模組！
    echo 直譯器路徑: %PYTHON_EXE%
    echo 請確認是否已啟動 miniforge3 或執行 pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo [*] Launching Streamlit dashboard...
echo [*] Local URL: http://localhost:8501
echo [*] Press Ctrl+C to stop the server
echo.

"%PYTHON_EXE%" -m streamlit run app\main_app.py --server.runOnSave true --browser.gatherUsageStats false %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Server exited with code: %ERRORLEVEL%
    pause
)
