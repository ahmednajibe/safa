; common.iss — shared code for all Safa installers (M10c §9).
;
; Included by online.iss / offline-cpu.iss / offline-gpu.iss AFTER their
; [Setup] sections. ISPP defines come from build-installer.ps1 /D flags:
;   AppVer, AppGuid, SourceDir, OutDir, [DriverMin=551], [SliceSize]
;
; Shared behavior:
;   * data-folder wizard page (or /DATADIR= in silent installs)
;   * installed.json marker written next to Safa.exe (paths.py §5)
;   * uninstall keeps user data by default; explicit bilingual prompt
;     with the path shown before deleting anything.

[CustomMessages]
en.DeleteDataPrompt=Also delete your Safa data folder (your recordings, results and downloaded models)? The folder is:
ar.DeleteDataPrompt=هل تريد أيضاً حذف مجلد بيانات صفا (تسجيلاتك ونتائجك والنماذج المحمّلة)؟ المجلد هو:
en.DataPageCaption=Choose the data folder
en.DataPageDescription=Where should Safa keep your recordings, results and models?
en.DataPageSubCaption=Pick a drive with enough free space (models need ~600 MB; the runtime needs ~2 GB CPU / ~6 GB GPU). The folder will be created if it does not exist.
ar.DataPageCaption=اختر مجلد البيانات
ar.DataPageDescription=أين يحتفظ صفا بتسجيلاتك ونتائجك والنماذج؟
ar.DataPageSubCaption=اختر قرصاً فيه مساحة كافية (النماذج ~600 م.ب؛ بيئة التشغيل ~2 غ.ب لوحدة المعالجة / ~6 غ.ب للبطاقة). سيُنشأ المجلد إن لم يكن موجوداً.
en.RuntimeInstallFailed=Could not download the runtime. Check the internet connection and re-run this installer. The log is kept at:%n%n%1
ar.RuntimeInstallFailed=تعذّر تنزيل بيئة التشغيل. تحقق من الاتصال بالإنترنت وأعد تشغيل هذا المثبّت. تم الاحتفاظ بالسجل في:%n%n%1
en.BrowseData=Safa data
ar.BrowseData=بيانات صفا
en.DataKeptPrompt=Your Safa data folder was kept (it is not marked as a Safa data folder — nothing was deleted). The folder is:
ar.DataKeptPrompt=تم الاحتفاظ بمجلد بيانات صفا (غير مميّز كمجلد بيانات صفا — لم يُحذف شيء). المجلد هو:

[Code]
var
  DataDirPage: TInputDirWizardPage;
  UninstDataDir: string;   { captured at usUninstall, used at usPostUninstall }

{ installed.json stays pure ASCII: backslash/quote escapes plus \uXXXX
  for every char > 127 — so Arabic and spaced data paths survive on any
  codepage (SaveStringToFile cannot emit a UTF-8 BOM). }
function JsonEscape(S: string): string;
var
  i: Integer;
  c: Char;
  R: string;
begin
  R := '';
  for i := 1 to Length(S) do
  begin
    c := S[i];
    if c = '\' then
      R := R + '\\'
    else if c = '"' then
      R := R + '\"'
    else if Ord(c) > 127 then
      R := R + '\u' + Format('%.4x', [Ord(c)])
    else
      R := R + c;
  end;
  Result := R;
end;

function JsonUnescape(S: string): string;
var
  i: Integer;
  R: string;
begin
  R := '';
  i := 1;
  while i <= Length(S) do
  begin
    if (S[i] = '\') and (i < Length(S)) then
    begin
      if (S[i+1] = 'u') and (i + 5 <= Length(S)) then
      begin
        R := R + Chr(StrToInt('$' + Copy(S, i+2, 4)));
        i := i + 6;
      end else begin
        if S[i+1] = '\' then R := R + '\'
        else if S[i+1] = '"' then R := R + '"'
        else if S[i+1] = 'n' then R := R + #10
        else if S[i+1] = 'r' then R := R + #13
        else if S[i+1] = 't' then R := R + #9
        else if S[i+1] = '/' then R := R + '/';
        i := i + 2;
      end;
    end else begin
      R := R + S[i];
      i := i + 1;
    end;
  end;
  Result := R;
end;

function DataDirParam(): string;
begin
  Result := ExpandConstant('{param:DATADIR|}');
  if Result <> '' then Result := ExpandFileName(Result);
end;

function SafaDataDir(): string;
begin
  Result := DataDirParam();
  if (Result = '') and Assigned(DataDirPage) then
    Result := DataDirPage.Values[0];
  if Result = '' then
    Result := ExpandConstant('{localappdata}\Safa');
end;

procedure CreateDataDirPage(AfterID: Integer);
begin
  DataDirPage := CreateInputDirPage(
    AfterID,
    CustomMessage('DataPageCaption'),
    CustomMessage('DataPageDescription'),
    CustomMessage('DataPageSubCaption'),
    False, '');
  DataDirPage.Add(ExpandConstant('{cm:BrowseData}'));
  DataDirPage.Values[0] := DataDirParam();
  if DataDirPage.Values[0] = '' then
    DataDirPage.Values[0] := ExpandConstant('{localappdata}\Safa');
end;

{ True when the folder does not exist or contains nothing at all.
  FindFirst matches the '.' / '..' entries — those don't count. }
function DirIsEmpty(const D: string): Boolean;
var
  FindRec: TFindRec;
begin
  Result := True;
  if FindFirst(AddBackslash(D) + '*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
          Result := False;
      until (not Result) or (not FindNext(FindRec));
    finally
      FindClose(FindRec);
    end;
  end;
end;

procedure WriteInstalledJson(const ModelsDir: string);
var
  J: string;
  FreshDir: Boolean;
begin
  { Decide BEFORE anything lands in the folder: a directory that already
    had user files is never marked as Safa-owned (R2: the uninstaller
    keys the data-deletion offer off the marker). }
  FreshDir := (not DirExists(SafaDataDir())) or DirIsEmpty(SafaDataDir());
  ForceDirectories(SafaDataDir());
  J := '{"data_dir":"' + JsonEscape(SafaDataDir()) + '"';
  if ModelsDir <> '' then
    J := J + ',"models_dir":"' + JsonEscape(ModelsDir) + '"';
  J := J + ',"app_version":"{#AppVer}","variant":"{#VariantName}"}';
  if not SaveStringToFile(ExpandConstant('{app}\installed.json'), J, False) then
    Log('warning: could not write installed.json');
  { .safa-data marks the folder as Safa-owned: the uninstaller offers to
    delete a data dir ONLY when this marker exists — a user-picked
    Documents folder or drive root is never offered for deletion.
    The app re-creates the marker on startup (backend/core/paths.py)
    so an upgrade does not lose it. }
  if FreshDir then
  begin
    if not SaveStringToFile(SafaDataDir() + '\.safa-data',
                            'safa {#AppVer}' + #13#10, False) then
      Log('warning: could not write .safa-data marker');
  end
  else
    Log('data folder was not empty before install — not marking ' +
        'as Safa-owned: ' + SafaDataDir());
end;

{ Reads data_dir back from installed.json — used by the uninstaller so the
  prompt always shows the real path even if the user moved the folder
  after install (they must re-pick it manually then; we never guess).
  The file is pure ASCII by construction (see JsonEscape). }
function ReadInstalledDataDir(): string;
var
  J: AnsiString;
  K: string;
  P: Integer;
begin
  Result := '';
  if not LoadStringFromFile(ExpandConstant('{app}\installed.json'), J) then
    exit;
  K := '"data_dir":"';
  P := Pos(K, string(J));
  if P = 0 then
    exit;
  Result := Copy(string(J), P + Length(K), MaxInt);
  Result := Copy(Result, 1, Pos('"', Result) - 1);
  Result := JsonUnescape(Result);
end;

{ True when A equals B or one is nested inside the other (case-insensitive,
  trailing-backslash normalised). }
function PathsOverlap(A, B: string): Boolean;
begin
  A := Lowercase(AddBackslash(A));
  B := Lowercase(AddBackslash(B));
  Result := (Pos(A, B) = 1) or (Pos(B, A) = 1);
end;

// DelTree an untracked app-dir subfolder, unless it overlaps a data
// folder that still exists.
procedure SweepAppSubdir(Sub, DataDir: string);
begin
  if (DataDir <> '') and DirExists(DataDir) and PathsOverlap(Sub, DataDir) then
  begin
    Log('uninstall: keeping ' + Sub + ' (overlaps data folder ' + DataDir + ')');
    exit;
  end;
  DelTree(Sub, True, True, True);
end;

{ Delete ONLY entries the app itself creates under the data folder —
  enumerated from backend/core/paths.py + settings + storage:
  app.db* (the SQLite db and its WAL/journal sidecars), jobs/, media/,
  logs/, models/, tmp/, and the marker. Anything else the folder holds
  is the user's and survives (R2 #9). }
procedure DeleteOwnedDataEntries(const D: string);
begin
  DeleteFile(D + '\app.db');
  DeleteFile(D + '\app.db-wal');
  DeleteFile(D + '\app.db-shm');
  DeleteFile(D + '\app.db-journal');
  DeleteFile(D + '\app.db.safa-part');
  DelTree(D + '\jobs', True, True, True);
  DelTree(D + '\media', True, True, True);
  DelTree(D + '\logs', True, True, True);
  DelTree(D + '\models', True, True, True);
  DelTree(D + '\tmp', True, True, True);
  DeleteFile(D + '\.safa-data');
end;

// Uninstall: app files are removed by Inno automatically. The data
// folder is NOT under the app dir and survives. data_dir is read into a
// global at usUninstall — at usPostUninstall installed.json is already
// gone. Deletion is offered ONLY when <data>\.safa-data exists (default:
// keep); a dir without the marker is shown and kept — never DelTree'd.
// On "Yes" only Safa-owned entries are deleted; the folder itself is
// removed only when nothing foreign remains inside.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  D, Marker: string;
begin
  if CurUninstallStep = usUninstall then
    UninstDataDir := ReadInstalledDataDir();
  if CurUninstallStep = usPostUninstall then
  begin
    D := UninstDataDir;
    if (D <> '') and DirExists(D) then
    begin
      Marker := D + '\.safa-data';
      if FileExists(Marker) then
      begin
        if SuppressibleMsgBox(
             CustomMessage('DeleteDataPrompt') + #13#10#13#10 + D,
             mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        begin
          DeleteOwnedDataEntries(D);
          RemoveDir(D);   { Inno removes only an empty directory }
        end;
      end
      else
        SuppressibleMsgBox(
          CustomMessage('DataKeptPrompt') + #13#10#13#10 + D,
          mbInformation, MB_OK, IDOK);
    end;
    // installed.json is written by code at install time — Inno does not
    // track it, so delete it ourselves or the app dir lingers. Same for
    // the runtime tree (python/, pyroot/) provisioned by
    // install-runtime.ps1, data/.unblocked written by the launcher, and
    // {app}\models — its stage files are Inno-tracked but first-run model
    // prefetch writes NEW files there that Inno never tracked.
    // Each sweep is skipped when it overlaps a data folder that still
    // exists (the user kept it, or picked a data folder inside {app}) —
    // kept user data is never removed without the prompt above.
    DeleteFile(ExpandConstant('{app}\installed.json'));
    SweepAppSubdir(ExpandConstant('{app}\python'), UninstDataDir);
    SweepAppSubdir(ExpandConstant('{app}\pyroot'), UninstDataDir);
    SweepAppSubdir(ExpandConstant('{app}\data'), UninstDataDir);
    SweepAppSubdir(ExpandConstant('{app}\models'), UninstDataDir);
    RemoveDir(ExpandConstant('{app}'));
  end;
end;
