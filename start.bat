@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title BeamHub

where py >nul 2>nul
if not errorlevel 1 goto use_py
where python >nul 2>nul
if not errorlevel 1 goto use_python

echo.
echo Python not found. Install Python 3.10+ from python.org
echo and keep the "Add to PATH" checkbox during setup.
echo.
pause
exit /b 1

:use_py
py app.py %*
goto end

:use_python
python app.py %*
goto end

:end
if errorlevel 1 pause