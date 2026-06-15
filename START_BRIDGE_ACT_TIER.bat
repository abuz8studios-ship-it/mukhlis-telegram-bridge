@echo off
rem ============================================================
rem  FULL-HANDS MOBILE AGENT  -  Telegram <-> Claude (Qadir)
rem  Double-click THIS to give @QadirCoreM1_bot real hands:
rem  read/write/edit files, run shell, + the qadir MCP toolset.
rem  By launching this yourself you are authorizing the act tier.
rem  Hard rails still apply (no money/email/public-post/delete
rem  without your explicit in-chat confirmation).
rem  Auto-restarts if it exits. Close this window to stop.
rem ============================================================
title ABUZ8 Telegram Bridge - ACT TIER (full hands)
set PYTHONIOENCODING=utf-8
set BRIDGE_TIER=act
:loop
"C:\Program Files\Python311\python.exe" "E:\ABU\QADIR_CORE\bridge\telegram_claude\qadir_telegram_claude_bridge.py"
echo bridge exited - restarting in 5s...  (Ctrl+C to stop)
timeout /t 5 /nobreak >nul
goto loop
