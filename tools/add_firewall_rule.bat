@echo off
:: Opens the Nexus Controller ports on PRIVATE networks only.
:: Run once, as Administrator. The server can also do this itself when elevated.
NET SESSION >nul 2>&1
if errorlevel 1 (
    echo This script must be run as Administrator.
    echo Right-click it and choose "Run as administrator".
    pause
    exit /b 1
)

netsh advfirewall firewall delete rule name="NexusController TCP" >nul 2>&1
netsh advfirewall firewall delete rule name="NexusController Discovery" >nul 2>&1
netsh advfirewall firewall add rule name="NexusController TCP" dir=in action=allow protocol=TCP localport=6000 profile=private
netsh advfirewall firewall add rule name="NexusController Discovery" dir=in action=allow protocol=UDP localport=6001 profile=private

echo.
echo Done. Ports 6000/TCP and 6001/UDP are open on private networks.
pause
