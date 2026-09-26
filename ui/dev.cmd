@echo off
rem One command: start the HULMS UI and open the browser.
set "PATH=C:\Program Files\nodejs;%PATH%"
cd /d "%~dp0"
start /b cmd /c "timeout /t 8 >nul & start http://localhost:3117"
npm run dev
