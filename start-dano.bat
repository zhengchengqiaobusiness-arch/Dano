@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title Dano Launcher

rem ---------------------------------------------------------------------------
rem Dano one-click launcher (Windows)
rem
rem Double-click: preflight dependencies, start backend/frontend, wait until
rem both are healthy, then open the UI.
rem Command line: start-dano.bat check   (read-only preflight, starts nothing)
rem Command line: start-dano.bat --stub  (local UI test, disables real Pi calls)
rem
rem Optional overrides:
rem   DANO_PYTHON        Absolute path to a Python 3.12+ executable
rem   DANO_BACKEND_PORT  Backend port (default 8077)
rem   DANO_FRONTEND_PORT Frontend port (default 5173)
rem   DANO_START_TIMEOUT Health wait in seconds (default 90)
rem   DANO_NO_BROWSER=1  Do not open the browser after startup
rem   DANO_NO_PAUSE=1    Do not pause on success/failure
rem   DANO_REUSE_SERVICES=1  Explicitly reuse healthy services on chosen ports
rem ---------------------------------------------------------------------------

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%back"
set "AGENT_DIR=%BACKEND_DIR%\agent"
set "PLAYWRIGHT_DIR=%ROOT%Playwright"
set "PLAYWRIGHT_SRC=%PLAYWRIGHT_DIR%\src"
set "FRONTEND_DIR=%ROOT%skillfrontend"

if defined DANO_BACKEND_PORT (
    set "BACKEND_PORT=%DANO_BACKEND_PORT%"
) else (
    set "BACKEND_PORT=8077"
)
if defined DANO_FRONTEND_PORT (
    set "FRONTEND_PORT=%DANO_FRONTEND_PORT%"
) else (
    set "FRONTEND_PORT=5173"
)
if defined DANO_START_TIMEOUT (
    set "START_TIMEOUT=%DANO_START_TIMEOUT%"
) else (
    set "START_TIMEOUT=90"
)

set "BACKEND_URL=http://127.0.0.1:%BACKEND_PORT%"
set "FRONTEND_URL=http://127.0.0.1:%FRONTEND_PORT%"
set "FRONTEND_PAGE=%FRONTEND_URL%/recording"
set "DANO_GATEWAY=%BACKEND_URL%"

set "CHECK_ONLY=0"
set "BACKEND_STARTED=0"
set "FRONTEND_STARTED=0"
set "BACKEND_PID="
set "FRONTEND_PID="
set "BACKEND_START_TICKS="
set "FRONTEND_START_TICKS="
if /i "%~1"=="__dano_backend" goto :child_backend
if /i "%~1"=="__dano_frontend" goto :child_frontend
set "PI_STUB="
if /i "%~1"=="check" set "CHECK_ONLY=1"
if /i "%~2"=="check" set "CHECK_ONLY=1"
if /i "%~1"=="--stub" set "PI_STUB=1"
if /i "%~2"=="--stub" set "PI_STUB=1"

echo.
echo [Dano] Project: %ROOT%
if "%CHECK_ONLY%"=="1" echo [Dano] Read-only launcher check

call :validate_layout || goto :failed
call :validate_port "%BACKEND_PORT%" "DANO_BACKEND_PORT" || goto :failed
call :validate_port "%FRONTEND_PORT%" "DANO_FRONTEND_PORT" || goto :failed
call :validate_number "%START_TIMEOUT%" "DANO_START_TIMEOUT" || goto :failed
if "%BACKEND_PORT%"=="%FRONTEND_PORT%" (
    echo [Dano] ERROR: backend and frontend ports must be different.
    goto :failed
)
echo [Dano] Checking Python and command-line runtimes ...
call :find_python || goto :failed
call :check_commands || goto :failed
echo [Dano] Checking Python dependencies and Chromium ...
call :ensure_python_dependencies || goto :failed
call :ensure_chromium || goto :failed
echo [Dano] Checking npm dependencies ...
call :ensure_node_dependencies "%PLAYWRIGHT_DIR%" "%ROOT%Playwright\package-lock.json"
set "STEP_RC=%ERRORLEVEL%"
title Dano Launcher
if not "%STEP_RC%"=="0" goto :failed
call :ensure_node_dependencies "%FRONTEND_DIR%" "%ROOT%skillfrontend\package-lock.json"
set "STEP_RC=%ERRORLEVEL%"
title Dano Launcher
if not "%STEP_RC%"=="0" goto :failed
call :ensure_node_dependencies "%AGENT_DIR%" "%ROOT%back\agent\package-lock.json"
set "STEP_RC=%ERRORLEVEL%"
title Dano Launcher
if not "%STEP_RC%"=="0" goto :failed
call :check_database || goto :failed
call :check_pi_configuration || goto :failed
call :check_service_ports || goto :failed

if "%CHECK_ONLY%"=="1" (
    echo.
    echo DANO_LAUNCHER_CHECK_OK
    echo [Dano] Python, Node, npm, Pi runtime, Chromium and ports are ready.
    exit /b 0
)

:start_services
if defined PYTHONPATH (
    set "PYTHONPATH=%PLAYWRIGHT_SRC%;%BACKEND_DIR%;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%PLAYWRIGHT_SRC%;%BACKEND_DIR%"
)

call :probe_backend_ready
if errorlevel 1 (
    echo [Dano] Starting backend at %BACKEND_URL% ...
    call :launch_backend
    if errorlevel 1 goto :failed
) else (
    echo [Dano] Reusing healthy backend at %BACKEND_URL%.
)
call :wait_for_backend || goto :failed

call :probe_frontend
if errorlevel 1 (
    echo [Dano] Starting frontend at %FRONTEND_URL% ...
    call :launch_frontend
    if errorlevel 1 goto :failed
) else (
    echo [Dano] Reusing healthy frontend at %FRONTEND_URL%.
)
call :wait_for_frontend || goto :failed

echo.
echo [Dano] READY
echo [Dano] Backend : %BACKEND_URL%
echo [Dano] Frontend: %FRONTEND_URL%
if "%PI_STUB%"=="1" echo [Dano] NOTE: Pi stub mode is active; no real model calls will be made.
if not "%DANO_NO_BROWSER%"=="1" start "" "%FRONTEND_PAGE%"
if not "%DANO_NO_PAUSE%"=="1" (
    echo.
    echo Close the Backend and Frontend windows to stop Dano.
    pause
)
exit /b 0

:validate_layout
for %%D in ("%BACKEND_DIR%" "%AGENT_DIR%" "%PLAYWRIGHT_DIR%" "%FRONTEND_DIR%") do (
    if not exist "%%~fD\" (
        echo [Dano] ERROR: required directory is missing: %%~fD
        exit /b 1
    )
)
if not exist "%BACKEND_DIR%\dano\gateway\app.py" (
    echo [Dano] ERROR: backend entry point is missing.
    exit /b 1
)
if not exist "%FRONTEND_DIR%\package.json" (
    echo [Dano] ERROR: frontend package.json is missing.
    exit /b 1
)
exit /b 0

:validate_number
echo(%~1| findstr /r "^[1-9][0-9]*$" >nul
if errorlevel 1 (
    echo [Dano] ERROR: %~2 must be a positive integer; got "%~1".
    exit /b 1
)
exit /b 0

:validate_port
call :validate_number "%~1" "%~2"
if errorlevel 1 exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=[int64]'%~1'; if($p -le 65535){exit 0}else{exit 1}" >nul 2>&1
if errorlevel 1 (
    echo [Dano] ERROR: %~2 must be between 1 and 65535; got "%~1".
    exit /b 1
)
exit /b 0

:find_python
set "PYTHON_EXE="
set "PYTHON_ARGS="

if defined DANO_PYTHON (
    if not exist "%DANO_PYTHON%" (
        echo [Dano] ERROR: DANO_PYTHON does not exist: %DANO_PYTHON%
        exit /b 1
    )
    "%DANO_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info[0] == 3 and sys.version_info[1] in range(12, 100) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo [Dano] ERROR: DANO_PYTHON must point to Python 3.12 or newer.
        exit /b 1
    )
    set "PYTHON_EXE=%DANO_PYTHON%"
    exit /b 0
)

for %%P in ("%ROOT%.venv\Scripts\python.exe" "%BACKEND_DIR%\.venv\Scripts\python.exe") do (
    if not defined PYTHON_EXE if exist "%%~fP" (
        "%%~fP" -c "import sys; raise SystemExit(0 if sys.version_info[0] == 3 and sys.version_info[1] in range(12, 100) else 1)" >nul 2>&1
        if not errorlevel 1 set "PYTHON_EXE=%%~fP"
    )
)
if defined PYTHON_EXE exit /b 0

rem Conda fallback: conda run -n dano-backend python
set "CONDA_EXE="
for /f "delims=" %%C in ('where conda.exe 2^>nul') do if not defined CONDA_EXE set "CONDA_EXE=%%~fC"
if defined CONDA_EXE (
    "%CONDA_EXE%" run --no-capture-output -n dano-backend python -c "import sys; raise SystemExit(0 if sys.version_info[0] == 3 and sys.version_info[1] in range(12, 100) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=%CONDA_EXE%"
        set "PYTHON_ARGS=run --no-capture-output -n dano-backend python"
        exit /b 0
    )
)

for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PYTHON_EXE (
        "%%~fP" -c "import sys; raise SystemExit(0 if sys.version_info[0] == 3 and sys.version_info[1] in range(12, 100) else 1)" >nul 2>&1
        if not errorlevel 1 set "PYTHON_EXE=%%~fP"
    )
)
if defined PYTHON_EXE exit /b 0

rem Windows launcher fallback: py -3.12
where py >nul 2>&1
if not errorlevel 1 (
    py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info[0] == 3 and sys.version_info[1] in range(12, 100) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=py"
        set "PYTHON_ARGS=-3.12"
        exit /b 0
    )
)

echo [Dano] ERROR: Python 3.12+ was not found.
echo [Dano] Set DANO_PYTHON to the full path of python.exe and retry.
exit /b 1

:check_commands
where powershell >nul 2>&1
if errorlevel 1 (
    echo [Dano] ERROR: Windows PowerShell is required for health checks.
    exit /b 1
)
where node >nul 2>&1
if errorlevel 1 (
    echo [Dano] ERROR: Node.js is not installed or not on PATH.
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$v=[version](& node -p 'process.versions.node'); if($v -ge [version]'22.19.0'){exit 0}else{exit 1}" >nul 2>&1
if errorlevel 1 (
    echo [Dano] ERROR: Node.js 22.19.0 or newer is required by the Pi runtime.
    exit /b 1
)
where npm >nul 2>&1
if errorlevel 1 (
    echo [Dano] ERROR: npm is not installed or not on PATH.
    exit /b 1
)
exit /b 0

:run_python
"%PYTHON_EXE%" %PYTHON_ARGS% %*
exit /b %ERRORLEVEL%

:python_import_check
call :run_python -c "import importlib.metadata,pathlib,sys,tomllib; sys.path[:0]=[r'%PLAYWRIGHT_SRC%',r'%BACKEND_DIR%']; from packaging.requirements import Requirement; back=tomllib.loads(pathlib.Path(r'%ROOT%back\pyproject.toml').read_text(encoding='utf-8')); recording=tomllib.loads(pathlib.Path(r'%ROOT%Playwright\pyproject.toml').read_text(encoding='utf-8')); deps=[*back['project']['dependencies'],*back['project']['optional-dependencies']['page'],*recording['project']['dependencies'],*recording['project']['optional-dependencies']['browser'],'packaging>=24']; requirements=[Requirement(item) for item in deps]; assert all(requirement.specifier.contains(importlib.metadata.version(requirement.name),prereleases=True) for requirement in requirements); import yaml; import asyncpg,fastapi,httpx,pydantic_settings,playwright,structlog,uvicorn,dano_recording" >nul 2>&1
exit /b %ERRORLEVEL%

:ensure_python_dependencies
call :python_import_check
if not errorlevel 1 exit /b 0
if "%CHECK_ONLY%"=="1" (
    echo [Dano] ERROR: Python dependencies are incomplete.
    echo [Dano] Run start-dano.bat once without "check" to install them.
    exit /b 1
)
echo [Dano] Installing missing Python dependencies ...
rem Install dependency sets from back\pyproject.toml and Playwright\pyproject.toml.
rem The back directory is intentionally not installed as a package because it
rem contains multiple top-level runtime directories; PYTHONPATH loads its source.
call :run_python -c "import pathlib,subprocess,sys,tomllib; back=tomllib.loads(pathlib.Path(r'%ROOT%back\pyproject.toml').read_text(encoding='utf-8')); recording=tomllib.loads(pathlib.Path(r'%ROOT%Playwright\pyproject.toml').read_text(encoding='utf-8')); deps=[*back['project']['dependencies'],*back['project']['optional-dependencies']['page'],*recording['project']['dependencies'],*recording['project']['optional-dependencies']['browser'],'packaging>=24']; subprocess.check_call([sys.executable,'-m','pip','install','--disable-pip-version-check',*deps])"
if errorlevel 1 (
    echo [Dano] ERROR: Python dependency installation failed.
    exit /b 1
)
call :python_import_check
if errorlevel 1 (
    echo [Dano] ERROR: Python dependencies still fail their import check.
    exit /b 1
)
exit /b 0

:check_database
echo [Dano] Checking PostgreSQL connectivity ...
call :run_python -c "import sys; sys.path.insert(0,r'%BACKEND_DIR%'); import asyncio,asyncpg; from dano.config import get_settings; exec('async def m():\n c=await asyncpg.connect(get_settings().pg_dsn,timeout=5)\n await c.close()\nasyncio.run(m())')" >nul 2>&1
if errorlevel 1 (
    echo [Dano] ERROR: PostgreSQL is unavailable or DANO_PG_DSN is invalid.
    echo [Dano] Recording V3 requires its durable PostgreSQL repository.
    exit /b 1
)
exit /b 0

:check_pi_configuration
call :run_python -c "import os,sys; sys.path.insert(0,r'%BACKEND_DIR%'); from dano.config import get_settings; key=get_settings().pi_api_key.strip(); stub=os.getenv('PI_STUB') == '1'; sys.exit(0 if stub or (key and not key.startswith(chr(60))) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [Dano] ERROR: Pi API key is missing or still contains the example placeholder.
    echo [Dano] Add DANO_PI_API_KEY to back\.env, or run start-dano.bat --stub for local UI testing only.
    exit /b 1
)
exit /b 0

:chromium_check
call :run_python -c "from pathlib import Path; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); ok=Path(p.chromium.executable_path).is_file(); p.stop(); raise SystemExit(0 if ok else 1)" >nul 2>&1
exit /b %ERRORLEVEL%

:ensure_chromium
call :chromium_check
if not errorlevel 1 exit /b 0
if "%CHECK_ONLY%"=="1" (
    echo [Dano] ERROR: Playwright Chromium is not installed.
    echo [Dano] Run start-dano.bat once without "check" to install it.
    exit /b 1
)
echo [Dano] Installing Playwright Chromium ...
call :run_python -m playwright install chromium
if errorlevel 1 (
    echo [Dano] ERROR: Chromium installation failed.
    exit /b 1
)
call :chromium_check
exit /b %ERRORLEVEL%

:ensure_node_dependencies
set "NODE_PROJECT=%~1"
set "NODE_LOCK=%~2"
if not exist "%NODE_LOCK%" (
    echo [Dano] ERROR: npm lockfile is missing: %NODE_LOCK%
    exit /b 1
)
pushd "%NODE_PROJECT%" >nul
call npm ls --depth=0 >nul 2>&1
if not errorlevel 1 (
    call :node_lock_check "%NODE_LOCK%" "%NODE_PROJECT%\node_modules\.package-lock.json"
    if not errorlevel 1 (
        popd >nul
        exit /b 0
    )
)
if "%CHECK_ONLY%"=="1" (
    popd >nul
    echo [Dano] ERROR: npm dependencies are incomplete in %NODE_PROJECT%.
    echo [Dano] Run start-dano.bat once without "check" to install them.
    exit /b 1
)
echo [Dano] Synchronizing npm dependencies in %NODE_PROJECT% ...
call npm ci --no-audit --no-fund
if errorlevel 1 (
    popd >nul
    echo [Dano] ERROR: npm ci failed in %NODE_PROJECT%.
    exit /b 1
)
call npm ls --depth=0 >nul 2>&1
if errorlevel 1 (
    popd >nul
    echo [Dano] ERROR: npm dependency validation failed in %NODE_PROJECT%.
    exit /b 1
)
call :node_lock_check "%NODE_LOCK%" "%NODE_PROJECT%\node_modules\.package-lock.json"
if errorlevel 1 (
    popd >nul
    echo [Dano] ERROR: installed npm tree does not match %NODE_LOCK%.
    exit /b 1
)
popd >nul
exit /b 0

:node_lock_check
if not exist "%~2" exit /b 1
node -e "const fs=require('fs');const root=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));const installed=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));const entries=Object.entries(installed.packages||{}).filter(function(entry){return entry[0].indexOf('node_modules/')===0;});const ok=entries.every(function(entry){const wanted=(root.packages||{})[entry[0]];const current=entry[1];return wanted&&current.version===wanted.version&&(wanted.integrity==null||current.integrity===wanted.integrity)&&(wanted.resolved==null||current.resolved===wanted.resolved);});process.exit(ok?0:1);" "%~1" "%~2" >nul 2>&1
exit /b %ERRORLEVEL%

:port_in_use
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c=New-Object Net.Sockets.TcpClient; try{$c.Connect('127.0.0.1',[int]'%~1'); exit 0}catch{exit 1}finally{$c.Dispose()}" >nul 2>&1
exit /b %ERRORLEVEL%

:probe_backend
powershell -NoProfile -ExecutionPolicy Bypass -Command "try{$r=Invoke-RestMethod -UseBasicParsing -TimeoutSec 2 -Uri '%BACKEND_URL%/health'; if($r.status -eq 'ok'){exit 0}; exit 1}catch{exit 1}" >nul 2>&1
exit /b %ERRORLEVEL%

:launch_backend
call :spawn_log_window "__dano_backend" "%BACKEND_DIR%" BACKEND_PID BACKEND_START_TICKS
if errorlevel 1 (
    echo [Dano] ERROR: failed to create the backend process.
    exit /b 1
)
set "BACKEND_STARTED=1"
exit /b 0

:launch_frontend
call :spawn_log_window "__dano_frontend" "%FRONTEND_DIR%" FRONTEND_PID FRONTEND_START_TICKS
if errorlevel 1 (
    echo [Dano] ERROR: failed to create the frontend process.
    exit /b 1
)
set "FRONTEND_STARTED=1"
exit /b 0

:spawn_log_window
set "DANO_SELF=%~f0"
set "DANO_CHILD_MODE=%~1"
set "SPAWN_PID="
set "SPAWN_TICKS="
for /f "tokens=1,2 delims=," %%A in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop';$launcherDir=[IO.Path]::GetDirectoryName($env:DANO_SELF);$command='call start-dano.bat {0}' -f $env:DANO_CHILD_MODE;$p=Start-Process -FilePath $env:ComSpec -ArgumentList @('/d','/k',$command) -WorkingDirectory $launcherDir -WindowStyle Normal -PassThru;[Console]::Out.WriteLine(('{0},{1}' -f $p.Id,$p.StartTime.ToUniversalTime().Ticks))"') do (
    set "SPAWN_PID=%%A"
    set "SPAWN_TICKS=%%B"
)
if not defined SPAWN_PID exit /b 1
if not defined SPAWN_TICKS exit /b 1
set "%~3=%SPAWN_PID%"
set "%~4=%SPAWN_TICKS%"
exit /b 0

:child_backend
title Dano Backend %BACKEND_PORT%
pushd "%BACKEND_DIR%" || exit /b 1
call :run_python -m uvicorn dano.gateway.app:app --host 127.0.0.1 --port %BACKEND_PORT% --lifespan on
set "CHILD_RC=%ERRORLEVEL%"
popd
echo [Dano] Backend exited with code %CHILD_RC%.
exit /b %CHILD_RC%

:child_frontend
title Dano Frontend %FRONTEND_PORT%
pushd "%FRONTEND_DIR%" || exit /b 1
call npm.cmd run dev -- --host 127.0.0.1 --port %FRONTEND_PORT% --strictPort
set "CHILD_RC=%ERRORLEVEL%"
popd
echo [Dano] Frontend exited with code %CHILD_RC%.
exit /b %CHILD_RC%

:probe_recording
powershell -NoProfile -ExecutionPolicy Bypass -Command "try{$r=Invoke-RestMethod -UseBasicParsing -TimeoutSec 2 -Uri '%BACKEND_URL%/recording-v3/health'; if($r.status -eq 'ready' -and $r.ready -eq $true){exit 0}; exit 1}catch{exit 1}" >nul 2>&1
exit /b %ERRORLEVEL%

:probe_backend_ready
call :probe_backend
if errorlevel 1 exit /b 1
call :probe_recording
exit /b %ERRORLEVEL%

:probe_frontend
powershell -NoProfile -ExecutionPolicy Bypass -Command "try{$r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri '%FRONTEND_PAGE%'; if($r.StatusCode -eq 200 -and $r.Content -match 'Dano'){exit 0}; exit 1}catch{exit 1}" >nul 2>&1
exit /b %ERRORLEVEL%

:check_service_ports
call :port_in_use "%BACKEND_PORT%"
if not errorlevel 1 (
    call :probe_backend_ready
    if not errorlevel 1 if "%DANO_REUSE_SERVICES%"=="1" (
        echo [Dano] Explicitly reusing the healthy backend on port %BACKEND_PORT%.
    ) else (
        echo [Dano] ERROR: port %BACKEND_PORT% is occupied; refusing to reuse it by default.
        echo [Dano] Stop that process, choose DANO_BACKEND_PORT, or set DANO_REUSE_SERVICES=1.
        exit /b 1
    )
    call :probe_backend_ready
    if errorlevel 1 (
        echo [Dano] ERROR: port %BACKEND_PORT% is occupied by a non-Dano backend process.
        exit /b 1
    )
)
call :port_in_use "%FRONTEND_PORT%"
if not errorlevel 1 (
    call :probe_frontend
    if not errorlevel 1 if "%DANO_REUSE_SERVICES%"=="1" (
        echo [Dano] Explicitly reusing the healthy frontend on port %FRONTEND_PORT%.
    ) else (
        echo [Dano] ERROR: port %FRONTEND_PORT% is occupied; refusing to reuse it by default.
        echo [Dano] Stop that process, choose DANO_FRONTEND_PORT, or set DANO_REUSE_SERVICES=1.
        exit /b 1
    )
    call :probe_frontend
    if errorlevel 1 (
        echo [Dano] ERROR: port %FRONTEND_PORT% is occupied by another process.
        exit /b 1
    )
)
exit /b 0

:wait_for_backend
for /L %%I in (1,1,%START_TIMEOUT%) do (
    call :probe_backend_ready
    if not errorlevel 1 (
        echo [Dano] Recording V3 is ready.
        exit /b 0
    )
    ping.exe -n 2 127.0.0.1 >nul 2>&1
)
echo [Dano] ERROR: backend did not become healthy within %START_TIMEOUT% seconds.
exit /b 1

:wait_for_frontend
for /L %%I in (1,1,%START_TIMEOUT%) do (
    call :probe_frontend
    if not errorlevel 1 (
        echo [Dano] Frontend is ready.
        exit /b 0
    )
    ping.exe -n 2 127.0.0.1 >nul 2>&1
)
echo [Dano] ERROR: frontend did not become ready within %START_TIMEOUT% seconds.
exit /b 1

:failed
set "FAIL_CODE=%ERRORLEVEL%"
if "%FAIL_CODE%"=="0" set "FAIL_CODE=1"
call :cleanup_started_services
echo.
echo [Dano] STARTUP FAILED. Review the error above; no browser was opened.
if not "%DANO_NO_PAUSE%"=="1" pause
exit /b %FAIL_CODE%

:cleanup_started_services
if "%FRONTEND_STARTED%"=="1" if defined FRONTEND_PID (
    echo [Dano] Cleaning up frontend process %FRONTEND_PID% ...
    call :kill_owned_tree "%FRONTEND_PID%" "%FRONTEND_START_TICKS%"
    set "FRONTEND_STARTED=0"
)
if "%BACKEND_STARTED%"=="1" if defined BACKEND_PID (
    echo [Dano] Cleaning up backend process %BACKEND_PID% ...
    call :kill_owned_tree "%BACKEND_PID%" "%BACKEND_START_TICKS%"
    set "BACKEND_STARTED=0"
)
exit /b 0

:kill_owned_tree
echo(%~1| findstr /r "^[1-9][0-9]*$" >nul || exit /b 0
echo(%~2| findstr /r "^[1-9][0-9]*$" >nul || exit /b 0
set "DANO_KILL_PID=%~1"
set "DANO_KILL_TICKS=%~2"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=Get-Process -Id ([int]$env:DANO_KILL_PID) -ErrorAction SilentlyContinue;if($null -eq $p){exit 1};try{if($p.StartTime.ToUniversalTime().Ticks -eq [int64]$env:DANO_KILL_TICKS){exit 0}}catch{};exit 1" >nul 2>&1
if errorlevel 1 exit /b 0
taskkill /PID %~1 /T /F >nul 2>&1
exit /b 0
