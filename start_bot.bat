@echo off
cd /d C:\Users\crist\polymarket-bot
:loop
call venv\Scripts\activate.bat
python bot.py
echo.
echo Bot reiniciando en 10 segundos... (Ctrl+C para parar)
timeout /t 10 /nobreak
echo.
goto loop
