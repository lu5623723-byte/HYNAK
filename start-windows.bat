@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 launcher.py
  if errorlevel 1 pause
  exit /b
)
where python >nul 2>nul
if %errorlevel% equ 0 (
  python launcher.py
  if errorlevel 1 pause
  exit /b
)
echo Please install Python 3.11+ from https://www.python.org/downloads/windows/
echo Enable Add Python to PATH, then run this file again.
pause
