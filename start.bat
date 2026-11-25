@echo off
chcp 65001 >nul
title 隧道代理IP质量测试 WebUI
cd /d %~dp0

rem 从 .env 读端口，默认 8821
set PORT=8821
if exist .env (
    for /f "usebackq eol=# tokens=1,2 delims==" %%a in (".env") do (
        if /i "%%a"=="WEBUI_PORT" set PORT=%%b
    )
)

rem 自动结束占用该端口的旧服务进程：双击本脚本即可替换旧代码重启，无需手动找 PID
set OLDPID=
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do set OLDPID=%%p
if defined OLDPID (
    echo [更新] 结束旧服务进程 PID=%OLDPID% ...
    taskkill /PID %OLDPID% /F >nul 2>&1
    timeout /t 1 /nobreak >nul
)

if not exist .venv\Scripts\python.exe (
    echo [初始化] 创建虚拟环境并安装依赖...
    set "http_proxy=http://192.168.50.48:7890"
    set "https_proxy=http://192.168.50.48:7890"
    uv venv .venv
    if errorlevel 1 goto :err
    uv pip install -r requirements.txt --python .venv\Scripts\python.exe
    if errorlevel 1 goto :err
    echo [初始化] 完成
)

echo [启动] http://127.0.0.1:%PORT%  （关闭本窗口或 Ctrl+C 即停止；改代码后再次双击本脚本即可自动替换旧进程）
.venv\Scripts\python.exe -m app.main
pause
goto :eof

:err
echo [失败] 环境初始化失败，请检查 uv 是否安装、代理是否可用
pause
