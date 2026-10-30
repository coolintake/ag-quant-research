@echo off
REM Refresh Matrix_FOB_Calculator.xlsx: data drops -> Excel Refresh All + save -> independent verification.
REM If the workbook is open in Excel, it is refreshed and saved in place. Scripts live in .\src
setlocal
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
echo [1/3] Weekly history drop (history_chart.csv)...
python src\run_historical_drop.py || goto :fail
echo [2/3] Daily drop (pdq_latest.csv, fx.csv, Hammersmith world_*.csv) + Excel Refresh All + save...
python src\run_pull.py --refresh-excel || goto :fail
echo [3/3] Verifying workbook against an independent Python recompute...
python src\verify.py || goto :fail
echo.
echo Done. Open Matrix_FOB_Calculator.xlsx (Control tab shows data dates and checks).
pause
exit /b 0
:fail
echo.
echo FAILED - read the messages above. Nothing was recorded to history.
pause
exit /b 1
