@echo off
rem Windows 双击入口：拉起浏览器训练台（bank.py web，正本在 scripts/web_app.py）。
rem 不想开浏览器可在终端用 scripts/menu.py 的命令行菜单。
cd /d %~dp0
where uv >nul 2>nul
if errorlevel 1 (
  echo 还没安装 uv（Python 环境管家，装一次即可）。
  echo 请打开 PowerShell 执行下面这一行，然后重新双击本文件：
  echo.
  echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  echo.
  pause
  exit /b 1
)
uv run python scripts\bank.py web
pause
