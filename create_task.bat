@echo off
schtasks /Create /TN "Meridian-AfterClose-20261012" /TR "D:\Projects\meridian-desk\meridian-desk\after_close_2026-10-12.bat" /SC ONCE /ST 15:45 /SD 12/10/2026 /F
