@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo  Starting Talker (console mode - startup errors show here)
echo  Tray icon appears when ready. Quit from tray to stop.
echo ============================================================
echo.
python main.py
echo.
echo ------------------------------------------------------------
echo  Talker exited. If there is a traceback above, copy it here.
echo ------------------------------------------------------------
pause
