@echo off
rem ============================================================
rem  Bind virtual device IPs (192.168.4.100 ~ 129, 30 IPs)
rem  Run as Administrator
rem  If your WiFi adapter name is not "WLAN", change IFACE below
rem ============================================================

set IFACE=WLAN

echo Binding 30 IPs to adapter %IFACE% ...
echo.
for /L %%i in (100,1,129) do (
    netsh interface ipv4 add address "%IFACE%" 192.168.4.%%i 255.255.255.0 >nul 2>&1
)

echo.
echo ============ Done. Current 192.168.4.x IPs ============
ipconfig | findstr "192.168.4"
echo.
pause
