; Inno Setup script for BudgetTool-Setup.exe. Built by build-installer.bat (and the release workflow),
; which pass the version in: ISCC /DAppVersion=1.2.3 installer.iss
; Installs for the current user only (no admin needed). The person installing picks:
;   - the app folder (default %LOCALAPPDATA%\Programs\BudgetTool, the same place build.bat uses), and
;   - the data folder (default %APPDATA%\BudgetTool). It's saved in HKCU\Software\BudgetTool\DataDir,
;     which paths.py reads; in-app updates never touch it.
; Silent installs can pass both:  BudgetTool-Setup.exe /VERYSILENT /DIR="D:\Apps\BudgetTool" /DATADIR="D:\BudgetData"

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
; Show the folder page (an upgrade suggests the folder used last time).
DisableDirPage=no
UsePreviousAppDir=yes
DisableProgramGroupPage=yes
DisableReadyPage=no
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

[Registry]
; Where the app keeps your data. Left in place on uninstall (like the data itself), so reinstalling finds it.
Root: HKCU; Subkey: "Software\BudgetTool"; ValueType: string; ValueName: "DataDir"; ValueData: "{code:GetDataDir}"

[Tasks]
Name: "desktopicon"; Description: "Create a Desktop shortcut"; GroupDescription: "Shortcuts:"

[Icons]
Name: "{userprograms}\Budget Tool"; Filename: "{app}\BudgetTool.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\Budget Tool"; Filename: "{app}\BudgetTool.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\BudgetTool.exe"; Description: "Open Budget Tool now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only the app's own files (updates may have added files the installer doesn't know about), never the whole
; folder: someone may have picked a folder that holds other things too. Your data folder is never touched.
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\BudgetTool.exe"
Type: dirifempty; Name: "{app}"

[Messages]
SelectDirBrowseLabel=To continue, click Next. To install somewhere else, click Browse.%n%nPick a folder this Windows account can write to (not Program Files, which needs an administrator and would stop automatic updates).

[Code]
var
  DataPage: TInputDirWizardPage;
  OldDataDir: String;   // the data folder before this install ('' = never set, so the default)

function DefaultDataDir(): String;
begin
  Result := ExpandConstant('{userappdata}\BudgetTool');
end;

function PreviousDataDir(): String;
begin
  if not RegQueryStringValue(HKCU, 'Software\BudgetTool', 'DataDir', Result) then
    Result := '';
end;

procedure InitializeWizard();
var
  Start: String;
begin
  OldDataDir := PreviousDataDir();
  DataPage := CreateInputDirPage(wpSelectDir,
    'Where should your data be stored?',
    'Your budget, settings and bank connections are kept in this folder.',
    'Budget Tool keeps its database in the folder below. It stays there when you update or uninstall the app.' + #13#10#13#10 +
    'Pick a folder this Windows account can write to, outside the app folder (updates replace everything in the app folder). ' +
    'If the folder already has Budget Tool data, Budget Tool will use it.',
    False, 'BudgetTool');
  DataPage.Add('');
  Start := ExpandConstant('{param:DATADIR|}');
  if Start = '' then Start := OldDataDir;
  if Start = '' then Start := DefaultDataDir();
  DataPage.Values[0] := Start;
end;

function GetDataDir(Param: String): String;
begin
  Result := RemoveBackslashUnlessRoot(ExpandFileName(Trim(DataPage.Values[0])));
end;

// The closest folder that already exists (so checks never create folders the person might abandon).
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

// True if Child is Parent or a folder inside it.
function IsInside(Child, Parent: String): Boolean;
begin
  Result := Pos(AddBackslash(Lowercase(ExpandFileName(Parent))), AddBackslash(Lowercase(ExpandFileName(Child)))) = 1;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    if not CanWriteTo(WizardDirValue) then
    begin
      SuppressibleMsgBox('Budget Tool can''t be installed in that folder because this Windows account can''t write there.' + #13#10#13#10 +
        'Folders like Program Files need an administrator and would also stop automatic updates. ' +
        'Pick a folder inside your user folder, or keep the suggested one.', mbError, MB_OK, IDOK);
      Result := False;
    end;
  end
  else if CurPageID = DataPage.ID then
  begin
    if Trim(DataPage.Values[0]) = '' then
    begin
      SuppressibleMsgBox('Pick a folder for your data.', mbError, MB_OK, IDOK);
      Result := False;
    end
    else if IsInside(DataPage.Values[0], WizardDirValue) then
    begin
      SuppressibleMsgBox('Pick a data folder outside the app folder (' + WizardDirValue + ').' + #13#10#13#10 +
        'Updates replace everything in the app folder, which would erase your data.', mbError, MB_OK, IDOK);
      Result := False;
    end
    else if not CanWriteTo(DataPage.Values[0]) then
    begin
      SuppressibleMsgBox('This Windows account can''t write to that folder (or its drive isn''t connected). Pick another one.',
        mbError, MB_OK, IDOK);
      Result := False;
    end;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo,
  MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo + NewLine + NewLine + 'Data folder:' + NewLine + Space + GetDataDir('') + NewLine;
  if MemoTasksInfo <> '' then
    Result := Result + NewLine + MemoTasksInfo + NewLine;
end;

// robocopy reads a trailing \ before a closing quote as an escaped quote; "D:\." is the same folder.
function RoboPath(Dir: String): String;
begin
  Result := Dir;
  if Copy(Result, Length(Result), 1) = '\' then
    Result := Result + '.';
  Result := '"' + Result + '"';
end;

// Picked a new data folder but have data in the old one? Offer to copy it (nothing is ever deleted).
procedure OfferToCopyData();
var
  FromDir, ToDir: String;
  Code: Integer;
begin
  ToDir := GetDataDir('');
  FromDir := OldDataDir;
  if FromDir = '' then
    FromDir := DefaultDataDir();
  FromDir := RemoveBackslashUnlessRoot(ExpandFileName(FromDir));
  if CompareText(AddBackslash(FromDir), AddBackslash(ToDir)) = 0 then
    exit;
  if not FileExists(AddBackslash(FromDir) + 'budget.db') then
    exit;
  if FileExists(AddBackslash(ToDir) + 'budget.db') then
  begin
    SuppressibleMsgBox('The data folder you picked already has Budget Tool data, so Budget Tool will use it:' + #13#10 + ToDir + #13#10#13#10 +
      'Your data in ' + FromDir + ' was left as it is.', mbInformation, MB_OK, IDOK);
    exit;
  end;
  if SuppressibleMsgBox('You already have Budget Tool data in:' + #13#10 + FromDir + #13#10#13#10 +
       'Copy it to your new data folder?' + #13#10 + ToDir + #13#10#13#10 +
       'Choose No to start with an empty budget there. Nothing is deleted either way.',
       mbConfirmation, MB_YESNO, IDYES) = IDYES then
  begin
    ForceDirectories(ToDir);
    if not Exec(ExpandConstant('{sys}\robocopy.exe'), RoboPath(FromDir) + ' ' + RoboPath(ToDir) + ' /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP',
                '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code >= 8) then
      SuppressibleMsgBox('Copying your data didn''t finish (robocopy code ' + IntToStr(Code) + ').' + #13#10#13#10 +
        'Before opening Budget Tool, copy everything from' + #13#10 + FromDir + #13#10 + 'to' + #13#10 + ToDir, mbError, MB_OK, IDOK);
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
  if CurStep = ssPostInstall then
  begin
    OfferToCopyData();
    if not WebView2Installed() then
    begin
      if SuppressibleMsgBox('Budget Tool needs the Microsoft Edge WebView2 Runtime, which isn''t on this PC.' + #13#10#13#10 +
                'Open Microsoft''s download page now? Install the "Evergreen Bootstrapper", then open Budget Tool.',
                mbConfirmation, MB_YESNO, IDNO) = IDYES then
        ShellExec('open', 'https://developer.microsoft.com/microsoft-edge/webview2/', '', '', SW_SHOWNORMAL, ewNoWait, Code);
    end;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
    WizardForm.FinishedLabel.Caption := 'Budget Tool is installed. Open it from the Start Menu or your Desktop.' + #13#10#13#10 +
      'Your budget data is kept in ' + GetDataDir('') + ' and is not removed if you uninstall.';
end;
