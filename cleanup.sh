#!/bin/bash

echo "========================================"
echo "CLEANUP SCRIPT FOR DASHBOARD PROJECT"
echo "========================================"
echo ""

echo "Step 1: Killing any Vite dev servers..."
# Kill all node processes that might be running vite
pkill -f "vite" 2>/dev/null
pkill -f "npm run dev" 2>/dev/null
echo "  Done."

echo "Step 2: Waiting for processes to close..."
sleep 3
echo "  Done."

echo "Step 3: Removing old dashboard directory..."
cd "/c/Users/aloys/OneDrive/Documents/GitHub/noble-trader-agent"
rm -rf dashboard
echo "  Done."

echo "Step 4: Verifying removal..."
if [ -d "dashboard" ]; then
    echo "  ERROR: Dashboard directory still exists!"
    echo "  Please close any terminal windows with 'npm run dev' and try again."
else
    echo "  SUCCESS: Dashboard directory removed!"
fi
echo ""

echo "========================================"
echo "Cleanup complete!"
echo "Your Next.js dashboard is in:"
echo "/c/Users/aloys/OneDrive/Documents/GitHub/noble-trader-agent/noble-trader-dashboard-nextjs/"
echo "========================================"
