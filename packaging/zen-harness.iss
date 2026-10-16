; Zazen installer (Inno Setup 6). Built by packaging\build.ps1, which passes AppVersion, DistDir and OutDir.
; Per-user install, no administrator rights. User data lives in %LOCALAPPDATA%\ZenHarness and survives
; uninstall unless the user chooses to remove it.

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#ifndef DistDir
  #define DistDir "out\dist\ZenHarness"
#endif
#ifndef OutDir
  #define OutDir "out"
#endif

[Setup]
AppId={{6E0C5A4B-2D7F-4C1E-9B8A-5F3D2E1C7A90}
AppName=Zazen
AppVersion={#AppVersion}
AppVerName=Zazen {#AppVersion}
AppPublisher=Zazen
DefaultDirName={localappdata}\Programs\Zen Harness
DefaultGroupName=Zazen
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19045
OutputDir={#OutDir}
OutputBaseFilename=ZenHarness-Setup-{#AppVersion}
SetupIconFile=..\backend\assets\zen_harness.ico
UninstallDisplayIcon={app}\ZenHarness.exe
LicenseFile=..\LICENSE
Compression=lzma2/ultra64
SolidCompression=yes
; compress in a separate 64-bit process (Qt WebEngine is large; the 32-bit compiler ran out of memory)
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Run in the background at sign-in, so other apps can use Zazen (API / MCP)"; GroupDescription: "Background service:"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Zazen"; Filename: "{app}\ZenHarness.exe"
Name: "{group}\Uninstall Zazen"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Zazen"; Filename: "{app}\ZenHarness.exe"; Tasks: desktopicon
; same name the app's own Settings toggle uses (backend/autostart.py), so it shows as on
Name: "{userstartup}\Zazen Headless"; Filename: "{app}\ZenHarness.exe"; Parameters: "--headless"; WorkingDir: "{app}"; Tasks: autostart

[InstallDelete]
; shortcuts from before the renames (Zen Harness -> Zen -> Zazen); the install folder keeps its old name
Type: files; Name: "{group}\Zen.lnk"
Type: files; Name: "{group}\Uninstall Zen.lnk"
Type: files; Name: "{userdesktop}\Zen.lnk"
Type: files; Name: "{userstartup}\Zen Headless.lnk"
Type: files; Name: "{group}\Zen Harness.lnk"
Type: files; Name: "{group}\Uninstall Zen Harness.lnk"
Type: files; Name: "{userdesktop}\Zen Harness.lnk"
Type: files; Name: "{userstartup}\Zen Headless Harness.lnk"

[Run]
Filename: "{app}\ZenHarness.exe"; Description: "Start Zazen now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; stop ONLY processes running from this installation (ZenHarness.exe, zenharness-cli.exe, its llama-server.exe).
; Never kill by image name: a name-based taskkill once stopped another copy's model server on the same PC.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -Command ""Get-Process | Where-Object {{ $_.Path -and $_.Path.StartsWith('{app}\', [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force"""; Flags: runhidden; RunOnceId: "StopOwnProcesses"

[UninstallDelete]
Type: files; Name: "{userstartup}\Zazen Headless.lnk"
Type: files; Name: "{userstartup}\Zen Headless.lnk"
Type: files; Name: "{userstartup}\Zen Headless Harness.lnk"
; PrismML's llama.cpp build that older installers unpacked here (first-run setup now keeps runtimes in the data folder)
Type: filesandordirs; Name: "{app}\llama-prism"

[Code]
// Nothing heavy is installed here: on first run the app detects the GPU (NVIDIA, AMD or Intel) and downloads the
// matching llama.cpp runtime, the model file sized for it and, if wanted, offline Wikipedia (backend/setup_api.py),
// all into the data folder.

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\ZenHarness');
    if DirExists(DataDir) and not UninstallSilent then
      if MsgBox('Also remove your data?' + #13#10#13#10 +
                'Your chats, settings and memory, and the downloaded model, runtime and offline Wikipedia, are in:' + #13#10 +
                DataDir + #13#10#13#10 + 'Choose No to keep them for a later reinstall.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
