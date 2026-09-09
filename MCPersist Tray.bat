@echo off
rem Uses the portable Python at %LOCALAPPDATA%\Programs\PythonEmbedded (see README).
rem If you have a normal Python install on PATH instead, replace the line below with:
rem   start "" pythonw -m mcpersist.tray_app
cd /d "%~dp0"
start "" "%LOCALAPPDATA%\Programs\PythonEmbedded\pythonw.exe" -m mcpersist.tray_app
