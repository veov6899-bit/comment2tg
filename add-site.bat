@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_python.bat"
if not defined PYEXE goto nopython
set /p URL="Setgegdeltei medeenii link: "
"%PYEXE%" forwarder.py --add "%URL%"
pause

goto :eof

:nopython
echo.
echo   Python oldsongui esvel sanguudiig suulgaj chadsangui.
echo   https://www.python.org/downloads/ deerees Python 3.11 suulgana uu.
echo.
pause
