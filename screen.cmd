@echo off
if exist "%~dp0.runtime\Scripts\python.exe" (
    "%~dp0.runtime\Scripts\python.exe" "%~dp0screen.py" %*
) else (
    python "%~dp0screen.py" %*
)
