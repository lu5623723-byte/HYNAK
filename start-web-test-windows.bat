@echo off
chcp 65001 >nul
cd /d "%~dp0"
set HYNAK_TEST_MODE=1
if not defined HYNAK_DATA_DIR set "HYNAK_DATA_DIR=%USERPROFILE%\.hynak-web-test"
if not defined PORT set PORT=8765
set "PUBLIC_BASE_URL=http://127.0.0.1:%PORT%"
call start-windows.bat
