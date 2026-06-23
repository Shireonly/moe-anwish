@echo off
chcp 65001 >nul 2>&1
title 简单随意的聊天室
echo.
echo ========================================
echo   简单随意的聊天室 - Local Server
echo ========================================
echo.
echo Checking port 8766...
netstat -ano | findstr ":8766 " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo Server is already running on port 8766!
    echo Open http://localhost:8766 in your browser.
) else (
    echo Starting server...
    cd /d "%~dp0"
    python app.py
)
echo.
pause