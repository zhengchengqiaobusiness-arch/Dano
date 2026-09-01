@echo off
setlocal EnableExtensions DisableDelayedExpansion

cd /d "%~dp0"
title Pi Business Skill Studio

rem Load existing project configuration. This script does not install dependencies.
if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
    if not "%%A"=="" set "%%A=%%B"
  )
)

if not exist ".\node_modules\.bin\pi.cmd" (
  echo [ERROR] Pi is not installed in this project.
  pause
  endlocal & exit /b 1
)

if defined PI_API_KEY if defined PI_BASE_URL if defined PI_MODEL (
  call ".\node_modules\.bin\pi.cmd" --provider xiaomi-token-plan-cn --model "%PI_MODEL%" %*
) else if defined OPENAI_API_KEY (
  if not defined OPENAI_MODEL set "OPENAI_MODEL=gpt-5.5"
  call ".\node_modules\.bin\pi.cmd" --provider openai --model "%OPENAI_MODEL%" %*
) else (
  call ".\node_modules\.bin\pi.cmd" %*
)

set "APP_EXIT_CODE=%ERRORLEVEL%"
if not "%APP_EXIT_CODE%"=="0" pause
endlocal & exit /b %APP_EXIT_CODE%
