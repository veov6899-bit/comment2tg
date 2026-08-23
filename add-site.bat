@echo off
chcp 65001 >nul
cd /d "%~dp0"
set /p URL="Setgegdeltei medeenii link: "
python forwarder.py --add "%URL%"
pause
