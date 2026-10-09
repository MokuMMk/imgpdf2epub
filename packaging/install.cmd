@echo off
chcp 65001 >nul 2>&1
title ImgPdf2Epub Setup
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
echo   Press any key to close...
pause >nul
