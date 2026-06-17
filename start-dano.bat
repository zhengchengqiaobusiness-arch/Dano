@echo off
title Dano Launcher
setlocal
set "ROOT=%~dp0"
set "PY=E:\python\condaEnv\dano-backend\python.exe"
set "PORT=8077"

echo Starting backend on port %PORT% (conda env python) ...
start "Dano Backend %PORT%" cmd /k "cd /d %ROOT%back && %PY% -m uvicorn dano.gateway.app:app --host 127.0.0.1 --port %PORT%"

echo Starting frontend on port 5173 ...
start "Dano Frontend 5173" cmd /k "cd /d %ROOT%skillfrontend && (if not exist node_modules npm install) && npm run dev"

timeout /t 8 >nul
start "" http://localhost:5173
echo.
echo Backend  http://127.0.0.1:%PORT%
echo Frontend http://localhost:5173
echo First time: open frontend -> Settings -> enter model API key -> Save -> Onboard.
echo (You can close THIS window; services run in the other two.)
pause
