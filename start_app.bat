@echo off
REM Start Flask app in the background
start /min cmd /c "python app.py"
REM Wait a few seconds for the server to start (optional)
timeout /t 3 >nul
REM Open Chrome to the app URL
start chrome http://127.0.0.1:5000