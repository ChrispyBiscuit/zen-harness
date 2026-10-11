; Zen Harness installer (Inno Setup 6). Built by packaging\build.ps1, which passes AppVersion, DistDir and OutDir.
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
AppName=Zen Harness
AppVersion={#AppVersion}
AppVerName=Zen Harness {#AppVersion}
AppPublisher=Zen Harness
DefaultDirName={localappdata}\Programs\Zen Harness
DefaultGroupName=Zen Harness
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
LZMANumBlockThreads=4
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Run in the background at sign-in, so other apps can use Zen Harness (API / MCP)"; GroupDescription: "Background service:"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Zen Harness"; Filename: "{app}\ZenHarness.exe"
Name: "{group}\Uninstall Zen Harness"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Zen Harness"; Filename: "{app}\ZenHarness.exe"; Tasks: desktopicon
; same name the app's own Settings toggle uses (backend/autostart.py), so it shows as on
Name: "{userstartup}\Zen Headless Harness"; Filename: "{app}\ZenHarness.exe"; Parameters: "--headless"; WorkingDir: "{app}"; Tasks: autostart

[Run]
Filename: "{app}\ZenHarness.exe"; Description: "Start Zen Harness now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM ZenHarness.exe"; Flags: runhidden; RunOnceId: "StopApp"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM llama-server.exe"; Flags: runhidden; RunOnceId: "StopModels"

[UninstallDelete]
Type: files; Name: "{userstartup}\Zen Headless Harness.lnk"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\ZenHarness');
    if DirExists(DataDir) and not UninstallSilent then
      if MsgBox('Also remove your data?' + #13#10#13#10 +
                'Your chats, settings, memory and downloaded models are in:' + #13#10 + DataDir + #13#10#13#10 +
                'Choose No to keep them for a later reinstall.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
