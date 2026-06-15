@echo off
rem ============================================================
rem  RESTART the Telegram bridge into ACT tier with latest code.
rem  Safe to run any time (from desktop, or spawned detached by
rem  Qadir from a bridge session AFTER the owner confirms in-chat).
rem  1. waits 45s (lets any in-flight reply reach Telegram)
rem  2. kills the running bridge python (by script match, not port)
rem  3. relaunches via START_BRIDGE_ACT_TIER.bat (auto-restart loop)
rem ============================================================
rem ping-sleep, NOT timeout: timeout dies instantly in console-less context
rem (caused a wrapper tight-loop + lock-spam on first use 2026-06-11)
ping -n 46 127.0.0.1 >nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'qadir_telegram_claude_bridge' -and $_.Name -match 'python' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
ping -n 4 127.0.0.1 >nul
start "" /min cmd /c "E:\ABU\QADIR_CORE\bridge\telegram_claude\START_BRIDGE_ACT_TIER.bat"
echo restart issued %date% %time% >> "E:\ABU\QADIR_CORE\bridge\telegram_claude\bridge.log"
