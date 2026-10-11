@echo off
setlocal
cd /d "%~dp0"
title Local Web-Search Agent
call "venv\Scripts\activate.bat"
python agent.py
endlocal
