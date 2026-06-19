@echo off
REM ============================================================
REM Football Advisor — 注册 Windows 计划任务
REM 用途：每日 06:00、12:00、18:00、22:00 执行数据同步
REM 运行方式：右键以管理员身份运行此脚本
REM ============================================================
setlocal

set "TASK_NAME=FootballAdvisor_DailySync"
set "SCRIPT_DIR=%~dp0"
set "BAT_PATH=%SCRIPT_DIR%schedule_sync.bat"

echo 正在注册 Windows 计划任务: %TASK_NAME%
echo 脚本路径: %BAT_PATH%

schtasks /create /tn "%TASK_NAME%" /tr "\"%BAT_PATH%\"" /sc daily /st 06:00 /ri 360 /du 24:00 /f

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ========================================
    echo 计划任务注册成功！
    echo 任务名: %TASK_NAME%
    echo 执行频率: 每 6 小时 (06:00/12:00/18:00/22:00)
    echo.
    echo 管理命令:
    echo   查看: schtasks /query /tn "%TASK_NAME%" /v
    echo   运行: schtasks /run /tn "%TASK_NAME%"
    echo   暂停: schtasks /change /tn "%TASK_NAME%" /disable
    echo   恢复: schtasks /change /tn "%TASK_NAME%" /enable
    echo   删除: schtasks /delete /tn "%TASK_NAME%" /f
    echo ========================================
) else (
    echo.
    echo 计划任务注册失败！请以管理员身份运行此脚本。
)

endlocal
pause