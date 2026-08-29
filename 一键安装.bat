@echo off
chcp 65001 >nul
title 🐳 小焦 · 一键安装/启动
cd /d "%~dp0"
echo.
echo  🐳 小焦 XiaoJiao · 一键安装/启动
echo  --------------------------------
echo.
python install_auto.py
echo.
echo  如果没自动打开浏览器，请访问 http://127.0.0.1:5000
pause
