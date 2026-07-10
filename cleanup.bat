@echo off
echo ========================================
echo CLEANUP SCRIPT FOR DASHBOARD PROJECT
echo ========================================
echo.

echo Step 1: Killing any Vite dev servers...
taskkill /F /IM node.exe 2>nul
echo.  Done.

echo Step 2: Waiting for processes to close...
timeout /t 3 /nobreak >nul
echo.  Done.

echo Step 3: Removing old dashboard directory...
cd /d "C:\Users\aloys\OneDrive\Documents\GitHub\noble-trader-agent"
rmdir /s /q dashboard
echo.  Done.

echo Step 4: Verifying removal...
if exist "dashboard" (
    echo.  ERROR: Dashboard directory still exists!
    echo  Please close any terminal windows with "npm run dev" and try again.
) else (
    echo.  SUCCESS: Dashboard directory removed!
)
echo.

echo ========================================
echo Cleanup complete!
echo Your Next.js dashboard is in:
echo C:\Users\aloys\OneDrive\Documents\GitHub\noble-trader-agent\noble-trader-dashboard-nextjs\
echo ========================================
pause
