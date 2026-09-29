@echo off
chcp 437 >nul

set TASK_NAME=SKSquare_FactorLog
rem 시스템 python은 .venv에 없는 패키지(dotenv 등) 때문에 바로 실패한다.
rem 반드시 이 프로젝트 .venv의 python.exe를 절대경로로 지정할 것 (2026-09-29 확인).
set PYTHON=C:\Users\3100041\Desktop\sk-square\daily-price-explainer\.venv\Scripts\python.exe
set SCRIPT=C:\Users\3100041\Desktop\sk-square\daily-price-explainer\main.py
set WORKDIR=C:\Users\3100041\Desktop\sk-square\daily-price-explainer

echo [1] Removing existing task if any...
schtasks /delete /tn "%TASK_NAME%" /f 2>nul

echo [2] Registering scheduled task...
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "\"%PYTHON%\" \"%SCRIPT%\" log" ^
  /sc WEEKLY ^
  /d MON,TUE,WED,THU,FRI ^
  /st 16:35 ^
  /sd 2026/06/13 ^
  /rl LIMITED ^
  /f

if %ERRORLEVEL% == 0 (
    echo.
    echo SUCCESS: Task "%TASK_NAME%" registered.
    echo Runs every weekday at 16:35 KST.
    echo.
    echo To test now:
    echo   schtasks /run /tn "%TASK_NAME%"
    echo.
    echo To verify:
    echo   schtasks /query /tn "%TASK_NAME%" /fo LIST
    echo.
    echo To delete:
    echo   schtasks /delete /tn "%TASK_NAME%" /f
) else (
    echo.
    echo ERROR: Registration failed.
)
pause
