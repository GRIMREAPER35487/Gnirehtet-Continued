@echo off
setlocal
title Enable Windows Internet Connection Sharing for Meta Quest

:: If not elevated, relaunch with RunAs administrator
net session >nul 2>&1
if not errorlevel 1 goto :elevated

echo Requesting Administrator permissions...
:: Pass the script path through an environment variable, so that a path containing an apostrophe
:: (or any other quote) is never interpolated into the PowerShell command; [char]34 is a double quote.
set "GNIREHTET_ICS_SCRIPT=%~f0"
powershell -NoProfile -Command "$q = [char]34; Start-Process -FilePath $env:ComSpec -ArgumentList ('/c ' + $q + $q + $env:GNIREHTET_ICS_SCRIPT + $q + $q) -Verb RunAs"
exit /b

:elevated
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_ics.ps1"
echo.
pause
endlocal
