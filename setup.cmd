@echo off
rem =====================================================================
rem  voice-activate-claude — shortcut setup launcher
rem
rem  HOW TO USE: just double-click this file in Explorer.
rem  It runs scripts\create-shortcut.ps1 and creates
rem  "Voice Activate Claude.lnk" in this repository folder.
rem  Double-click that .lnk to start the app windowless.
rem
rem  (.ps1 files can't be double-clicked directly on Windows, so this
rem   .cmd is the double-clickable entry point.)
rem =====================================================================
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\create-shortcut.ps1"
echo.
echo 完了したら、このフォルダの「Voice Activate Claude」をダブルクリックで起動できます。
pause
