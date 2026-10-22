@echo off
cd /d "%~dp0"
py demo\simulate_device.py --scenario both --seconds 120 --records 6
pause
