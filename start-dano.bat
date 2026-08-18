@echo off
title Dano Launcher
setlocal EnableExtensions

set "ROOT=%~dp0"
set "PY=E:\python\condaEnv\dano-backend\python.exe"
if not defined DANO_BACKEND_PORT set "DANO_BACKEND_PORT=8077"
if not defined DANO_FRONTEND_PORT set "DANO_FRONTEND_PORT=5173"
set "BACKEND_PORT=%DANO_BACKEND_PORT%"
set "FRONTEND_PORT=%DANO_FRONTEND_PORT%"

call :clear_port %BACKEND_PORT% Backend
if errorlevel 1 goto :cleanup_failed
call :clear_port %FRONTEND_PORT% Frontend
if errorlevel 1 goto :cleanup_failed

echo Cleaning temporary files...
call :rmdir_if "%ROOT%.runtime"
call :rmdir_if "%ROOT%back\.pi-agent"
call :rmdir_if "%ROOT%back\.dano"
call :rmdir_if "%ROOT%skillfrontend\dist"
call :rmdir_if "%ROOT%back\examples\_chunks"
if exist "%ROOT%back\*.log" del /q "%ROOT%back\*.log" >nul 2>&1
if exist "%ROOT%*.log" del /q "%ROOT%*.log" >nul 2>&1
for /d /r "%ROOT%back\dano" %%D in (__pycache__ .pytest_cache .mypy_cache .ruff_cache) do (
    if exist "%%D" rd /s /q "%%D"
)
for /d /r "%ROOT%back\tests" %%D in (__pycache__ .pytest_cache .mypy_cache .ruff_cache) do (
    if exist "%%D" rd /s /q "%%D"
)
del /s /q "%ROOT%back\dano\*.pyc" "%ROOT%back\dano\*.pyo" "%ROOT%back\tests\*.pyc" "%ROOT%back\tests\*.pyo" >nul 2>&1
echo Done.

if not exist "%PY%" (
    echo ERROR: Backend Python was not found: %PY%
    goto :startup_failed
)

echo Starting backend on port %BACKEND_PORT% ...
pushd "%ROOT%back"
start "Dano Backend %BACKEND_PORT%" cmd /k ""%PY%" -m uvicorn dano.gateway.app:app --host 127.0.0.1 --port %BACKEND_PORT% --ws-max-queue 2048"
popd

echo Starting frontend on port %FRONTEND_PORT% ...
pushd "%ROOT%skillfrontend"
start "Dano Frontend %FRONTEND_PORT%" cmd /k "(if not exist node_modules npm install) && npm run dev -- --port %FRONTEND_PORT% --strictPort"
popd

echo Waiting for Dano-owned listeners and health checks ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$backendPort=%BACKEND_PORT%; $frontendPort=%FRONTEND_PORT%; $deadline=(Get-Date).AddSeconds(60);" ^
  "do {" ^
  "  $backendReady=$false; $frontendReady=$false;" ^
  "  try { $response=Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:' + $backendPort + '/health') -TimeoutSec 2; if ($response.StatusCode -eq 200) { $backendReady=$true } } catch {};" ^
  "  try { $response=Invoke-WebRequest -UseBasicParsing -Uri ('http://localhost:' + $frontendPort) -TimeoutSec 2; if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) { $frontendReady=$true } } catch {};" ^
  "  if ($backendReady -and $frontendReady) { exit 0 }; Start-Sleep -Milliseconds 500" ^
  "} while ((Get-Date) -lt $deadline);" ^
  "Write-Host 'ERROR: Dano services failed readiness checks.'; exit 1"
if errorlevel 1 goto :startup_failed

if not defined DANO_NO_BROWSER start "" http://localhost:%FRONTEND_PORT%
echo.
echo Backend  http://127.0.0.1:%BACKEND_PORT%
echo Frontend http://localhost:%FRONTEND_PORT%
echo First time: open frontend -^> Settings -^> enter model API key -^> Save -^> Onboard.
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
