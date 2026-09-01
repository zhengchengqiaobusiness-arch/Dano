@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not defined BSS_PORT set "BSS_PORT=4310"
if not defined BSS_OPEN_UI set "BSS_OPEN_UI=true"
set "BSS_URL=http://127.0.0.1:%BSS_PORT%"

echo ========================================
echo Pi Business Skill Studio
echo ========================================
echo.
echo [CHECK] Checking whether Studio is already running...
powershell.exe -NoProfile -NonInteractive -Command "$ProgressPreference='SilentlyContinue'; try { $r=Invoke-WebRequest -UseBasicParsing -Uri '%BSS_URL%/api/status' -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>&1
if not errorlevel 1 (
  echo [INFO] Pi Business Skill Studio is already running at %BSS_URL%
  if /I "%BSS_OPEN_UI%"=="true" start "" "%BSS_URL%"
  endlocal
  exit /b 0
)

where node.exe >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Node.js was not found. The project cannot start.
  set "EXIT_CODE=1"
  goto :failed
)

where npm.cmd >nul 2>&1
if errorlevel 1 (
  echo [ERROR] npm was not found. The project cannot start.
  set "EXIT_CODE=1"
  goto :failed
)

if not exist "node_modules\.bin\tsx.cmd" (
  echo [ERROR] Project dependencies are missing. BAT only starts the project and will not install them.
  set "EXIT_CODE=1"
  goto :failed
)

echo [START] Launching Pi Business Skill Studio...
echo [INFO] Project configuration will be read from .env when present.
echo.
call npm run web
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" (
  endlocal
  exit /b 0
)

:failed
echo.
echo [ERROR] Pi Business Skill Studio did not start. Exit code: %EXIT_CODE%
echo [INFO] The error above is preserved so it can be read.
if /I not "%BSS_NO_PAUSE%"=="true" pause
endlocal & exit /b %EXIT_CODE%
