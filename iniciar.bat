@echo off
cd /d %~dp0
python -m pip install -q openpyxl fastapi uvicorn
start "" http://localhost:8000
python server.py
