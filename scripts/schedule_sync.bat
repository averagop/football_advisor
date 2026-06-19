@echo off
REM ============================================================
REM Football Advisor — 定时数据同步脚本
REM 用途：由 Windows 计划任务每日定时调用，执行多源数据同步
REM 路径：项目根目录
REM ============================================================
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "LOG_DIR=%SCRIPT_DIR%logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

set "LOG_FILE=%LOG_DIR%\sync_%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%.log"
set "LOG_FILE=%LOG_FILE: =0%"

echo [%date% %time%] 开始定时数据同步... > "%LOG_FILE%"

REM 激活虚拟环境（如果存在）
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

REM 执行每日数据同步
python -c "from football_advisor.sync import DataSyncCoordinator; from football_advisor.config import load_config; from football_advisor.models import MatchRequest; config = load_config(); coordinator = DataSyncCoordinator(config=config.sync, parent_config=config); result = coordinator.sync_structured_data(MatchRequest(query='daily_sync')); print(f'Sync result: {result.status} - {result.source}')" >> "%LOG_FILE%" 2>&1

set "EXIT_CODE=%ERRORLEVEL%"

if %EXIT_CODE% EQU 0 (
    echo [%date% %time%] 同步完成，状态=成功 >> "%LOG_FILE%"
) else (
    echo [%date% %time%] 同步失败，退出码=%EXIT_CODE% >> "%LOG_FILE%"
)

REM 清理 30 天前的日志
forfiles /p "%LOG_DIR%" /m "sync_*.log" /d -30 /c "cmd /c del @file" 2>nul

echo [%date% %time%] 日志已保存到 %LOG_FILE%
endlocal
exit /b %EXIT_CODE%