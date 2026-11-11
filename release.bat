@echo off
rem Publishes a new version: tags the current commit with the number in VERSION and pushes it.
rem GitHub then builds the app and creates the release (see .github\workflows\release.yml),
rem and everyone's Budget Tool offers the update within a few hours.
cd /d "%~dp0"
set /p VER=<VERSION
for /f "tokens=* delims= " %%a in ("%VER%") do set VER=%%a
git diff --quiet && git diff --cached --quiet || (
  echo You have uncommitted changes. Commit them first.
  pause & exit /b 1
)
git rev-parse "v%VER%" >nul 2>&1 && (
  echo v%VER% already exists. Bump the number in VERSION, commit, and run this again.
  pause & exit /b 1
)
echo Releasing v%VER%...
git tag -a "v%VER%" -m "Budget Tool v%VER%" || goto :fail
git push origin HEAD "v%VER%" || goto :fail
echo.
echo Pushed v%VER%. GitHub is building it now: https://github.com/AlexKindheart/budget-tool/actions
pause
exit /b 0
:fail
echo Release failed - see the messages above.
pause
exit /b 1
