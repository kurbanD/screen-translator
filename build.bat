@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0" || goto :fail
set "MODE=%~1"
if /i "%MODE%"=="" set "MODE=lite"
if not exist ".build-venv\Scripts\python.exe" (
    py -3 --version >nul 2>&1
    if errorlevel 1 (
        python -m venv .build-venv
    ) else (
        py -3 -m venv .build-venv
    )
    if errorlevel 1 goto :fail
)
set "PY=.build-venv\Scripts\python.exe"
"%PY%" -m pip install --disable-pip-version-check -r requirements-build.txt
if errorlevel 1 goto :fail
"%PY%" build_windows.py %MODE%
if errorlevel 1 goto :fail
echo.
echo Build complete. Check the dist folder.
start "" "%CD%\dist"
pause
exit /b 0
:fail
echo.
echo BUILD FAILED. See the error above.
pause
exit /b 1
