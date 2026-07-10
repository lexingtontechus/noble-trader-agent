@echo off
echo Killing existing backend process...
netstat -ano | findstr :8080 | findstr LISTENING
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8080 ^| findstr LISTENING') do (
    echo Killing process %%a
    taskkill /F /PID %%a
)
timeout /t 2 /nobreak > nul
echo Starting new backend...
python start_backend.py
pause