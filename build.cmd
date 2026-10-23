@echo off
rem Builds dist\PepeMonitor.exe and installer_output\PepeMonitorSetup-*.exe
cd /d "%~dp0"
python make_icon.py || exit /b 1
python -m PyInstaller --noconfirm --onefile --windowed --name PepeMonitor --icon frog.ico ^
  --add-data "frog.ico;." --add-data "frog.png;." pepe_monitor.py || exit /b 1
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss || exit /b 1
