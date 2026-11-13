; Inno Setup script for BudgetTool-Setup.exe. Built by build-installer.bat (and the release workflow),
; which pass the version in: ISCC /DAppVersion=1.2.3 installer.iss
;
; Contents come from dist\BudgetTool, made by `py build.py --stage-only` (build-installer.bat runs it):
; BudgetTool.exe, "Update Budget Tool.exe", runtime\ (private Python + libraries) and app\ (the code).
; Updates later come straight from new commits on GitHub through "Update Budget Tool.exe".
;
; Portable: everything goes into ONE folder you pick (a USB drive works). The program and your data ("data"
; folder, made on first start) live there together. No registry entries, no uninstaller, nothing in AppData;
; a Desktop shortcut only if you tick it. To remove Budget Tool, delete the folder.
; Installing again into the same folder updates the program and keeps your data.
; Silent install:  BudgetTool-Setup.exe /VERYSILENT /DIR="E:\BudgetTool"

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{CCEBBAD2-57A2-4B54-92CB-7C7F31A7F483}
AppName=Budget Tool
AppVersion={#AppVersion}
AppVerName=Budget Tool {#AppVersion}
AppPublisher=AlexKindheart
AppPublisherURL=https://github.com/AlexKindheart/budget-tool
DefaultDirName={localappdata}\Programs\BudgetTool
DisableDirPage=no
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
Uninstallable=no
; Don't reuse choices from older, installed versions (e.g. a ticked Desktop shortcut): start fresh each time.
UsePreviousTasks=no
OutputDir=release
OutputBaseFilename=BudgetTool-Setup
#ifdef SIGN
; build-installer.bat passes the signing command from signing.bat (see signing.example.bat)
SignTool=budgettool
SignedUninstaller=no
#endif
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=static\icon.ico
CloseApplications=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[InstallDelete]
; Replace the program cleanly so files from an older version don't linger. The data folder is never touched.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\app.previous"
Type: filesandordirs; Name: "{app}\.update"

[Files]
Source: "dist\BudgetTool\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "Create a Desktop shortcut on this PC"; GroupDescription: "Shortcut:"; Flags: unchecked

[Icons]
Name: "{userdesktop}\Budget Tool"; Filename: "{app}\BudgetTool.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\BudgetTool.exe"; Description: "Open Budget Tool now"; Flags: nowait postinstall skipifsilent

[Messages]
SelectDirLabel3=Budget Tool and all of its data will be kept together in this folder. Pick any folder you can write to, including a USB drive.
SelectDirBrowseLabel=To continue, click Next. To use another folder (or a USB drive), click Browse.%n%nAvoid Program Files: Budget Tool needs to save your data next to itself.
FinishedLabel=Budget Tool is installed in its own folder, together with your data. You can copy that whole folder to a USB drive and run BudgetTool.exe from there.%n%nTo remove Budget Tool, just delete the folder.

[Code]
// The closest folder that already exists (so the check never creates folders the person might abandon).
function NearestExistingDir(Dir: String): String;
var
  Parent: String;
begin
  Result := RemoveBackslashUnlessRoot(ExpandFileName(Dir));
  while (Result <> '') and not DirExists(Result) do
  begin
    Parent := ExtractFileDir(Result);
    if (Parent = Result) or (Parent = '') then
    begin
      Result := '';
      exit;
    end;
    Result := Parent;
  end;
end;

function CanWriteTo(Dir: String): Boolean;
var
  Probe, F: String;
begin
  Result := False;
  Probe := NearestExistingDir(Dir);
  if Probe = '' then
    exit;
  F := AddBackslash(Probe) + '~budgettool-write-test.tmp';
  if SaveStringToFile(F, 'test', False) then
  begin
    DeleteFile(F);
    Result := True;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and not CanWriteTo(WizardDirValue) then
  begin
    SuppressibleMsgBox('Budget Tool can''t go in that folder because this Windows account can''t write there' +
      ' (or the drive isn''t connected).' + #13#10#13#10 +
      'Budget Tool saves your data next to itself, so pick a folder like Documents, the Desktop or a USB drive.',
      mbError, MB_OK, IDOK);
    Result := False;
  end;
end;

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
