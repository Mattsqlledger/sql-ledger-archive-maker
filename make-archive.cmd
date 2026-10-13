@echo off
rem SQL-Ledger Archive Maker - double-click this file (Windows).
cd /d "%~dp0"
where py >nul 2>&1 && (py make_archive.py) || (
  where python >nul 2>&1 && (python make_archive.py) || (
    echo Python is not installed yet. Download: https://www.python.org/downloads/
    echo During installation, tick "Add python.exe to PATH".
  )
)
pause
