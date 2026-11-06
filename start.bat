@echo off
chcp 65001 >nul
title RAG 评估控制台

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   没有找到 Python。
  echo   请先安装 Python 3.8 或更高版本：https://www.python.org/downloads/
  echo   安装时记得勾选 "Add Python to PATH"
  echo.
  pause
  exit /b 1
)

cd /d "%~dp0eval"
if not exist "serve.py" (
  echo   找不到 eval\serve.py，请确认文件完整
  pause
  exit /b 1
)

echo.
echo   正在启动本地控制台，浏览器会自动打开…
echo   关闭这个窗口即可停止服务。
echo.
python serve.py
pause
