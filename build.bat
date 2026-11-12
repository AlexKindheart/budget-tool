@echo off
rem Builds Budget Tool into a portable folder: %LOCALAPPDATA%\Programs\BudgetTool, or the folder you pass
rem (e.g.  build.bat E:\BudgetTool  for a USB drive). Your "data" folder there is never touched.
rem On this PC it also adds Desktop + Start Menu shortcuts. Needs Python 3.11 (py -3.11).
cd /d "%~dp0"
set "INSTALL=%LOCALAPPDATA%\Programs\BudgetTool"
if not "%~1"=="" set "INSTALL=%~1"

py -3.11 build.py "%INSTALL%" || goto :fail

echo Creating shortcuts on this PC...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$exe = Join-Path $env:INSTALL 'BudgetTool.exe';" ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {" ^
  "  $s = $ws.CreateShortcut((Join-Path $dir 'Budget Tool.lnk')); $s.TargetPath = $exe;" ^
  "  $s.WorkingDirectory = Split-Path $exe; $s.Description = 'Budget Tool'; $s.Save() }"

echo.
echo Done! Budget Tool is in %INSTALL%
echo That folder is portable: copy the whole folder to a USB drive and run BudgetTool.exe from there.
echo To update later, run "Update Budget Tool" in that folder (or use the banner inside Budget Tool).
pause
exit /b 0

:fail
echo Build failed - see the messages above.
pause
exit /b 1
