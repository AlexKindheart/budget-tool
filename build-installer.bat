@echo off
rem Builds release\BudgetTool-Setup.exe, an installer you can send to someone else.
rem Needs Python and Inno Setup 6 (free: winget install JRSoftware.InnoSetup).
cd /d "%~dp0"

set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup 6 isn't installed. Install it with:  winget install JRSoftware.InnoSetup
  pause
  exit /b 1
)
set /p VER=<VERSION

echo Installing build tools...
py -m pip install -q -r requirements.txt || goto :fail

echo Building Budget Tool %VER% (takes a minute)...
py -m PyInstaller --noconfirm --clean --windowed --name BudgetTool ^
  --add-data "templates;templates" --add-data "static;static" --add-data "VERSION;." ^
  budget_tool.py || goto :fail

echo Packing the installer...
"%ISCC%" /Q /DAppVersion=%VER% installer.iss || goto :fail

rmdir /s /q build
rmdir /s /q dist
del /q BudgetTool.spec 2>nul

echo.
echo Done! Send this file:  %~dp0release\BudgetTool-Setup.exe
pause
exit /b 0

:fail
echo Build failed - see the messages above.
pause
exit /b 1
