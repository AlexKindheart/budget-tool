@echo off
rem Builds Budget Tool, installs it to %LOCALAPPDATA%\Programs\BudgetTool,
rem and adds Desktop + Start Menu shortcuts. Your data (in %APPDATA%\BudgetTool) is not touched.
cd /d "%~dp0"
set "INSTALL=%LOCALAPPDATA%\Programs\BudgetTool"

tasklist /FI "IMAGENAME eq BudgetTool.exe" | find /I "BudgetTool.exe" >nul && (
  echo Budget Tool is open. Close it first, then run this again.
  pause
  exit /b 1
)

echo Installing build tools...
py -m pip install -q -r requirements.txt || goto :fail

echo Building Budget Tool (takes a minute)...
py -m PyInstaller --noconfirm --clean --windowed --name BudgetTool ^
  --add-data "templates;templates" --add-data "static;static" ^
  budget_tool.py || goto :fail

echo Installing to %INSTALL%...
if exist "%INSTALL%" rmdir /s /q "%INSTALL%"
robocopy "dist\BudgetTool" "%INSTALL%" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :fail

rem Remove PyInstaller's work folders so there's no half-built exe lying around.
rmdir /s /q build
rmdir /s /q dist
del /q BudgetTool.spec 2>nul

echo Creating shortcuts...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$exe = Join-Path $env:LOCALAPPDATA 'Programs\BudgetTool\BudgetTool.exe';" ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {" ^
  "  $s = $ws.CreateShortcut((Join-Path $dir 'Budget Tool.lnk')); $s.TargetPath = $exe;" ^
  "  $s.WorkingDirectory = Split-Path $exe; $s.Description = 'Budget Tool'; $s.Save() }"

echo.
echo Done! Open "Budget Tool" from your Desktop or Start Menu.
pause
exit /b 0

:fail
echo Build failed - see the messages above.
pause
exit /b 1
