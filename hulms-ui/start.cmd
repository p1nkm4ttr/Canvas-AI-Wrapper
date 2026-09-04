@echo off
rem Production launcher: builds once (or when asked), then serves. Much
rem snappier than dev.cmd -- no HMR, no on-demand compilation, no React
rem dev-mode double rendering. Use dev.cmd only when changing UI code.
set "PATH=C:\Program Files\nodejs;%PATH%"
cd /d "%~dp0"
if "%~1"=="--build" goto build
if not exist ".next\BUILD_ID" goto build
goto serve
:build
echo Building production bundle...
call npm run build
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)
:serve
start /b cmd /c "timeout /t 4 >nul & start http://localhost:3117"
call npm start
