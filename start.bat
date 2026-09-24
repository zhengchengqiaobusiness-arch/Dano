@echo off
title Dano Launcher
setlocal EnableExtensions

set "ROOT=%~dp0"
if not defined DANO_FRONTEND_PORT set "DANO_FRONTEND_PORT=19082"
if not defined PI_CHECK_PORT set "PI_CHECK_PORT=19081"
set "FRONTEND_PORT=%DANO_FRONTEND_PORT%"
set "PICHECK_PORT=%PI_CHECK_PORT%"

call :clear_port %PICHECK_PORT% Backend
if errorlevel 1 goto :cleanup_failed
call :clear_port %FRONTEND_PORT% Frontend
if errorlevel 1 goto :cleanup_failed

echo Cleaning temporary files...
call :rmdir_if "%ROOT%frontend\dist"
if exist "%ROOT%*.log" del /q "%ROOT%*.log" >nul 2>&1
echo Done.

where node >nul 2>&1
if errorlevel 1 (
    echo ERROR: node was not found on PATH.
    goto :startup_failed
)
if not exist "%ROOT%.env" (
    echo ERROR: .env was not found: %ROOT%.env
    goto :startup_failed
)

echo Installing recorder...
pushd "%ROOT%"
if not exist node_modules call npm install
if errorlevel 1 (
    popd
    goto :startup_failed
)
popd

echo Starting backend on port %PICHECK_PORT% ...
pushd "%ROOT%"
start "Dano Backend %PICHECK_PORT%" cmd /k "set PORT=%PICHECK_PORT%&& node --env-file=.env src/server.mjs"
popd

echo Starting frontend on port %FRONTEND_PORT% ...
pushd "%ROOT%frontend"
start "Dano Frontend %FRONTEND_PORT%" cmd /k "set DANO_PI_CHECK=http://127.0.0.1:%PICHECK_PORT%&& (if not exist node_modules npm install) && npm run dev -- --host 127.0.0.1 --port %FRONTEND_PORT% --strictPort"
popd

echo Waiting for Dano-owned listeners and health checks ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$backendPort=%PICHECK_PORT%; $frontendPort=%FRONTEND_PORT%; $deadline=(Get-Date).AddSeconds(60);" ^
  "do {" ^
  "  $backendReady=$false; $frontendReady=$false;" ^
  "  try { $response=Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:' + $backendPort + '/api/health') -TimeoutSec 2; if ($response.StatusCode -eq 200) { $backendReady=$true } } catch {};" ^
  "  try { $response=Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:' + $frontendPort + '/') -TimeoutSec 2; if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) { $frontendReady=$true } } catch {};" ^
  "  if ($backendReady -and $frontendReady) { exit 0 }; Start-Sleep -Milliseconds 500" ^
  "} while ((Get-Date) -lt $deadline);" ^
  "Write-Host 'ERROR: Dano services failed readiness checks.'; exit 1"
if errorlevel 1 goto :startup_failed

if not defined DANO_NO_BROWSER start "" http://127.0.0.1:%FRONTEND_PORT%/recording
echo.
echo Backend  http://127.0.0.1:%PICHECK_PORT%
echo Frontend http://127.0.0.1:%FRONTEND_PORT%/recording
echo (You can close THIS window; services run in the other two.)
if not defined DANO_NONINTERACTIVE pause
exit /b 0

:rmdir_if
if exist "%~1" rd /s /q "%~1"
exit /b 0

:clear_port
set "TARGET_PORT=%~1"
set "SERVICE_NAME=%~2"
echo Clearing %SERVICE_NAME% port %TARGET_PORT% and checking that it stays free ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$port=%TARGET_PORT%; $deadline=(Get-Date).AddSeconds(15); $freeSince=$null; $getOwners={ param([int]$port) @(netstat.exe -ano -p tcp | Select-String -Pattern ('^\s*TCP\s+\S+:' + $port + '\s+\S+\s+LISTENING\s+(\d+)\s*$') | ForEach-Object { [int]$_.Matches[0].Groups[1].Value } | Sort-Object -Unique) };" ^
  "do {" ^
  "  $owners=@(& $getOwners $port | Where-Object { $_ -gt 0 });" ^
  "  if ($owners.Count -eq 0) { if ($null -eq $freeSince) { $freeSince=Get-Date } elseif (((Get-Date)-$freeSince).TotalSeconds -ge 2) { exit 0 } }" ^
  "  else { $freeSince=$null; foreach ($processId in $owners) { if ($processId -eq 4) { Write-Host ('ERROR: Port ' + $port + ' is owned by Windows System PID 4.'); exit 1 }; Write-Host ('Stopping PID ' + $processId + ' on port ' + $port); taskkill.exe /PID $processId /T /F | Out-Null } };" ^
  "  Start-Sleep -Milliseconds 250" ^
  "} while ((Get-Date) -lt $deadline);" ^
  "$remaining=@(& $getOwners $port);" ^
  "Write-Host ('ERROR: Port ' + $port + ' did not remain free for 2 seconds.'); foreach ($processId in $remaining) { Write-Host ('  PID ' + $processId) }; exit 1"
exit /b %errorlevel%

:cleanup_failed
echo.
echo ERROR: Port cleanup failed. Dano was not started.
echo Run this launcher as Administrator if the reported process cannot be stopped.
if not defined DANO_NONINTERACTIVE pause
exit /b 1

:startup_failed
echo.
echo ERROR: Dano startup failed.
if not defined DANO_NONINTERACTIVE pause
exit /b 1
