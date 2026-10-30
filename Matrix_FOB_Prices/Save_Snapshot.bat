@echo off
REM Record today's matrix (and Manual_Inputs entries) in data_drop\history\*.parquet.
REM Re-running on the same as-of date replaces that day's rows. Then rebuild the weekly history so the
REM Historical_Charts tab uses recorded (not pro-forma) values for this week. Scripts live in .\src
setlocal
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
python src\save_snapshot.py || goto :fail
python src\run_historical_drop.py || goto :fail
python src\excel_com.py || goto :fail
echo.
echo Snapshot saved and charts refreshed.
pause
exit /b 0
:fail
echo.
echo FAILED - read the messages above.
pause
exit /b 1
