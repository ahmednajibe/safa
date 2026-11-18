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
  #define RepoUrl "https://github.com/ahmednajibe/safa"
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
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Payload staged by build-installer.ps1 (small — no python/, no models/).
Source: "{#SourceDir}\payload\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#SourceDir}\launcher\Safa.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Safa"; Filename: "{app}\Safa.exe"; IconFilename: "{app}\Safa.exe"
Name: "{group}\Safa (صفا) README"; Filename: "{app}\README.md"
Name: "{autodesktop}\Safa"; Filename: "{app}\Safa.exe"; IconFilename: "{app}\Safa.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Safa.exe"; Description: "{cm:LaunchProgram,Safa}"; Flags: nowait postinstall skipifsilent unchecked

[CustomMessages]
en.VariantCaption=Choose the engine variant
en.VariantDescription=Safa can use an NVIDIA GPU for faster processing.
en.VariantSubCaption=GPU requires an NVIDIA card with driver {#DriverMin} or newer and ~3 GB of downloads. CPU works on every PC and needs ~1 GB.
ar.VariantCaption=اختر نمط المحرّك
ar.VariantDescription=يمكن لصفا استخدام بطاقة NVIDIA للمعالجة الأسرع.
ar.VariantSubCaption=وضع البطاقة يتطلب بطاقة NVIDIA بمشغّل {#DriverMin} أو أحدث و~3 غ.ب من التنزيلات. وضع المعالج يعمل على أي جهاز ويحتاج ~1 غ.ب.
en.VariantGpu=GPU (CUDA 12.4) — recommended for NVIDIA cards
en.VariantCpu=CPU — works on every computer
ar.VariantGpu=بطاقة الشاشة (CUDA 12.4) — موصى به لبطاقات NVIDIA
ar.VariantCpu=المعالج — يعمل على أي جهاز
en.ProgressCaption=Installing the Safa runtime
en.ProgressText=Downloading Python and libraries. Keep this window open.
ar.ProgressCaption=تثبيت بيئة تشغيل صفا
ar.ProgressText=يتم تنزيل بايثون والمكتبات. أبقِ هذه النافذة مفتوحة.
en.Step1=Step 1 of 3: Downloading Python
en.Step2=Step 2 of 3: Downloading and installing libraries
en.Step3=Step 3 of 3: Checking the installation
ar.Step1=الخطوة 1 من 3: تنزيل بايثون
ar.Step2=الخطوة 2 من 3: تنزيل المكتبات وتثبيتها
ar.Step3=الخطوة 3 من 3: التحقق من التثبيت
en.Amount=%1 of about %2 GB  (%3%%)  —  %4
ar.Amount=%1 من حوالي %2 غ.ب  (%3%%)  —  %4
en.EtaUnknown=estimating time left...
en.EtaUnderMin=less than a minute left
en.EtaMin=about %1 min left
ar.EtaUnknown=جارٍ حساب الوقت المتبقي...
ar.EtaUnderMin=أقل من دقيقة متبقية
ar.EtaMin=حوالي %1 دقيقة متبقية
en.CancelAsk=Stop installing the runtime? Safa will not work until you run this setup again.
ar.CancelAsk=إيقاف تثبيت بيئة التشغيل؟ لن يعمل صفا حتى تشغّل هذا المثبّت مرة أخرى.
en.RuntimeCancelled=Runtime install was cancelled. Run this setup again to finish installing Safa.
ar.RuntimeCancelled=تم إلغاء تثبيت بيئة التشغيل. شغّل هذا المثبّت مرة أخرى لإكمال تثبيت صفا.
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
  begin
    { No explicit /VARIANT: the wizard's radio choice wins in
      interactive mode; silent installs fall back to auto-detect. }
    if not WizardSilent and Assigned(VariantPage) then
      if VariantPage.Values[0] then Result := 'cu124'
      else Result := 'cpu'
    else if GpuDetected then Result := 'cu124'
    else Result := 'cpu';
  end;
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
  end else if (VariantParam() = 'gpu') or (VariantParam() = 'cu124') then
  begin
    VariantPage.Values[0] := True;
    VariantPage.Values[1] := False;
  end else begin
    { auto — preselect by detection so a CPU-only machine does not
      default to a runtime it cannot install. }
    VariantPage.Values[0] := GpuDetected;
    VariantPage.Values[1] := not GpuDetected;
  end;
  CreateDataDirPage(VariantPage.ID);
  UvProgressPage := CreateOutputProgressPage(
    CustomMessage('ProgressCaption'), CustomMessage('ProgressText'));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  { show the folder the install will really use before the user moves
    on — a typed F:\ becomes F:\Safa, a trailing '\' is stripped }
  if Assigned(DataDirPage) and (CurPageID = DataDirPage.ID) then
    DataDirPage.Values[0] := NormalizeDataDir(DataDirPage.Values[0]);
  if Assigned(VariantPage) and (CurPageID = VariantPage.ID) then
    if GpuDetected and not VariantPage.Values[0] then
      Log('user overrode detected GPU -> cpu variant');
end;

{ v0.1.1: real progress. install-runtime.ps1 runs hidden WITHOUT waiting
  and rewrites a one-line status file every ~2 s:
    <pid>|<step>|<steps>|<done_mb>|<total_mb>|<eta_s>|<state>|<exit>
  This loop polls it, draws the bar + "1.2 of about 4.6 GB (26%) - about
  9 min left", and keeps the wizard responsive (SetText/SetProgress pump
  messages, as in Inno's CodeDlg example). Cancel kills the script's
  process tree by pid. v0.1.0 blocked in Exec(ewWaitUntilTerminated)
  behind static text for 20-40 min. }
var
  RuntimeCancelRequested: Boolean;

procedure RuntimeCancelClick(Sender: TObject);
begin
  RuntimeCancelRequested := True;
end;

function StatusField(const Line: string; Index: Integer): string;
var
  Rest: string;
  I, P: Integer;
begin
  Rest := Line + '|';
  Result := '';
  for I := 0 to Index do
  begin
    P := Pos('|', Rest);
    if P = 0 then
    begin
      Result := '';
      exit;
    end;
    Result := Copy(Rest, 1, P - 1);
    Delete(Rest, 1, P);
  end;
end;

function GbText(MB: Integer): string;
var
  T: Integer;
begin
  T := (MB * 10) div 1024;  { tenths of a GB, integer math only }
  Result := IntToStr(T div 10) + '.' + IntToStr(T mod 10);
end;

function EtaText(EtaS: Integer): string;
begin
  if EtaS < 0 then
    Result := CustomMessage('EtaUnknown')
  else if EtaS < 60 then
    Result := CustomMessage('EtaUnderMin')
  else
    Result := FmtMessage(CustomMessage('EtaMin'), [IntToStr((EtaS + 30) div 60)]);
end;

{ True when the runtime installed; False on failure or cancel. }
function RunRuntimeInstall(): Boolean;
var
  ResultCode, Step, DoneMB, TotalMB, EtaS, Pct, Overall, Stalled: Integer;
  Params, LogPath, StatusPath, State, Pid, Detail, LastLine: string;
  Line: AnsiString;
  OldCancel: TNotifyEvent;
  OldCancelEnabled: Boolean;
begin
  Result := False;
  ForceDirectories(SafaDataDir() + '\logs');
  LogPath := SafaDataDir() + '\logs\install.log';
  StatusPath := ExpandConstant('{tmp}\runtime-status.txt');
  DeleteFile(StatusPath);
  RuntimeCancelRequested := False;
  UvProgressPage.SetText(CustomMessage('ProgressText'), CustomMessage('Step1'));
  UvProgressPage.SetProgress(0, 1000);
  UvProgressPage.Show;
  OldCancel := WizardForm.CancelButton.OnClick;
  OldCancelEnabled := WizardForm.CancelButton.Enabled;
  WizardForm.CancelButton.OnClick := @RuntimeCancelClick;
  WizardForm.CancelButton.Enabled := True;
  try
    { every path goes through QuoteArg: a naive '"' wrap breaks on any
      trailing backslash (-DataDir "F:\" ate the rest of the line in
      v0.1.1 — the runtime then failed before writing install.log) }
    Params := '-NoProfile -ExecutionPolicy Bypass -File '
      + QuoteArg(ExpandConstant('{app}\scripts\install-runtime.ps1'))
      + ' -Variant ' + SelectedVariant()
      + ' -AppDir ' + QuoteArg(ExpandConstant('{app}'))
      + ' -DataDir ' + QuoteArg(SafaDataDir())
      + ' -StatusFile ' + QuoteArg(StatusPath);
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
                Params, ExpandConstant('{app}'), SW_HIDE,
                ewNoWait, ResultCode) then
    begin
      SuppressibleMsgBox(FmtMessage(CustomMessage('RuntimeInstallFailed'), [LogPath]),
        mbError, MB_OK, IDOK);
      exit;
    end;
    State := 'run';
    LastLine := '';
    Stalled := 0;
    Pid := '';
    while True do
    begin
      Sleep(250);
      if LoadStringFromFile(StatusPath, Line) then
      begin
        if String(Line) <> LastLine then
        begin
          LastLine := String(Line);
          Stalled := 0;
        end else
          Stalled := Stalled + 1;
        Pid := StatusField(LastLine, 0);
        Step := StrToIntDef(StatusField(LastLine, 1), 1);
        DoneMB := StrToIntDef(StatusField(LastLine, 3), 0);
        TotalMB := StrToIntDef(StatusField(LastLine, 4), 0);
        EtaS := StrToIntDef(StatusField(LastLine, 5), -1);
        State := StatusField(LastLine, 6);
        if (State = 'done') or (State = 'fail') then
          break;
        { overall bar: python 0-3 %, libraries 3-97 %, check 97-100 % }
        Detail := CustomMessage('Step1');
        Overall := 10;
        if Step = 2 then
        begin
          Pct := 0;
          if TotalMB > 0 then
            Pct := (DoneMB * 100) div TotalMB;
          if Pct > 99 then
            Pct := 99;
          Overall := 30 + (Pct * 940) div 100;
          Detail := CustomMessage('Step2') + #13#10 +
            FmtMessage(CustomMessage('Amount'), [GbText(DoneMB),
              GbText(TotalMB), IntToStr(Pct), EtaText(EtaS)]);  { no line may start with '[' in .iss }
        end else if Step >= 3 then
        begin
          Detail := CustomMessage('Step3');
          Overall := 980;
        end;
        UvProgressPage.SetText(CustomMessage('ProgressText'), Detail);
        UvProgressPage.SetProgress(Overall, 1000);
      end else
      begin
        Stalled := Stalled + 1;
        UvProgressPage.SetProgress(10, 1000);  { keeps messages pumping }
      end;
      { nothing new for 15 min (or no status file after 2 min): the
        script died without writing its final line }
      if (Stalled > 4 * 60 * 15) or ((Pid = '') and (Stalled > 4 * 120)) then
      begin
        State := 'fail';
        break;
      end;
      if RuntimeCancelRequested then
      begin
        RuntimeCancelRequested := False;
        if SuppressibleMsgBox(CustomMessage('CancelAsk'), mbConfirmation,
                              MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        begin
          if Pid <> '' then
            Exec(ExpandConstant('{sys}\taskkill.exe'), '/T /F /PID ' + Pid, '',
                 SW_HIDE, ewWaitUntilTerminated, ResultCode);
          State := 'cancel';
          break;
        end;
      end;
    end;
    if State = 'done' then
      Result := True
    else if State = 'cancel' then
      SuppressibleMsgBox(CustomMessage('RuntimeCancelled'), mbInformation, MB_OK, IDOK)
    else
      SuppressibleMsgBox(FmtMessage(CustomMessage('RuntimeInstallFailed'), [LogPath]),
        mbError, MB_OK, IDOK);
  finally
    WizardForm.CancelButton.OnClick := OldCancel;
    WizardForm.CancelButton.Enabled := OldCancelEnabled;
    UvProgressPage.Hide;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    { installed.json first: the uninstaller reads data_dir from it, so a
      failed/cancelled runtime install can still be cleanly uninstalled. }
    WriteInstalledJson('');
    if not RunRuntimeInstall() then
      Log('runtime install did not complete');
  end;
end;
