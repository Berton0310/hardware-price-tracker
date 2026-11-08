@echo off
chcp 65001 > nul
set "TASK_NAME=HardwarePriceDailySync"
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\daily_crawl.bat") do set "TARGET_BAT=%%~fI"

echo ============================================================
echo    電腦硬體價格比價系統 - Windows 每日定時排程管理工具
echo ============================================================
echo 排程名稱 : %TASK_NAME%
echo 目標檔案 : %TARGET_BAT%
echo 排定時間 : 每日 20:00 (執行完畢自動關閉退出)
echo ------------------------------------------------------------

if /i "%~1"=="install" goto do_install
if /i "%~1"=="status" goto do_status
if /i "%~1"=="uninstall" goto do_uninstall
if not "%~1"=="" goto usage

echo 請選擇操作項目：
echo   [1] 註冊 / 更新每日排程 (每日 20:00 自動執行)
echo   [2] 查詢排程當前狀態
echo   [3] 移除每日排程
echo   [4] 結束離開
echo.
set /p CHOICE="請輸入選項 [1-4]: "

if "%CHOICE%"=="1" goto do_install
if "%CHOICE%"=="2" goto do_status
if "%CHOICE%"=="3" goto do_uninstall
if "%CHOICE%"=="4" goto end
echo 無效選項，已取消操作。
goto end

:usage
echo 未知參數: %~1
echo 用法: setup_schedule.bat [install / status / uninstall]
goto end

:do_install
echo.
echo [*] 正在向 Windows 工作排程器註冊任務 [%TASK_NAME%]...
if not exist "%TARGET_BAT%" goto err_nobat
schtasks /create /tn "%TASK_NAME%" /tr "\"%TARGET_BAT%\"" /sc daily /st 20:00 /f
if %ERRORLEVEL% equ 0 goto install_ok
echo.
echo [ERROR] 註冊失敗，結束代碼: %ERRORLEVEL%。若為權限問題，請以系統管理員身分執行。
goto end

:install_ok
echo.
echo [OK] 註冊成功，排程已就緒，將於每日晚上 20:00 背景啟動，爬取結束後自動關閉。
goto end

:err_nobat
echo [ERROR] 找不到目標執行檔 %TARGET_BAT%
goto end

:do_status
echo.
echo [*] 正在查詢排程 [%TASK_NAME%] 狀態...
schtasks /query /tn "%TASK_NAME%" /fo LIST /v
if %ERRORLEVEL% equ 0 goto end
echo [INFO] 查無排程 [%TASK_NAME%] 或尚未註冊。
goto end

:do_uninstall
echo.
echo [*] 正在移除排程 [%TASK_NAME%]...
schtasks /delete /tn "%TASK_NAME%" /f
if %ERRORLEVEL% equ 0 goto uninstall_ok
echo [INFO] 移除失敗或排程不存在。
goto end

:uninstall_ok
echo [OK] 排程 [%TASK_NAME%] 已成功移除。
goto end

:end
echo.
