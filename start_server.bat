@echo off
chcp 65001 >nul 2>&1
echo Checking port 8766...
netstat -ano | findstr ":8766 " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo.
    echo Server is already running on port 8766!
    echo.
    echo Open http://localhost:8766 in your browser.
) else (
    echo Starting awishr server...
    echo.
    "C:\Program Files\AutoClaw\resources\python\python3.exe" "C:\Users\Shire\.openclaw-autoclaw\agents\ouo\workspace\chatroom\app.py"
)
echo.
echo Press any key to exit...
pause >nul
