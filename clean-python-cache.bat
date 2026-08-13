@echo off
setlocal
pushd "%~dp0"

echo Cleaning Python caches...

for /d /r %%D in (__pycache__ .pytest_cache .mypy_cache .ruff_cache) do (
    if exist "%%D" rd /s /q "%%D"
)

del /s /q "*.pyc" "*.pyo" >nul 2>&1

echo Done.
popd
if /i not "%~1"=="nopause" pause
