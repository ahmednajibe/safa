; offline-cpu.iss — Safa offline CPU installer (M10c §9.2).
;
; One self-contained exe < 2 GiB: full standalone python + wheels, the
; app, ffmpeg and the core model files — zero internet needed. The stage
; tree is built by build-installer.ps1 (-Variant cpu -Offline, which runs
; package.ps1 -Variant cpu -Full).
;
;   ISCC /DAppVer /DAppGuid /DSourceDir=<stage> /DOutDir=... offline-cpu.iss
;
; Silent: setup.exe /VERYSILENT /DIR=... /DATADIR=...

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
#define VariantName "cpu-offline"
#define AppExeName "Safa.exe"

[Setup]
AppId={{{#AppGuid}}
AppName=Safa (صفا) CPU offline
AppVerName=Safa (صفا) {#AppVer} — CPU offline
AppVersion={#AppVer}
AppPublisher=Safa
AppSupportURL={#RepoUrl}
AppUpdatesURL={#RepoUrl}
DefaultDirName={localappdata}\Programs\Safa
DefaultGroupName=Safa
PrivilegesRequired=lowest
OutputDir={#OutDir}
OutputBaseFilename=Safa-{#AppVer}-win-x64-cpu-offline-setup
Compression=lzma2/ultra64
SolidCompression=yes
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
; Full portable stage: python\ backend\ frontend\ tools\ models\ scripts\ ...
Source: "{#SourceDir}\stage\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Safa"; Filename: "{app}\Safa.exe"; IconFilename: "{app}\Safa.exe"
Name: "{autodesktop}\Safa"; Filename: "{app}\Safa.exe"; IconFilename: "{app}\Safa.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Safa.exe"; Description: "{cm:LaunchProgram,Safa}"; Flags: nowait postinstall skipifsilent unchecked

#include "common.iss"

[Code]
procedure InitializeWizard();
begin
  CreateDataDirPage(wpSelectDir);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    // models ride inside {app}\models — point installed.json at them so
    // they are not duplicated into the data folder.
    WriteInstalledJson(ExpandConstant('{app}\models'));
end;
