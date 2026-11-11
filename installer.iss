; Inno Setup script for BudgetTool-Setup.exe. Built by build-installer.bat (and the release workflow),
; which pass the version in: ISCC /DAppVersion=1.2.3 installer.iss
; Installs for the current user only (no admin needed) to %LOCALAPPDATA%\Programs\BudgetTool, the same
; place build.bat uses, so the in-app updater works the same either way.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; Never change AppId: Windows uses it to recognize upgrades and the uninstaller.
AppId={{CCEBBAD2-57A2-4B54-92CB-7C7F31A7F483}
AppName=Budget Tool
AppVersion={#AppVersion}
AppVerName=Budget Tool {#AppVersion}
AppPublisher=AlexKindheart
AppPublisherURL=https://github.com/AlexKindheart/budget-tool
DefaultDirName={localappdata}\Programs\BudgetTool
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=release
OutputBaseFilename=BudgetTool-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\BudgetTool.exe
UninstallDisplayName=Budget Tool
CloseApplications=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[InstallDelete]
; Start clean so files from an older version don't linger.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\BudgetTool\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "Create a Desktop shortcut"; GroupDescription: "Shortcuts:"

[Icons]
Name: "{userprograms}\Budget Tool"; Filename: "{app}\BudgetTool.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\Budget Tool"; Filename: "{app}\BudgetTool.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\BudgetTool.exe"; Description: "Open Budget Tool now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Messages]
FinishedLabel=Budget Tool is installed. Open it from the Start Menu or your Desktop.%n%nYour budget data is kept in %APPDATA%\BudgetTool and is not removed if you uninstall.

[Code]
// The app's window uses Microsoft Edge WebView2, which comes with Windows 11 and almost every Windows 10 PC.
// If it's missing, offer to open Microsoft's download page instead of leaving a blank window.
function WebView2Installed(): Boolean;
var
  Version: String;
  Key: String;
begin
  Key := 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result :=
    (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\' + Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM, 'SOFTWARE\' + Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, 'Software\' + Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
begin
  if (CurStep = ssPostInstall) and not WebView2Installed() then
  begin
    if SuppressibleMsgBox('Budget Tool needs the Microsoft Edge WebView2 Runtime, which isn''t on this PC.' + #13#10#13#10 +
              'Open Microsoft''s download page now? Install the "Evergreen Bootstrapper", then open Budget Tool.',
              mbConfirmation, MB_YESNO, IDNO) = IDYES then
      ShellExec('open', 'https://developer.microsoft.com/microsoft-edge/webview2/', '', '', SW_SHOWNORMAL, ewNoWait, Code);
  end;
end;
