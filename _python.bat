@echo off
rem ---------------------------------------------------------------
rem  Zov Python-iig oloh tuslah script.
rem  Ene komputer deer 4 Python baigaa bolovch zarim ni shaardlagatai
rem  sanguudgui. Tiimees import shalgaj, ajilladagiig ni songono.
rem ---------------------------------------------------------------
set "PYEXE="
set "CAND=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"

if exist "%CAND%" call :try "%CAND%"
if not defined PYEXE call :try python
if not defined PYEXE if exist "%CAND%" call :fix "%CAND%"
if not defined PYEXE call :fix python
goto :eof

:try
"%~1" -c "import requests,bs4,lxml" >nul 2>&1
if errorlevel 1 goto :eof
set "PYEXE=%~1"
goto :eof

:fix
echo.
echo   Shaardlagatai Python sanguudiig suulgaj baina, tureeree hulee...
echo.
"%~1" -m pip install --disable-pip-version-check -q -r "%~dp0requirements.txt"
call :try "%~1"
goto :eof
