@echo off
rem Builds release\BudgetTool-Setup.exe, an installer you can send to someone else.
rem Needs Python 3.11 and Inno Setup 6 (free: winget install JRSoftware.InnoSetup).
cd /d "%~dp0"

set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup 6 isn't installed. Install it with:  winget install JRSoftware.InnoSetup
  pause
  exit /b 1
)
set /p VER=<VERSION

py -3.11 build.py --stage-only || goto :fail

rem Code signing (optional): with a code-signing certificate set up in signing.bat (copy signing.example.bat; it's
rem never committed), the two launchers and the installer are signed, so Windows doesn't warn "Windows protected your PC".
set "SIGN_CMD="
if exist signing.bat call signing.bat
if defined SIGN_CMD (
  echo Signing the launchers...
  for %%F in ("dist\BudgetTool\BudgetTool.exe" "dist\BudgetTool\Update Budget Tool.exe") do (
    %SIGN_CMD% %%F || goto :fail
  )
  echo Packing and signing the installer...
  "%ISCC%" /Q /DAppVersion=%VER% /DSIGN=1 "/Sbudgettool=%SIGN_CMD% $f" installer.iss || goto :fail
) else (
  echo Packing the installer ^(not code-signed: see signing.example.bat^)...
  "%ISCC%" /Q /DAppVersion=%VER% installer.iss || goto :fail
)
rmdir /s /q dist

echo.
echo Done! Send this file:  %~dp0release\BudgetTool-Setup.exe
pause
exit /b 0

:fail
echo Build failed - see the messages above.
pause
exit /b 1
