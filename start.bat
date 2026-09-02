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

if not exist "node_modules\tsx\package.json" (
  echo [ERROR] Project dependencies are missing. BAT only starts the project and will not install them.
  set "EXIT_CODE=1"
  goto :failed
)

echo [CHECK] Stopping leftover Studio processes on port %BSS_PORT%...
node.exe scripts\stop-studio.mjs
if /I "%BSS_SKIP_WEB%"=="true" (
  echo [INFO] Leftover Studio processes were stopped.
  endlocal
  exit /b 0
)

echo [START] Launching Pi Business Skill Studio...
echo [INFO] Close this CMD window to stop the page and every Studio process.
echo [INFO] Project configuration will be read from .env when present.
echo.
node.exe --env-file-if-exists=.env --import tsx src/web/server.ts
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo [STOP] CMD session ended. Killing leftover Studio processes...
node.exe scripts\stop-studio.mjs
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
