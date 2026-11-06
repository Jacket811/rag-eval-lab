@echo off
chcp 65001 >nul
title RAG 一键部署
setlocal enabledelayedexpansion

echo.
echo ============================================
echo   RAG 评估项目 一键部署
echo ============================================
echo.

rem ===== 检查 Docker 是否安装 =====
where docker >nul 2>nul
if errorlevel 1 (
  echo   [×] 没有找到 Docker
  echo.
  echo       请先安装 Docker Desktop：
  echo       https://www.docker.com/products/docker-desktop/
  echo.
  echo       安装完重启电脑，再双击本文件。
  echo.
  pause
  exit /b 1
)
echo   [√] 已安装 Docker

rem ===== 检查 Docker 是否在运行 =====
docker info >nul 2>nul
if errorlevel 1 (
  echo   [×] Docker 已安装，但没有运行
  echo.
  echo       请打开 Docker Desktop（开始菜单里找），
  echo       等左下角的小鲸鱼图标不再转动，再双击本文件。
  echo.
  pause
  exit /b 1
)
echo   [√] Docker 正在运行

cd /d "%~dp0"

rem ===== 生成配置文件 =====
if not exist docker-compose.yml (
  copy docker\docker-compose.simple.yml docker-compose.yml >nul
  echo   [√] 已生成 docker-compose.yml
) else (
  echo   [√] 已有 docker-compose.yml，沿用
)

rem ===== 启动容器 =====
echo.
echo --------------------------------------------
echo   第 1 步：下载镜像并启动容器
echo   第一次约 8GB，可能要 10-30 分钟
echo   屏幕会滚动大量进度，属于正常现象
echo --------------------------------------------
echo.
docker compose up -d
if errorlevel 1 (
  echo.
  echo   [×] 启动失败。常见原因：
  echo       1. 端口 3000 被占用（关掉其他 Open WebUI）
  echo       2. 网络问题（配置 Docker 镜像加速）
  echo.
  pause
  exit /b 1
)

echo.
echo   等待服务就绪（约 20 秒）...
timeout /t 20 /nobreak >nul

rem ===== 下载模型 =====
echo.
echo --------------------------------------------
echo   第 2 步：下载模型（约 6GB）
echo   qwen3:8b  生成用，5GB
echo   bge-m3    检索用，1.2GB
echo --------------------------------------------
echo.

rem 注意：部分 ollama 镜像里 `ollama pull` 会报 "something went wrong"，
rem 但 HTTP 接口是好的，所以这里走 API，不用容器内的命令行。
echo   正在下载 qwen3:8b（5GB，屏幕会滚动 JSON 进度，属于正常）...
echo {"name":"qwen3:8b"}> "%TEMP%\ollama-pull.json"
curl.exe -s -X POST http://127.0.0.1:11434/api/pull -H "Content-Type: application/json" -d @"%TEMP%\ollama-pull.json"
echo.

echo   正在下载 bge-m3（1.2GB）...
echo {"name":"bge-m3"}> "%TEMP%\ollama-pull.json"
curl.exe -s -X POST http://127.0.0.1:11434/api/pull -H "Content-Type: application/json" -d @"%TEMP%\ollama-pull.json"
del "%TEMP%\ollama-pull.json" >nul 2>nul
echo.

echo   检查模型是否就绪...
curl.exe -s http://127.0.0.1:11434/api/tags
echo.

echo.
echo ============================================
echo   部署完成
echo ============================================
echo.
echo   接下来：
echo   1. 浏览器打开  http://127.0.0.1:3000
echo      注册第一个账号（自动是管理员）
echo   2. 建一个知识库，上传你的文档
echo   3. 双击 start.bat 打开评估控制台
echo.
echo   详细步骤见 docs\deploy.md
echo.
echo   关闭这个窗口不会停止服务。
echo   要停止服务：在本目录执行 docker compose down
echo.
pause
