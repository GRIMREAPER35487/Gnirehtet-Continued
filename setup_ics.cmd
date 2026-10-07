@echo off
setlocal
title Enable Windows Internet Connection Sharing for Meta Quest

:: If not elevated, relaunch with RunAs administrator
net session >nul 2>&1
if errorlevel 1 (
    echo Requesting Administrator permissions...
    powershell -NoProfile -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_ics.ps1"
echo.
pause
endlocal
