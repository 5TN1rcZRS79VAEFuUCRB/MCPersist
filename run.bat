@echo off
rem Uses the portable Python at %LOCALAPPDATA%\Programs\PythonEmbedded (see README).
rem If you have a normal Python install on PATH instead, replace the line below with:
rem   python -m mcpersist %*
cd /d "%~dp0"
"%LOCALAPPDATA%\Programs\PythonEmbedded\python.exe" -m mcpersist %*
