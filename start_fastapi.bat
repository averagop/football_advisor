@echo off
REM ============================================================
REM Football Betting Advisor - FastAPI 启动脚本
REM 用法: 双击运行或在命令行执行 start_fastapi.bat
REM ============================================================

cd /d "%~dp0"

REM ---- 检查运行时 Python ----
set PYTHON=.\.runtime\python\python.exe
if not exist "%PYTHON%" (
    echo [错误] 未找到项目运行时 Python: %PYTHON%
    echo 请确保 .runtime\python\python.exe 存在。
    pause
    exit /b 1
)

echo [信息] Python 运行时: %PYTHON%

REM ---- 检查核心依赖 ----
%PYTHON% -c "import fastapi, uvicorn, duckdb" >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 缺少核心依赖 (fastapi/uvicorn/duckdb)，请先安装: 
    echo %PYTHON% -m pip install -r requirements.txt
    pause
    exit /b 1
)

REM ---- 加载环境变量 (如果 .env 不存在，使用默认值) ----
if exist ".env" (
    echo [信息] 加载 .env 配置文件...
    for /f "usebackq tokens=1,2 delims==" %%a in (".env") do (
        set "%%a=%%b"
    )
) else (
    echo [信息] 未找到 .env 文件，使用默认配置。
    echo 建议: 复制 .env.example 为 .env 并填入你的 API Key。
)

REM ---- 设置默认环境变量 (如果未在 .env 中配置) ----
if "%FOOTBALL_DUCKDB_PATH%"=="" set FOOTBALL_DUCKDB_PATH=football_system.db
if "%FOOTBALL_BACKUP_LLM_BASE_URL%"=="" set FOOTBALL_BACKUP_LLM_BASE_URL=http://localhost:11434/v1
if "%FOOTBALL_BACKUP_LLM_MODEL%"=="" set FOOTBALL_BACKUP_LLM_MODEL=qwen2.5:7b

echo [信息] DuckDB 路径: %FOOTBALL_DUCKDB_PATH%
echo [信息] Backup LLM: %FOOTBALL_BACKUP_LLM_MODEL% @ %FOOTBALL_BACKUP_LLM_BASE_URL%

REM ---- 启动 FastAPI ----
set FASTAPI_HOST=%FOOTBALL_HOST%
if "%FASTAPI_HOST%"=="" set FASTAPI_HOST=127.0.0.1
echo.
echo [启动] Football Betting Advisor API 服务...
echo [地址] http://%FASTAPI_HOST%:8000
echo [文档] http://%FASTAPI_HOST%:8000/docs
echo [健康检查] http://%FASTAPI_HOST%:8000/health
if "%FASTAPI_HOST%"=="0.0.0.0" (
    echo [Docker访问] http://host.docker.internal:8000
) else (
    echo [提示] 如需Docker访问请先设置 set FOOTBALL_HOST=0.0.0.0
)
echo.
echo 按 Ctrl+C 停止服务。
echo.

%PYTHON% -m uvicorn football_advisor.api:app --host %FASTAPI_HOST% --port 8000 --log-level info

pause