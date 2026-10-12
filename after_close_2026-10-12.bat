@echo off
rem Meridian Desk - after-close run for session 2026-10-12.
rem NSE closing price is determined at 15:40 IST; this runs at 15:45.
cd /d D:\Projects\meridian-desk\meridian-desk
set PYTHONPATH=src
(
  echo === after-close run started %date% %time% ===
  python -m meridian.run outcomes --session 2026-10-12 --provider live
  echo.
  echo === report ===
  python -m meridian.run report
  echo.
  echo === done %date% %time% ===
) >> data\after_close_2026-10-12.log 2>&1
