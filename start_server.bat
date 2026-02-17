@echo off
title PC Remote Console Server
echo.
echo ==========================================
echo   PC Remote Console - Server Launcher
echo ==========================================
echo.

:: Get local IP address
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4"') do (
    set IP=%%a
)
set IP=%IP: =%

echo   Your PC IP: %IP%
echo   Server URL: http://%IP%:5000
echo.
echo   Open the URL above on your phone to connect!
echo   Make sure your phone is on the same WiFi network.
echo.
echo ==========================================
echo.

cd /d "%~dp0"
python shutdown.py

pause
