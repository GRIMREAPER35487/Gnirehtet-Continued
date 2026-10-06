@echo off
title Gnirehtet Continued by Synthos
cd /d "%~dp0"
python gnirehtet_watchdog.py %*
if errorlevel 1 pause
