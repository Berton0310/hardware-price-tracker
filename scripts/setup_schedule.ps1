param (
    [string]$Action
)

$TaskName = "HardwarePriceDailySync"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$TargetBat = "$ProjectRoot\daily_crawl.bat"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   電腦硬體價格比價系統 - Windows 每日定時排程管理工具   " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "排程名稱 : $TaskName"
Write-Host "目標檔案 : $TargetBat"
Write-Host "排定時間 : 每日 20:00 (執行完畢自動關閉退出)"
Write-Host "------------------------------------------------------------"

function Do-Install {
    Write-Host "`n[*] 正在向 Windows 工作排程器註冊每日同步任務..." -ForegroundColor Yellow
    if (-not (Test-Path $TargetBat)) {
        Write-Host "[ERROR] 找不到目標批次檔: $TargetBat" -ForegroundColor Red
        return 1
    }
    $TaskRun = '"{0}"' -f $TargetBat
    schtasks.exe /Create /TN $TaskName /TR $TaskRun /SC DAILY /ST 20:00 /F
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n[OK] 註冊成功！排程已就緒，將於每日晚上 20:00 自動啟動並於執行完畢後關閉。" -ForegroundColor Green
        return 0
    } else {
        Write-Host "`n[ERROR] 註冊失敗。若提示存取拒絕，請嘗試以「系統管理員身分」執行此終端機。" -ForegroundColor Red
        return $LASTEXITCODE
    }
}

function Do-Status {
    Write-Host "`n[*] 正在查詢排程 [$TaskName] 之當前狀態..." -ForegroundColor Yellow
    schtasks.exe /Query /TN $TaskName /FO LIST /V
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`n[INFO] 查無排程 [$TaskName] 或尚未註冊。" -ForegroundColor Yellow
        return 1
    }
    return 0
}

function Do-Uninstall {
    Write-Host "`n[*] 正在自 Windows 工作排程器移除排程 [$TaskName]..." -ForegroundColor Yellow
    schtasks.exe /Delete /TN $TaskName /F
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n[OK] 排程 [$TaskName] 已成功移除！" -ForegroundColor Green
        return 0
    } else {
        Write-Host "`n[INFO] 移除失敗或該排程本就不存在。" -ForegroundColor Yellow
        return 1
    }
}

if ($Action -match "^(?i)install$") {
    $code = Do-Install
    exit $code
}

if ($Action -match "^(?i)status$") {
    $code = Do-Status
    exit $code
}

if ($Action -match "^(?i)uninstall$") {
    $code = Do-Uninstall
    exit $code
}

Write-Host "請選擇操作模式："
Write-Host "  1. 註冊 / 更新每日排程 (每日 20:00 自動執行)"
Write-Host "  2. 檢查排程狀態 (Query)"
Write-Host "  3. 移除每日排程 (Uninstall)"
Write-Host "  0. 退出"
$choice = Read-Host "`n請輸入選項 [0-3]"

if ($choice -eq "1") {
    $code = Do-Install
    exit $code
} elseif ($choice -eq "2") {
    $code = Do-Status
    exit $code
} elseif ($choice -eq "3") {
    $code = Do-Uninstall
    exit $code
} else {
    Write-Host "已取消操作。"
    exit 0
}
