; online.iss — Safa online installer (M10c §9.1).
;
; Small payload (~100-150 MB): app code, frontend/dist, ffmpeg, uv.exe,
; frozen requirements for both variants. At install time the wizard picks
; CPU vs CUDA 12.4 (auto-detected via nvidia-smi, driver >= DriverMin) and
; scripts\install-runtime.ps1 provisions the runtime from the official
; PyPI / download.pytorch.org indexes (lock hashes inside requirements).
;
;   ISCC /DAppVer=0.1.0 /DAppGuid={...} /DSourceDir=... /DOutDir=... online.iss
;
; Silent: setup.exe /VERYSILENT /DIR=... /DATADIR=... /VARIANT=cpu|gpu|auto

#ifndef AppVer
  #error "ISCC /DAppVer=x.y.z required"
#endif
#ifndef AppGuid
  #error "ISCC /DAppGuid={GUID} required"
#endif
#ifndef SourceDir
  #error "ISCC /DSourceDir=path required"
#endif
#ifndef OutDir
  #define OutDir SourceDir
#endif
#ifndef RepoUrl
  #define RepoUrl "https://github.com/<owner>/safa"
#endif
#ifndef DriverMin
  #define DriverMin 551
#endif
#define VariantName "online"
#define AppExeName "Safa.exe"

[Setup]
AppId={{{#AppGuid}}
AppName=Safa (صفا)
AppVerName=Safa (صفا) {#AppVer}
AppVersion={#AppVer}
AppPublisher=Safa
AppSupportURL={#RepoUrl}
AppUpdatesURL={#RepoUrl}
DefaultDirName={localappdata}\Programs\Safa
DefaultGroupName=Safa
PrivilegesRequired=lowest
OutputDir={#OutDir}
OutputBaseFilename=Safa-{#AppVer}-win-x64-setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
UninstallDisplayName=Safa (صفا)
UninstallDisplayIcon={app}\Safa.exe
CloseApplications=yes
SetupIconFile={#SourceDir}\launcher\safa.ico

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ar"; MessagesFile: "compiler:Languages\Arabic.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Payload staged by build-installer.ps1 (small — no python/, no models/).
Source: "{#SourceDir}\payload\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#SourceDir}\launcher\Safa.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Safa"; Filename: "{app}\Safa.exe"; IconFilename: "{app}\Safa.exe"
Name: "{group}\Safa (صفا) README"; Filename: "{app}\README.md"
Name: "{autodesktop}\Safa"; Filename: "{app}\Safa.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Safa.exe"; Description: "{cm:LaunchProgram,Safa}"; Flags: nowait postinstall skipifsilent unchecked

[CustomMessages]
en.VariantCaption=Choose the engine variant
en.VariantDescription=Safa can use an NVIDIA GPU for faster processing.
en.VariantSubCaption=GPU requires an NVIDIA card with driver {#DriverMin} or newer and ~6 GB of downloads. CPU works on every PC and needs ~2 GB.
ar.VariantCaption=اختر نمط المحرّك
ar.VariantDescription=يمكن لصفا استخدام بطاقة NVIDIA للمعالجة الأسرع.
ar.VariantSubCaption=وضع البطاقة يتطلب بطاقة NVIDIA بمشغّل {#DriverMin} أو أحدث و~6 غ.ب من التنزيلات. وضع المعالج يعمل على أي جهاز ويحتاج ~2 غ.ب.
en.VariantGpu=GPU (CUDA 12.4) — recommended for NVIDIA cards
en.VariantCpu=CPU — works on every computer
ar.VariantGpu=بطاقة الشاشة (CUDA 12.4) — موصى به لبطاقات NVIDIA
ar.VariantCpu=المعالج — يعمل على أي جهاز
en.ProgressCaption=Installing the Safa runtime
en.ProgressText=Downloading Python and libraries — this can take a few minutes.
ar.ProgressCaption=تثبيت بيئة تشغيل صفا
ar.ProgressText=يتم تنزيل بايثون والمكتبات — قد يستغرق هذا عدة دقائق.
en.GpuDetected=NVIDIA GPU detected (driver %s) — the GPU variant is preselected.
ar.GpuDetected=تم اكتشاف بطاقة NVIDIA (مشغّل %s) — تم اختيار نمط البطاقة مسبقاً.

#include "common.iss"

[Code]
var
  VariantPage: TInputOptionWizardPage;
  UvProgressPage: TOutputProgressWizardPage;
  GpuDetected: Boolean;
  GpuDriver: string;

function VariantParam(): string;
begin
  Result := Lowercase(ExpandConstant('{param:VARIANT|auto}'));
  if (Result <> 'cpu') and (Result <> 'gpu') and (Result <> 'cu124') then
    Result := 'auto';
end;

function DetectNvidia(): Boolean;
var
  ResultCode: Integer;
  TmpFile, Major: string;
  OutText: AnsiString;
  P: Integer;
begin
  Result := False;
  GpuDriver := '';
  TmpFile := ExpandConstant('{tmp}\nv-driver.txt');
  if not Exec(ExpandConstant('{cmd}'),
              '/c nvidia-smi --query-gpu=driver_version --format=csv,noheader > "' + TmpFile + '" 2>nul',
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    exit;
  if (ResultCode <> 0) or not LoadStringFromFile(TmpFile, OutText) then
    exit;
  P := Pos('.', OutText);
  if P = 0 then
    Major := Trim(OutText)
  else
    Major := Trim(Copy(OutText, 1, P - 1));
  GpuDriver := Trim(Copy(OutText, 1, Pos(#13, OutText + #13) - 1));
  Result := StrToIntDef(Major, 0) >= {#DriverMin};
end;

function SelectedVariant(): string;
begin
  Result := VariantParam();
  if Result = 'gpu' then Result := 'cu124';
  if Result = 'auto' then
    if GpuDetected then Result := 'cu124' else Result := 'cpu';
end;

function InitializeSetup(): Boolean;
begin
  GpuDetected := DetectNvidia();
  Result := True;
end;

procedure InitializeWizard();
begin
  VariantPage := CreateInputOptionPage(
    wpWelcome,
    CustomMessage('VariantCaption'),
    CustomMessage('VariantDescription'),
    CustomMessage('VariantSubCaption'),
    True, False);
  VariantPage.Add(CustomMessage('VariantGpu'));
  VariantPage.Add(CustomMessage('VariantCpu'));
  if VariantParam() = 'cpu' then
  begin
    VariantPage.Values[0] := False;
    VariantPage.Values[1] := True;
  end else begin
    VariantPage.Values[0] := True;
    VariantPage.Values[1] := False;
  end;
  CreateDataDirPage(VariantPage.ID);
  UvProgressPage := CreateOutputProgressPage(
    CustomMessage('ProgressCaption'), CustomMessage('ProgressText'));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if Assigned(VariantPage) and (CurPageID = VariantPage.ID) then
    if GpuDetected and not VariantPage.Values[0] then
      Log('user overrode detected GPU -> cpu variant');
end;

{ The wizard can't show streaming output; run the provisioning script
  hidden and point at the kept log on failure. }
procedure RunRuntimeInstall();
var
  ResultCode: Integer;
  Params, LogPath: string;
begin
  ForceDirectories(SafaDataDir() + '\logs');
  LogPath := SafaDataDir() + '\logs\install.log';
  UvProgressPage.SetText(CustomMessage('ProgressText'), '');
  UvProgressPage.SetProgress(0, 0);
  UvProgressPage.Show;
  try
    Params := '-NoProfile -ExecutionPolicy Bypass -File "'
      + ExpandConstant('{app}\scripts\install-runtime.ps1')
      + '" -Variant ' + SelectedVariant()
      + ' -AppDir "' + ExpandConstant('{app}')
      + '" -DataDir "' + SafaDataDir() + '"';
    Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
         Params, ExpandConstant('{app}'), SW_HIDE,
         ewWaitUntilTerminated, ResultCode);
    if ResultCode <> 0 then
      SuppressibleMsgBox(
        FmtMessage(CustomMessage('RuntimeInstallFailed'), [LogPath]),
        mbError, MB_OK, IDOK);
  finally
    UvProgressPage.Hide;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    WriteInstalledJson('');
    RunRuntimeInstall();
  end;
end;
