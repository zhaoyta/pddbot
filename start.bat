@echo off
rem pddbot 一键启动 (Windows)
rem   - 自动安装 uv
rem   - uv venv + uv pip install (镜像源由 uv.toml 固化)
rem   - uv run 装 playwright chromium 内核
rem   - uv run 启动 GUI
rem
rem 双击 start.bat 即可。

setlocal enableextensions
cd /d "%~dp0"

echo ============================================================
echo   pddbot 启动器
echo ============================================================

rem ---------- 1. 确认 uv ----------
where uv >nul 2>nul
if errorlevel 1 (
    echo ^>^>^> 未检测到 uv,自动安装...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    rem uv 默认装到 %USERPROFILE%\.local\bin,临时把它加进 PATH
    set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
    where uv >nul 2>nul
    if errorlevel 1 (
        echo 错误:uv 安装后仍找不到,请手动加入 PATH 后重试
        echo       文档 https://docs.astral.sh/uv/getting-started/installation/
        pause
        exit /b 1
    )
)
for /f "delims=" %%v in ('uv --version') do echo     uv: %%v

rem ---------- 2. 虚拟环境 ----------
if exist ".venv" (
    echo ^>^>^> 复用已存在的 .venv
) else (
    echo ^>^>^> 创建虚拟环境 .venv ^(Python 3.11^)
    uv venv --python 3.11 || goto :err
)

rem ---------- 3. Python 依赖 ----------
echo ^>^>^> 同步 Python 依赖 ^(uv pip install -r requirements.txt^)
uv pip install -r requirements.txt || goto :err

rem ---------- 4. Playwright Chromium 内核 ----------
echo ^>^>^> 检查 / 安装 Playwright Chromium
uv run python -m playwright install chromium || goto :err

rem ---------- 5. 启动 GUI ----------
echo ^>^>^> 启动 GUI ^(uv run python -m gui.app^)
uv run python -m gui.app %*
exit /b %errorlevel%

:err
echo.
echo 启动失败,上方有错误信息,请截图反馈
pause
exit /b 1
