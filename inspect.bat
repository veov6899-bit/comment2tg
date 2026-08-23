@echo off
chcp 65001 >nul
cd /d "%~dp0"
set /p URL="Medeenii hayg (URL): "
python forwarder.py --inspect "%URL%"
pause
