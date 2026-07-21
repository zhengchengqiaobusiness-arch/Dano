@echo off
title Dano Launcher
setlocal EnableExtensions

set "ROOT=%~dp0"
set "PY=E:\python\condaEnv\dano-backend\python.exe"
if not defined DANO_BACKEND_PORT set "DANO_BACKEND_PORT=8077"
if not defined DANO_FRONTEND_PORT set "DANO_FRONTEND_PORT=5173"
set "BACKEND_PORT=%DANO_BACKEND_PORT%"
set "FRONTEND_PORT=%DANO_FRONTEND_PORT%"

echo Stopping previous Dano process trees ...
call :stop_known_dano_processes

call :clear_port %BACKEND_PORT% Backend
if errorlevel 1 goto :cleanup_failed
call :clear_port %FRONTEND_PORT% Frontend
if errorlevel 1 goto :cleanup_failed

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
  "$root=[IO.Path]::GetFullPath('%ROOT%'); $backendPort=%BACKEND_PORT%; $frontendPort=%FRONTEND_PORT%; $deadline=(Get-Date).AddSeconds(60);" ^
  "do {" ^
  "  $backendReady=$false; $frontendReady=$false;" ^
  "  $backendOwners=@(Get-NetTCPConnection -State Listen -LocalPort $backendPort -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique);" ^
  "  foreach ($processId in $backendOwners) { $process=Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $processId) -ErrorAction SilentlyContinue; if ($process.CommandLine -match 'dano\.gateway\.app:app') { try { $response=Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:' + $backendPort + '/health') -TimeoutSec 2; if ($response.StatusCode -eq 200) { $backendReady=$true } } catch {} } };" ^
  "  $frontendOwners=@(Get-NetTCPConnection -State Listen -LocalPort $frontendPort -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique);" ^
  "  foreach ($processId in $frontendOwners) { $process=Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $processId) -ErrorAction SilentlyContinue; $command=[string]$process.CommandLine; if ($process.Name -ieq 'node.exe' -and $command -match 'vite[\\/]bin[\\/]vite\.js' -and $command -like ('*' + $root + 'skillfrontend*')) { try { $response=Invoke-WebRequest -UseBasicParsing -Uri ('http://localhost:' + $frontendPort) -TimeoutSec 2; if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) { $frontendReady=$true } } catch {} } };" ^
  "  if ($backendReady -and $frontendReady) { exit 0 }; Start-Sleep -Milliseconds 500" ^
  "} while ((Get-Date) -lt $deadline);" ^
  "Write-Host 'ERROR: Dano-owned services failed readiness checks.';" ^
  "foreach ($port in @($backendPort,$frontendPort)) { $owners=@(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); if ($owners.Count -eq 0) { Write-Host ('  Port ' + $port + ': no listener') } else { foreach ($processId in $owners) { $process=Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $processId) -ErrorAction SilentlyContinue; Write-Host ('  Port ' + $port + ': PID ' + $processId + ' ' + $process.Name + ' ' + $process.CommandLine) } } }; exit 1"
if errorlevel 1 goto :startup_failed

if not defined DANO_NO_BROWSER start "" http://localhost:%FRONTEND_PORT%
echo.
echo Backend  http://127.0.0.1:%BACKEND_PORT%
echo Frontend http://localhost:%FRONTEND_PORT%
echo First time: open frontend -^> Settings -^> enter model API key -^> Save -^> Onboard.
echo (You can close THIS window; services run in the other two.)
if not defined DANO_NONINTERACTIVE pause
exit /b 0

:stop_known_dano_processes
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root=[IO.Path]::GetFullPath('%ROOT%');" ^
  "$targets=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $command=[string]$_.CommandLine; (($_.Name -in @('python.exe','pythonw.exe')) -and $command -match 'dano\.gateway\.app:app') -or ($_.Name -ieq 'node.exe' -and $command -match 'vite[\\/]bin[\\/]vite\.js' -and $command -like ('*' + $root + 'skillfrontend*')) });" ^
  "foreach ($process in $targets) { Write-Host ('Stopping Dano PID ' + $process.ProcessId + ' (' + $process.Name + ')'); taskkill.exe /PID $process.ProcessId /T /F | Out-Null }; exit 0"
taskkill /FI "WINDOWTITLE eq Dano Backend %BACKEND_PORT%*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Dano Frontend %FRONTEND_PORT%*" /T /F >nul 2>&1
exit /b 0

:clear_port
set "TARGET_PORT=%~1"
set "SERVICE_NAME=%~2"
echo Clearing %SERVICE_NAME% port %TARGET_PORT% and checking that it stays free ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$port=%TARGET_PORT%; $deadline=(Get-Date).AddSeconds(15); $freeSince=$null;" ^
  "do {" ^
  "  $owners=@(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ -gt 0 });" ^
  "  if ($owners.Count -eq 0) { if ($null -eq $freeSince) { $freeSince=Get-Date } elseif (((Get-Date)-$freeSince).TotalSeconds -ge 2) { exit 0 } }" ^
  "  else { $freeSince=$null; foreach ($processId in $owners) { if ($processId -eq 4) { Write-Host ('ERROR: Port ' + $port + ' is owned by Windows System PID 4.'); exit 1 }; $process=Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $processId) -ErrorAction SilentlyContinue; Write-Host ('Stopping PID ' + $processId + ' (' + $process.Name + ') on port ' + $port); taskkill.exe /PID $processId /T /F | Out-Null } };" ^
  "  Start-Sleep -Milliseconds 250" ^
  "} while ((Get-Date) -lt $deadline);" ^
  "$remaining=@(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique);" ^
  "Write-Host ('ERROR: Port ' + $port + ' did not remain free for 2 seconds.'); foreach ($processId in $remaining) { $process=Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $processId) -ErrorAction SilentlyContinue; Write-Host ('  PID ' + $processId + ' ' + $process.Name + ' ' + $process.CommandLine) }; exit 1"
exit /b %errorlevel%

:cleanup_failed
echo.
echo ERROR: Port cleanup failed. Dano was not started.
echo Run this launcher as Administrator if the reported process cannot be stopped.
if not defined DANO_NONINTERACTIVE pause
exit /b 1

:startup_failed
echo.
echo ERROR: Dano startup failed. Cleaning up the partial startup ...
call :stop_known_dano_processes
if not defined DANO_NONINTERACTIVE pause
exit /b 1
