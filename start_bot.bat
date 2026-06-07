@echo off
cd /d C:\Users\crist\polymarket-bot

:loop
call venv\Scripts\activate.bat
python bot.py
set EXIT_CODE=%ERRORLEVEL%

if %EXIT_CODE%==2 (
    echo.
    echo Ya hay una instancia corriendo. Cerrando esta ventana...
    timeout /t 3 /nobreak >nul
    exit
)

echo.
echo Bot reiniciando en 10 segundos... (Ctrl+C para parar)
timeout /t 10 /nobreak
echo.
goto loop
