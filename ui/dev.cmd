@echo off
rem One command: start the Canvas Coach UI and open the browser.
set "PATH=C:\Program Files\nodejs;%PATH%"
cd /d "%~dp0"
start /b node feed-proxy.mjs
start /b cmd /c "timeout /t 8 >nul & start http://localhost:3117"
npm run dev
