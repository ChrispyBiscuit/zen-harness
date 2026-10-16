; Zen installer (Inno Setup 6). Built by packaging\build.ps1, which passes AppVersion, DistDir and OutDir.
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
AppName=Zen
AppVersion={#AppVersion}
AppVerName=Zen {#AppVersion}
AppPublisher=Zen
DefaultDirName={localappdata}\Programs\Zen Harness
DefaultGroupName=Zen
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
; the CUDA DLLs are huge: compress in a separate 64-bit process (the 32-bit compiler runs out of memory)
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Run in the background at sign-in, so other apps can use Zen (API / MCP)"; GroupDescription: "Background service:"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Zen"; Filename: "{app}\ZenHarness.exe"
Name: "{group}\Uninstall Zen"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Zen"; Filename: "{app}\ZenHarness.exe"; Tasks: desktopicon
; same name the app's own Settings toggle uses (backend/autostart.py), so it shows as on
Name: "{userstartup}\Zen Headless"; Filename: "{app}\ZenHarness.exe"; Parameters: "--headless"; WorkingDir: "{app}"; Tasks: autostart

[InstallDelete]
; shortcuts from before the rename (Zen Harness -> Zen); the install folder keeps its old name
Type: files; Name: "{group}\Zen Harness.lnk"
Type: files; Name: "{group}\Uninstall Zen Harness.lnk"
Type: files; Name: "{userdesktop}\Zen Harness.lnk"
Type: files; Name: "{userstartup}\Zen Headless Harness.lnk"

[Run]
Filename: "{app}\ZenHarness.exe"; Description: "Start Zen now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; stop ONLY processes running from this installation (ZenHarness.exe, zenharness-cli.exe, its llama-server.exe).
; Never kill by image name: a name-based taskkill once stopped another copy's model server on the same PC.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -Command ""Get-Process | Where-Object {{ $_.Path -and $_.Path.StartsWith('{app}\', [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force"""; Flags: runhidden; RunOnceId: "StopOwnProcesses"

[UninstallDelete]
Type: files; Name: "{userstartup}\Zen Headless.lnk"
Type: files; Name: "{userstartup}\Zen Headless Harness.lnk"
; PrismML's llama.cpp build, unpacked by the installer when Ternary Bonsai is chosen (not a [Files] entry)
Type: filesandordirs; Name: "{app}\llama-prism"

[Code]
// ---------------------------------------------------------------------------------------------------------------
// Models downloaded during setup (not bundled): chosen on a "Models" page, fetched with checksums before the files
// are installed, then moved into the data folder's models directory, where the app's built-in profiles
// (backend/model_profiles.json) find them by file name. Files already there with the right size are skipped.
// Ternary Bonsai needs PrismML's llama.cpp fork for its 1-bit format: unpacked to {app}\llama-prism, with the
// CUDA runtime copied from the bundled build (same CUDA 12 DLLs - saves a 391 MB download).
// ---------------------------------------------------------------------------------------------------------------
const
  NModels = 3;
  PrismZip = 'llama-prism-b10772-e8fd209-bin-win-cuda-12.4-x64.zip';
  PrismUrl = 'https://github.com/PrismML-Eng/llama.cpp/releases/download/prism-b10772-e8fd209/llama-prism-b10772-e8fd209-bin-win-cuda-12.4-x64.zip';
  PrismSha = '462599816f7a3cd66a397b2a7d652ac41a28cdf6cc6696f022b1429ba4126e6a';

var
  ModelPage: TInputOptionWizardPage;
  DownloadPage: TDownloadWizardPage;
  MName, MFile, MUrl, MSha: array of String;
  MSize: array of Int64;
  Fetched: array of Boolean;      // downloaded this run (to move into place after install)
  PrismFetched: Boolean;

procedure AddModel(I: Integer; Name, FileName, Url, Sha: String; Size: Int64);
begin
  MName[I] := Name; MFile[I] := FileName; MUrl[I] := Url; MSha[I] := Sha; MSize[I] := Size;
end;

function ModelsDir: String;
begin
#ifdef DLTEST
  Result := ExpandConstant('{%TEMP}\isstest\test-models');   // test build: never touch the real data folder
#else
  Result := ExpandConstant('{localappdata}\ZenHarness\models');
#endif
end;

function GB(Size: Int64): String;
begin
  Result := Format('%.1f GB', [Size / 1000000000.0]);
end;

function Present(I: Integer): Boolean;
var
  Size: Int64;
begin
  Result := FileSize64(ModelsDir + '\' + MFile[I], Size) and (Size = MSize[I]);
end;

procedure InitializeWizard;
var
  I: Integer;
begin
  SetArrayLength(MName, NModels + 1); SetArrayLength(MFile, NModels + 1); SetArrayLength(MUrl, NModels + 1);
  SetArrayLength(MSha, NModels + 1); SetArrayLength(MSize, NModels + 1); SetArrayLength(Fetched, NModels + 1);
  // index 3 = Ornith's vision projector, fetched with Ornith (not a separate choice)
  AddModel(0, 'Ornith 1.5 9B (Q4_K_M) - agent work and coding, fast; needs ~7 GB of GPU memory',
    'Ornith-1.5-9B-Q4_K_M.gguf',
    'https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF/resolve/main/Ornith-1.5-9B-Q4_K_M.gguf',
    '70c112196e0b7023803c9762752e46d29e612a92c83f995bc3ba1ceb07e8fab6', 5780090816);
  AddModel(1, 'Qwen3.6 35B-A3B REAP-48 (mixture of experts) - stronger; part of it runs from system RAM',
    'Qwen3.6-35B-A3B-REAP-48-v2.gguf',
    'https://huggingface.co/crucible-labs/Qwen3.6-35B-A3B-REAP-48-v2-GGUF/resolve/main/Qwen3.6-35B-A3B-REAP-48-v2.gguf',
    '91d1e110bf55741f9bddbeaba1d0c6e224058a09d46607b2b6cd757cb03fa191', 9426919520);
  AddModel(2, 'Ternary Bonsai 2 27B (1-bit) - strongest local builder; needs 8 GB of GPU memory',
    'Ternary-Bonsai-2-27B-PTQ1_0.gguf',
    'https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf/resolve/main/Ternary-Bonsai-2-27B-PTQ1_0.gguf',
    '53107f530aa52eb00912263ab1ee29bd199261c87cd7b4ad4ca1318c1fe33ee3', 5946648928);
  AddModel(3, 'Ornith vision', 'mmproj-Ornith-1.5-9B-BF16.gguf',
    'https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF/resolve/main/mmproj-Ornith-1.5-9B-BF16.gguf',
    '626f9f90627402a6bf4a999111d0fbd69b5fcca7aa8ba089d69e5f10e8858e1d', 921704672);
#ifdef DLTEST
  // test build (ISCC /DDLTEST): small files instead of the models, same code path; no checksum
  for I := 0 to NModels do
  begin
    MUrl[I] := 'https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF/resolve/main/README.md';
    MSha[I] := '';
    MFile[I] := 'test-' + IntToStr(I) + '.gguf';
  end;
#endif

  ModelPage := CreateInputOptionPage(wpSelectTasks, 'Models',
    'Which AI models should setup download?',
    'Models are downloaded from Hugging Face now (about 22 GB for all three) and stored in ' + ModelsDir +
    '. You can add or remove models later in Settings > Models. Untick all to download none.', False, False);
  for I := 0 to NModels - 1 do
  begin
    if Present(I) then
      ModelPage.Add(MName[I] + '  [already downloaded]')
    else if I = 0 then
      ModelPage.Add(MName[I] + '  (' + GB(MSize[0] + MSize[3]) + ')')
    else
      ModelPage.Add(MName[I] + '  (' + GB(MSize[I]) + ')');
    ModelPage.Values[I] := True;
  end;
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), SetupMessage(msgPreparingDesc), nil);
end;

function Wanted(I: Integer): Boolean;
begin
  if I = 3 then Result := ModelPage.Values[0] else Result := ModelPage.Values[I];
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  I: Integer;
  Any: Boolean;
begin
  Result := True;
  if CurPageID <> wpReady then exit;
  DownloadPage.Clear;
  Any := False;
  for I := 0 to NModels do
  begin
    Fetched[I] := False;
    if Wanted(I) and not Present(I) then
    begin
      DownloadPage.Add(MUrl[I], MFile[I], MSha[I]);
      Fetched[I] := True;
      Any := True;
    end;
  end;
  PrismFetched := ModelPage.Values[2] and not FileExists(ExpandConstant('{app}\llama-prism\llama-server.exe'));
  if PrismFetched then
  begin
    DownloadPage.Add(PrismUrl, PrismZip, PrismSha);
    Any := True;
  end;
  if not Any then exit;
  DownloadPage.Show;
  try
    try
      DownloadPage.Download;            // into {tmp}, each checked against its SHA-256
    except
      if DownloadPage.AbortedByUser then
        Log('Model download cancelled by the user.')
      else
        SuppressibleMsgBox('A download failed: ' + GetExceptionMessage + #13#10#13#10 +
          'Setup will continue without it - you can download models later in Settings > Models.',
          mbError, MB_OK, IDOK);
      for I := 0 to NModels do
        if Fetched[I] and not FileExists(ExpandConstant('{tmp}\') + MFile[I]) then Fetched[I] := False;
      if PrismFetched and not FileExists(ExpandConstant('{tmp}\' + PrismZip)) then PrismFetched := False;
    end;
  finally
    DownloadPage.Hide;
  end;
end;

procedure PlaceDownloads;
var
  I, Code: Integer;
  Src, Dst, Llama: String;
begin
  ForceDirectories(ModelsDir);
  for I := 0 to NModels do
    if Fetched[I] then
    begin
      Src := ExpandConstant('{tmp}\') + MFile[I];
      Dst := ModelsDir + '\' + MFile[I];
      DeleteFile(Dst);
      if not RenameFile(Src, Dst) then        // another drive: copy instead
        if not FileCopy(Src, Dst, False) then Log('Could not place ' + MFile[I]);
    end;
  if PrismFetched then
  begin
    Dst := ExpandConstant('{app}\llama-prism');
    ForceDirectories(Dst);
    // tar.exe ships with Windows 10 1803+ and reads .zip files
    if not Exec(ExpandConstant('{sys}\tar.exe'), '-xf "' + ExpandConstant('{tmp}\' + PrismZip) + '" -C "' + Dst + '"',
                '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
      Log('Unpacking PrismML llama.cpp failed: ' + IntToStr(Code));
    Llama := ExpandConstant('{app}\llama\');
    FileCopy(Llama + 'cudart64_12.dll', Dst + '\cudart64_12.dll', False);
    FileCopy(Llama + 'cublas64_12.dll', Dst + '\cublas64_12.dll', False);
    FileCopy(Llama + 'cublasLt64_12.dll', Dst + '\cublasLt64_12.dll', False);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := 'Moving the downloaded models into place...';
    PlaceDownloads;
  end;
end;

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
