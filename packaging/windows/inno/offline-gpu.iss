; offline-gpu.iss — Safa offline GPU installer (M10c §9.3).
;
; Same contents as the CPU offline installer but with the CUDA 12.4
; runtime (~9 GiB extracted). DiskSpanning produces
;   Safa-<ver>-win-x64-gpu-offline-setup.exe + -1.bin ... -N.bin
; slices <= SliceSize bytes (default 1.8 GiB, overridable via /DSliceSize
; so tests can force spanning with a small payload). Inno refuses to
; install when a slice is missing or from another build — the message is
; localized by the wizard language (EN/AR ship with Inno).
;
;   ISCC /DAppVer /DAppGuid /DSourceDir=<stage> /DOutDir=... offline-gpu.iss
;
; Silent: setup.exe /VERYSILENT /DIR=... /DATADIR=...   (all slices must
; sit next to the exe)

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
#ifndef SliceSize
  #define SliceSize 1932735283
#endif
#define VariantName "gpu-offline"
#define AppExeName "Safa.exe"

[Setup]
AppId={{{#AppGuid}}
AppName=Safa (صفا) GPU offline
AppVerName=Safa (صفا) {#AppVer} — GPU offline
AppVersion={#AppVer}
AppPublisher=Safa
AppSupportURL={#RepoUrl}
AppUpdatesURL={#RepoUrl}
DefaultDirName={localappdata}\Programs\Safa
DefaultGroupName=Safa
PrivilegesRequired=lowest
OutputDir={#OutDir}
OutputBaseFilename=Safa-{#AppVer}-win-x64-gpu-offline-setup
Compression=lzma2/ultra64
SolidCompression=yes
DiskSpanning=yes
DiskSliceSize={#SliceSize}
SlicesPerDisk=1
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
UninstallDisplayName=Safa (صفا)
UninstallDisplayIcon={app}\Safa.exe
CloseApplications=yes
SetupIconFile={#SourceDir}\Safa.ico

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ar"; MessagesFile: "compiler:Languages\Arabic.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\stage\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Safa"; Filename: "{app}\Safa.exe"; IconFilename: "{app}\Safa.exe"
Name: "{autodesktop}\Safa"; Filename: "{app}\Safa.exe"; IconFilename: "{app}\Safa.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Safa.exe"; Description: "{cm:LaunchProgram,Safa}"; Flags: nowait postinstall skipifsilent unchecked

[CustomMessages]
en.MissingSlices=Setup is incomplete. Download these files into the SAME folder as this setup, then run it again:
ar.MissingSlices=التثبيت غير مكتمل. نزّل هذه الملفات في نفس مجلد هذا المثبّت، ثم شغّله مرة أخرى:

#include "common.iss"

[Code]
{ Verify the -N.bin slices are present BEFORE the wizard starts — Inno's
  native "insert disk" prompt cannot abort a silent install and confuses
  people who downloaded only the exe. With a slicecount.txt companion
  (release packaging writes it) every expected slice is checked. Without
  it (the usual browser download: just the exe) slice 1 — which always
  exists when DiskSpanning splits anything — must still be there; a
  missing later slice then falls back to Inno's own disk prompt. }
function SliceName(I: Integer): string;
begin
  Result := Format('Safa-{#AppVer}-win-x64-gpu-offline-setup-%d.bin', [I]);
end;

function InitializeSetup(): Boolean;
var
  CntFile: string;
  S: AnsiString;
  N, I: Integer;
  Missing: string;
begin
  Result := True;
  CntFile := ExpandConstant('{src}\slicecount.txt');
  N := 1;  // no companion file -> at least slice 1
  if LoadStringFromFile(CntFile, S) then
    N := StrToIntDef(Trim(string(S)), 1);
  if N <= 0 then
    N := 1;
  Missing := '';
  for I := 1 to N do
    if not FileExists(ExpandConstant('{src}\') + SliceName(I)) then
      Missing := Missing + #13#10 + '  ' + SliceName(I);
  if Missing <> '' then
  begin
    SuppressibleMsgBox(
      CustomMessage('MissingSlices') + Missing, mbError, MB_OK, IDOK);
    Result := False;
  end;
end;

procedure InitializeWizard();
begin
  CreateDataDirPage(wpSelectDir);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  { show the folder the install will really use (F:\ -> F:\Safa) }
  if Assigned(DataDirPage) and (CurPageID = DataDirPage.ID) then
    DataDirPage.Values[0] := NormalizeDataDir(DataDirPage.Values[0]);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    WriteInstalledJson(ExpandConstant('{app}\models'));
end;
