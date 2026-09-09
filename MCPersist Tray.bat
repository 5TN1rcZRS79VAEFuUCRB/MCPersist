@echo off
cd /d "%~dp0"
start "" "%LOCALAPPDATA%\Programs\PythonEmbedded\pythonw.exe" -m mcpersist.tray_app
