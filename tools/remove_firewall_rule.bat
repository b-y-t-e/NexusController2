@echo off
NET SESSION >nul 2>&1
if errorlevel 1 (
    echo This script must be run as Administrator.
    pause
    exit /b 1
)
netsh advfirewall firewall delete rule name="NexusController TCP"
netsh advfirewall firewall delete rule name="NexusController Discovery"
echo Firewall rules removed.
pause
