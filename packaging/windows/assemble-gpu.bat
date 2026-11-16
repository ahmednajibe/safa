@echo off
rem assemble-gpu.bat — join & extract the split Safa GPU portable
rem archive (M10c §9.4). Put this file next to ALL downloaded parts:
rem   Safa-<ver>-win-x64-gpu-portable.7z.001 ... .7z.NNN
rem   Safa-<ver>-win-x64-gpu-portable-SHA256SUMS.txt
rem   7z.exe + 7z.dll   (bundled beside this script)
rem Every part is verified against SHA256SUMS.txt before extraction.
rem Missing / corrupt / mixed-version parts refuse the assembly.
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

set "SUMS="
for %%f in ("Safa-*-win-x64-gpu-portable-SHA256SUMS.txt") do set "SUMS=%%f"
if not defined SUMS (
    echo [EN] SHA256SUMS.txt not found next to this script.
    echo [AR] ملف SHA256SUMS.txt غير موجود بجانب هذا الملف.
    exit /b 2
)

set "BASE=%SUMS:-SHA256SUMS.txt=%.7z"
set "FIRST=%BASE%.001"
if not exist "%FIRST%" (
    echo [EN] First part %FIRST% is missing - download every .7z.NNN file.
    echo [AR] الجزء الأول %FIRST% غير موجود - نزّل كل ملفات .7z.NNN
    exit /b 2
)

rem -- verify every part listed in SHA256SUMS.txt ------------------------------
set "COUNT=0"
set "BAD=0"
for /f "usebackq tokens=1,*" %%a in ("%SUMS%") do (
    set "H=%%a"
    set "F=%%b"
    if defined F (
        if not exist "!F!" (
            echo [EN] MISSING part: !F!
            echo [AR] جزء مفقود: !F!
            set "BAD=1"
        ) else (
            set "GOT="
            for /f "usebackq skip=1 delims=" %%h in (`certutil -hashfile "!F!" SHA256`) do (
                if not defined GOT set "GOT=%%h"
            )
            set "GOT=!GOT: =!"
            if /i not "!GOT!"=="!H!" (
                echo [EN] CORRUPT part: !F!
                echo [AR] جزء تالف: !F!
                set "BAD=1"
            ) else (
                set /a COUNT+=1
            )
        )
    )
)
if "!BAD!"=="1" (
    echo.
    echo [EN] Assembly refused - re-download the listed part^(s^) into this folder.
    echo [AR] تم رفض التجميع - أعد تنزيل الأجزاء المذكورة في هذا المجلد.
    exit /b 2
)

rem -- every listed part must exist AND every .7z.* part must be listed --------
set "EXTRA=0"
for %%f in ("%BASE%.*") do (
    findstr /l /c:" %%f" "%SUMS%" >nul || (
        echo [EN] UNLISTED part ^(wrong version?^): %%f
        echo [AR] جزء غير مدرج ^(نسخة خاطئة؟^): %%f
        set "EXTRA=1"
    )
)
if "!EXTRA!"=="1" exit /b 2

if not exist "%~dp07z.exe" (
    echo [EN] 7z.exe missing - keep it next to this script.
    echo [AR] ملف 7z.exe غير موجود - أبقه بجانب هذا الملف.
    exit /b 2
)

rem -- free space: extracted ~= 2x total archive, checked via PowerShell -------
powershell -NoProfile -Command "$p=0; Get-ChildItem '%BASE%.*' -File | ForEach-Object { $p += $_.Length }; try { $f = (Get-PSDrive -Name '%~d0'.TrimEnd(':')).Free } catch { exit 0 }; exit [int]($f -lt ($p * 2))"
if errorlevel 1 (
    echo [EN] Not enough free disk space on this drive - free about twice the archive size.
    echo [AR] لا توجد مساحة حرة كافية على هذا القرص - وفّر ضعف حجم الأرشيف تقريباً.
    exit /b 2
)

rem -- extract -----------------------------------------------------------------
echo [EN] Verified !COUNT! part^(s^) - extracting %FIRST% ...
echo [AR] تم التحقق من !COUNT! جزء - جارٍ فك ضغط %FIRST% ...
"%~dp07z.exe" x -y "%FIRST%"
if errorlevel 1 (
    echo [EN] Extraction failed - check free disk space and re-run.
    echo [AR] فشل فك الضغط - تحقق من المساحة الحرة وأعد المحاولة.
    exit /b 3
)

echo.
echo [EN] Done. Start Safa with Safa.exe inside the extracted folder.
echo [AR] تم. شغّل صفا من Safa.exe داخل المجلد المفكوك.
exit /b 0
