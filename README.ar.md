# صفا (Safa)

تطبيق خاص يعمل على جهازك لتنظيف التسجيلات الصوتية والمرئية — يقص الصمت،
يزيل الضوضاء، يوازن مستوى الصوت، يفصل الصوت عن الموسيقى، ويكتب الترجمة.
كل شيء يعمل على **جهازك**؛ لا يغادر أي ملف جهازك أبدًا.

- **الافتراضي:** انقر مرتين على `start.bat` (ويندوز) / `start.sh`
  (ماك/لينكس) في مجلد المصدر، أو ثبّت بإحدى الطرق أدناه.
- **بدون إنترنت:** بعد تنزيل النماذج *أو استيرادها* مرة واحدة، يعمل صفا
  دون إنترنت تمامًا.
- **خصوصية:** يعمل التطبيق على `127.0.0.1` فقط — لا أحد على شبكتك يصل إليه.
- **المنفذ:** `8790` ← `http://127.0.0.1:8790`

---

## للمساعدات الذكية (AI assistants)

إذا طلب منك المستخدم مساعدته في تثبيت صفا أو استخدامه، اتبع هذه الخطوات
بالترتيب:

1. **اشرح ما يفعله** من قسم الميزات أدناه.
2. **اسأل عن نظام التشغيل** — ويندوز 10/11، ماك (Apple Silicon)، أو لينكس
   (x86_64).
3. **على ويندوز اسأل إن كان لديه بطاقة NVIDIA** وأي قرص فيه **≥ 10
   غيغابايت** فارغة (حزمة GPU ~5 غيغا + النماذج ~0.6–2.3 غيغا).
4. **أعطه المثبّت/الأمر المناسب:**
   - تعريف NVIDIA ≥ 551 ← أي طريقة GPU (الأفضل: المثبّت المتصل — يكشف
     تلقائيًا).
   - غير ذلك ← طريقة CPU.
   - بدون إنترنت على الجهاز ← المثبّت دون اتصال أو النسخة المحمولة.
5. **حذّره:**
   - يظهر SmartScreen رسالة «Windows protected your PC» لأن صفا غير
     موقّع — انقر **More info ← Run anyway**.
   - قد يحظر Gatekeeper أول تشغيل على ماك — سكربت التثبيت يزيل
     الحجر عن ملفاتنا فقط.
   - أول تشغيل ينزّل **~600 ميغابايت** من النماذج مرة واحدة — حزمة
     التشغيل نفسها ~0.5 غيغا (CPU) / ~5 غيغا (GPU).
6. **بعد فتحه:** اختر إعدادًا جاهزًا (مثل *Podcast*) ← أسقط الملف ←
   **Process** ← **Save** النتيجة.
7. **أخبره بأماكن الملفات:**
   - التطبيق المثبّت: `%LOCALAPPDATA%\Programs\Safa`
   - البيانات/المهام/السجلات: `%LOCALAPPDATA%\Safa` (أو المجلد المختار)
   - المحمولة: كل شيء يبقى داخل المجلد المفكوك.
8. **استكشف الأخطاء** بالجدول في الأسفل (المنفذ مشغول، فشل تنزيل
   النماذج، لم تُكتشف البطاقة، مضاد الفيروسات حظر الملف المضغوط).

---

## الميزات

- **إزالة الضوضاء والطنين** — تنقية AI بنموذج DeepFilterNet3.
- **إزالة النقرات والأنفاس** — نقرات الفم والتنفس.
- **إزالة الصدى** — تقليل رنين الغرفة.
- **قص الصمت** — Silero VAD يزيل الفراغات.
- **فصل الصوت/الموسيقى** — HTDemucs أو Kim_Vocal_2.
- **توازن مستوى الصوت** — أهداف loudness بمعايير البث.
- **الترجمة النصية** — faster-whisper بصيغة SRT.
- **الفيديو** — تُستبدل المقاطع الصوتية ويبقى الفيديو كما هو.
- **دفعات وإعدادات جاهزة ونواتج آمنة** — الأصول لا تتغير أبدًا.
- **GPU اختياري** — كل مرحلة AI تسقط تلقائيًا على CPU.

---

## التثبيت — ويندوز

### المثبّت المتصل (موصى به)
<!-- method:win-online -->

`Safa-<ver>-win-x64-setup.exe` — **تنزيل ~50 ميغابايت** ثم يجلب بيئة
التشغيل (~1 غيغا CPU / ~5 غيغا GPU) أثناء التثبيت.

- لا يحتاج صلاحيات مسؤول؛ يثبّت في `%LOCALAPPDATA%\Programs\Safa`.
- معالج ثنائي اللغة (العربية/English)، اختصارات قائمة ابدأ وسطح المكتب.
- يكشف NVIDIA عبر `nvidia-smi` — يقترح CUDA إذا كان التعريف ≥ 551 ويمكنك
  اختيار CPU.
- يتيح اختيار مجلد **البيانات** (المهام والنواتج والنماذج) — GPU يحتاج
  ≥ 10 غيغا فارغة.
- يحتاج إنترنت **أثناء التثبيت**؛ بعدها يعمل دون اتصال عدا تنزيل
  النماذج (~600 ميغا أول مرة).
- غير موقّع ← SmartScreen: **More info ← Run anyway**.

### مثبّت دون اتصال — CPU
<!-- method:win-offline-cpu -->

`Safa-<ver>-win-x64-cpu-offline-setup.exe` — **ملف واحد < 2 غيغابايت** فيه
بيئة تشغيل CPU كاملة وكل النماذج الأساسية. لا يحتاج إنترنت إطلاقًا.

### مثبّت دون اتصال — GPU
<!-- method:win-offline-gpu -->

`Safa-<ver>-win-x64-gpu-offline-setup.exe` **مع** شرائح `-1.bin … -N.bin`
(كل شريحة ≤ 1.8 غيغا، ~6 غيغا إجمالًا). بيئة التشغيل والنماذج مضمّنة؛
مناسب للنسخ على USB. احتفظ بكل الشرائح بجانب الملف التنفيذي — الشرائح
الناقصة أو المختلطة الإصدارات تجعل التثبيت يرفض البدء.

### محمولة — ملف CPU مضغوط
<!-- method:win-portable-cpu -->

`Safa-<ver>-win-x64-cpu-portable.zip` — **~0.5 غيغا**. فك الضغط في مسار
قصير خارج OneDrive (مثل `D:\Safa`) وشغّل `Safa.exe` أو `start.bat`. كل
شيء — البيانات والنماذج — يبقى داخل المجلد. تنزيل ~600 ميغا للنماذج
أول تشغيل.

### محمولة — أرشيف GPU مجزأ
<!-- method:win-portable-gpu -->

`Safa-<ver>-win-x64-gpu-portable.7z.001` … `.NNN` (كل جزء ≤ 1.8 غيغا) مع
`…-SHA256SUMS.txt`. نزّل كل الأجزاء في مجلد واحد ثم شغّل
**`assemble-gpu.bat`** المضمّن — يتحقق من بصمة كل جزء ومساحة القرص ثم يفك
الضغط بـ`7z.exe` المضمّن. الأجزاء التالفة أو الناقصة تُرفض برسالة
ثنائية اللغة.

### التثبيت بأمر واحد (PowerShell)
<!-- method:win-one-liner -->

```powershell
irm https://github.com/ahmednajibe/safa/releases/latest/download/install.ps1 | iex
```

ينزّل السكربت `SHA256SUMS.txt` مع `Safa-<ver>-win-x64-setup.exe` ويتحقق
من بصمة SHA-256 (غياب السطر أو عدم التطابق يوقف التثبيت ويحذف التنزيل)
ثم يشغّل مثبّت Inno صامتًا. لا يحذف أي شيء داخل `-InstallDir` —
الترقية يديرها المثبّت نفسه. قيمة `latest` يستعلم عنها السكربت من GitHub
لأن اسم الملف يحمل رقم الإصدار؛ مرّر `-Version` لتثبيت إصدار محدد.

لتمرير معلمات عبر الأمر الواحد:

```powershell
& ([scriptblock]::Create((irm https://github.com/ahmednajibe/safa/releases/latest/download/install.ps1))) -Variant gpu
```

معلمات اختيارية: `-Variant cpu|gpu`، `-DataDir D:\SafaData`،
`-InstallDir ...`، `-Offline -SetupExe <ملف المثبّت>` (يستخدم مثبّتًا
محليًا — المتصل أو cpu-offline أو gpu-offline — ويتحقق منه عند وجود
`SHA256SUMS.txt` بجانبه)، `-DryRun`.

البديل الأكثر أمانًا — *نزّل ← افحص ← شغّل*:

```powershell
iwr https://github.com/ahmednajibe/safa/releases/latest/download/install.ps1 -OutFile install.ps1
notepad install.ps1   # اقرأ السكربت
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

### winget
<!-- method:win-winget -->

```powershell
winget install --id Safa.Safa
```

يغلّف المثبّت المتصل (نفس الحجم ونفس ملاحظة SmartScreen).

### Scoop
<!-- method:win-scoop -->

```powershell
scoop install https://github.com/ahmednajibe/safa/raw/main/packaging/scoop/safa.json
```

يستخدم ملف CPU المحمول؛ لنسخة GPU استخدم الأرشيف المجزأ أعلاه (Scoop لا
يجمع الأجزاء المقسّمة).

### Chocolatey
<!-- method:win-choco -->

```powershell
choco install safa --params "/Variant:gpu /DataDir:D:\SafaData"
```

يغلّف المثبّت المتصل بصمت — نفس ملف setup.exe (~50 ميغا) مع تنزيل بيئة
التشغيل أثناء التثبيت (يحتاج إنترنت). `--params` تتحول إلى معلمات Inno:
`/Variant:cpu|gpu|auto` (الافتراضي `auto` — يكشف تعريف NVIDIA)،
`/DataDir:<مجلد>` للمهام والنواتج والنماذج، `/InstallDir:<مجلد>`
للتطبيق. `choco uninstall safa` يشغّل مزيل Inno — مجلد بياناتك يبقى.
غير موقّع — نفس ملاحظة SmartScreen أعلاه.

### من المصدر (ويندوز)
<!-- method:win-source -->

```powershell
git clone https://github.com/ahmednajibe/safa.git; cd safa
uv sync --extra cu124   # أو: --extra cpu
npm --prefix frontend ci; npm --prefix frontend run build
.\start.bat
```

يحتاج `uv` وNode.js. ملف `Safa-<ver>-source.zip` هو نفس الشجرة.

---

## التثبيت — ماك (تجريبي، Apple Silicon)

### التثبيت بأمر واحد
<!-- method:mac-install-sh -->

```sh
curl -fsSL https://github.com/ahmednajibe/safa/releases/latest/download/install.sh | sh
```

يثبّت في `~/Applications/Safa`، البيانات في
`~/Library/Application Support/Safa`، ويضع **Safa.command** على سطح
المكتب. حجر Gatekeeper يُزال من مجلد التطبيق فقط. أرشيف ~0.5 غيغا —
تُتحقق بصمة SHA-256 للأرشيف مقابل `SHA256SUMS.txt` المنشور قبل فك الضغط
(`--from-dir <مجلد>` يثبّت من أرشيف محلي بنفس الطريقة).

### ملف ‎.dmg (تطبيق غير موقّع)
<!-- method:mac-dmg -->

`Safa-<ver>-macos-arm64.dmg` — **~0.7 غيغا**، بلا مثبّت: افتحه واسحب
**Safa.app** إلى أي مكان (مثل `/Applications`). بيئة التشغيل وffmpeg
داخل الحزمة — لا يحتاج إنترنت عدا تنزيل النماذج أول مرة (~600 ميغا).

- **غير موقّع**: أول تشغيل عبر **Right-click ← Open** — يسأل Gatekeeper
  مرة واحدة. البناء لا يزيل الحجر عنك أبدًا؛ إزالته يدويًا
  (`xattr -dr com.apple.quarantine Safa.app`) خيارك أنت.
- البيانات في `~/Library/Application Support/Safa`.

### Homebrew
<!-- method:mac-brew -->

```sh
brew install --formula https://github.com/ahmednajibe/safa/raw/main/packaging/homebrew/safa.rb
safa
```

### أرشيف محمول
<!-- method:mac-portable -->

`Safa-<ver>-macos-arm64.tar.gz` — فك الضغط في أي مكان وشغّل
`python/bin/python3 -m backend.launcher` من الداخل.

### من المصدر (ماك)
<!-- method:mac-source -->

```sh
git clone https://github.com/ahmednajibe/safa.git; cd safa
uv sync --extra cpu     # لا توجد نسخة CUDA على ماك
npm --prefix frontend ci; npm --prefix frontend run build
./start.command        # أو ./start.sh
```

---

## التثبيت — لينكس (تجريبي، x86_64)

### التثبيت بأمر واحد
<!-- method:linux-install-sh-cpu -->
<!-- method:linux-install-sh-cuda -->

```sh
curl -fsSL https://github.com/ahmednajibe/safa/releases/latest/download/install.sh | sh
curl -fsSL .../install.sh | sh -s -- --variant cuda   # NVIDIA
```

يثبّت في `~/.local/share/safa`، البيانات في `~/.local/share/safa-data`،
وينشئ `~/.local/bin/safa` وملف `.desktop`. `--variant cpu` (الافتراضي)
أو `--variant cuda`. تُتحقق بصمة SHA-256 للأرشيف مقابل `SHA256SUMS.txt`
المنشور قبل فك الضغط.

`--variant cuda` يثبّت حمولة المعالج ثم ينزّل بيئة CUDA 12.4
(~5 غيغا، ببصمة مُتحققة من `requirements-cu124.txt` داخل الأرشيف)
بأداة `tools/uv` المرفقة — نفس آلية `safa setup --device cuda`. لا
يوجد `-cuda.tar.gz` في الإصدار: حمولة ~4.9 غيغا تتجاوز حد ملفات
GitHub البالغ 2 غيغابايت.

### حزمة ‎.deb (ديبيان / أوبونتو / مينت)
<!-- method:linux-deb-cpu -->
<!-- method:linux-deb-cuda -->

```sh
sudo apt install ./Safa-<ver>-linux-x64-cpu.deb
```

يثبّت بيئة التشغيل كاملة في `/opt/safa` مع أمر `safa` وعنصر قائمة.
**~0.8 غيغا** (CPU). لا تُنشر حزمة .deb بنسخة CUDA — حمولة ~4.9 غيغا
فوق حد ملفات GitHub البالغ 2 غيغابايت؛ لبطاقة NVIDIA شغّل
`install.sh --variant cuda` (تجهيز cu124 عبر الإنترنت) أو
`safa setup --device cuda` داخل حزمة المعالج المثبّتة. ffmpeg مضمّن؛
البيانات في `~/.local/share/safa-data` وتبقى بعد `apt purge`. لا يحتاج
إنترنت عدا تنزيل النماذج أول مرة (~600 ميغا).

### حزمة ‎.rpm (فيدورا)
<!-- method:linux-rpm-cpu -->
<!-- method:linux-rpm-cuda -->

```sh
sudo dnf install ./Safa-<ver>-linux-x64-cpu.rpm
```

نفس تخطيط ‎.deb وقواعدها — معالج فقط؛ يستخدم CUDA نفس مسار التجهيز
عبر الإنترنت (`install.sh --variant cuda` أو `safa setup --device cuda`).

### AppImage (CPU)
<!-- method:linux-appimage -->

```sh
chmod +x Safa-<ver>-linux-x64.AppImage
./Safa-<ver>-linux-x64.AppImage                          # يحتاج FUSE
./Safa-<ver>-linux-x64.AppImage --appimage-extract-and-run   # بدون FUSE
```

**~0.8 غيغا**، بلا تثبيت — البيانات في
`${XDG_DATA_HOME:-~/.local/share}/safa-data` (يتجاوزها
`APPIMAGE_DATA_DIR`). CPU فقط: AppImage بنسخة CUDA سيكون ~4.9 غيغا —
فوق حد 2 غيغابايت — استخدم `install.sh --variant cuda` أو
`safa setup --device cuda`.

### Flatpak (تجريبي)
<!-- method:linux-flatpak -->

```sh
flatpak install ./Safa-<ver>-linux-x64.flatpak
```

**تجريبي** — ليس على Flathub؛ تُنشر الحزمة في الإصدار فقط عند نجاح بنائها
في CI. هي محمّل صغير **~10 ميغا** داخل صندوق رملي: عند أول تشغيل ينزّل
بيئة التشغيل (~2 غيغا، ببصمة مُتحققة) ثم النماذج (~600 ميغا) إلى
`~/.var/app/org.safa.Safa/data/safa-data` — يحتاج إنترنت مرة واحدة.
أذونات الصندوق: `--share=network` (واجهة localhost + تلك التنزيلات)،
`--filesystem=home` (قراءة/كتابة ملفاتك)، `--device=dri` (GPU
اختياري). ملف `org.safa.Safa.yaml` (المانيفست) يُنشر بجانبها.

### AUR (آرتش)
<!-- method:linux-aur -->

```sh
yay -S safa        # معالج — لبطاقة NVIDIA شغّل `safa setup --device cuda`
                   # داخل الحزمة المثبّتة (لا يُنشر أرشيف cuda)
```

### الأرشيفات المحمولة
<!-- method:linux-portable-cpu -->
<!-- method:linux-portable-cuda -->

`Safa-<ver>-linux-x64-cpu.tar.gz` — فك الضغط وشغّل `./start.sh` من
الداخل. البيانات تبقى في المجلد. لا يُنشر `-cuda.tar.gz` (حد 2
غيغابايت): حدّث شجرة المعالج بالأمر `uv pip install --python
python/bin/python3 --system -r requirements-cu124.txt --require-hashes
--extra-index-url https://download.pytorch.org/whl/cu124
--index-strategy unsafe-best-match` عبر `tools/uv` المرفقة.

### من المصدر (لينكس)
<!-- method:linux-source -->

```sh
git clone https://github.com/ahmednajibe/safa.git; cd safa
uv sync --extra cu124   # NVIDIA؛ وإلا --extra cpu
npm --prefix frontend ci; npm --prefix frontend run build
./start.sh
```

يحتاج ffmpeg من النظام (`apt install ffmpeg`).

---

## التثبيت — Python (أي نظام)
<!-- method:pypi -->

```sh
pip install safa-audio        # أو: uv tool install safa-audio / pipx install safa-audio
safa setup --device auto      # cpu|cuda — ينزّل ~2 غيغا (cpu) / ~5 غيغا (cuda)
safa                          # يفتح http://127.0.0.1:8790
safa models import <pack.zip> # حزمة نماذج دون اتصال
```

حزمة bootstrap صغيرة **~1.5 ميغا**: أمر `safa` يجهّز بيئة التشغيل
الحقيقية (Python 3.11 + تبعيات مقفلة البصمة + ffmpeg) تحت مجلد بياناتك —
`%LOCALAPPDATA%\Safa` أو `~/Library/Application Support/Safa` أو
`${XDG_DATA_HOME:-~/.local/share}/safa-data` (يتجاوزها
`SOUND_SYSTEM_DATA_DIR`). يحتاج إنترنت لـ`setup` ولتنزيل النماذج أول
مرة (~600 ميغا)؛ ثم يعمل دون اتصال تمامًا. يتطلب Python 3.11+ ‏(3.11
تحديدًا عند غياب `uv`). على ماك ثبّت ffmpeg أولًا (`brew install
ffmpeg`). نفس الحزمة تُنشر في الإصدار كملف
`safa_audio-<ver>-py3-none-any.whl`.

---

## Docker

<!-- method:docker-cpu -->
<!-- method:docker-cuda -->

```sh
docker build -f Dockerfile -t safa:cpu .
docker run --rm -p 127.0.0.1:8790:8790 -v safa-data:/data safa:cpu

docker build -f Dockerfile.cuda -t safa:cuda .
docker run --rm --gpus all -p 127.0.0.1:8790:8790 -v safa-data:/data safa:cuda
```

- **دائمًا** انشر `127.0.0.1:8790:8790` — لا تستخدم `-p 8790:8790` أبدًا
  لأنه يعرض صفا على الشبكة المحلية.
- حجم الصورة ~1.5 غيغا (CPU) / ~8 غيغا (CUDA)؛ ffmpeg مضمّن.
- مجلد البيانات `/data` (المهام والنواتج والنماذج).
- `docker compose up safa-cpu` يفعل نفس الشيء.

### حزم النماذج دون اتصال
<!-- method:models-import -->

لأجهزة بلا إنترنت: نزّل `Safa-models-core-<ver>.zip` (~600 ميغا) أو
`Safa-models-all-<ver>.zip` (~2.3 غيغا) من مكان آخر ثم داخل مجلد
التطبيق:

```
python -m backend.launcher models import Safa-models-core-<ver>.zip
```

البصمات تُتحقق والاستيراد الجزئي يستكمل بأمان.

---

## الاستخدام

1. شغّل التطبيق (بأي طريقة) ← يفتح المتصفح على
   `http://127.0.0.1:8790`.
2. اختر **إعدادًا جاهزًا** (Podcast، Lecture، Music، …) أو فعّل الخطوات
   يدويًا.
3. **أسقط ملفًا** (wav/mp3/m4a/mp4/mkv/mov/…).
4. اضغط **Process**. تابع التقدم ونزّل النتيجة.
5. النواتج في `<data>/jobs/<id>/` — الملفات الأصلية لا تُعدّل أبدًا.

## أماكن الملفات

| ماذا                     | مثبّت (ويندوز)                    | محمولة                |
| ------------------------ | -------------------------------- | --------------------- |
| ملفات التطبيق            | `%LOCALAPPDATA%\Programs\Safa`   | المجلد المفكوك        |
| المهام والنواتج والسجلات | `%LOCALAPPDATA%\Safa` *أو المختار* | `<المجلد>\data`     |
| نماذج AI                 | `<data>\models`                  | `<المجلد>\models`    |
| المؤقتة والكاش           | `<data>\tmp`                     | `<المجلد>\data\tmp`  |

متغيرات `SOUND_SYSTEM_DATA_DIR` و`SOUND_SYSTEM_PORT` و
`SOUND_SYSTEM_BIND_HOST` و`SOUND_SYSTEM_MODELS_DIR` تتجاوز هذه الإعدادات.

## ملاحظات التوقيع والأمان

- **نسخ غير موقّعة** — SmartScreen وGatekeeper وبعض مضادات الفيروسات
  تحذّر. تحقق من `SHA256SUMS.txt` في صفحة الإصدار.
- صفا **لا يفتح منفذًا عامًا أبدًا** (`127.0.0.1` فقط؛ صورة Docker
  تربط 0.0.0.0 *داخل* الحاوية لكن الربط في المضيف أعلاه يبقيه محليًا).
- كل شيء محلي: لا telemetry ولا سحابة ولا رفع ملفات.

## استكشاف الأخطاء

| العَرَض                                        | الحل |
| ---------------------------------------------- | ---- |
| «Port 8790 is in use»                          | أغلق البرنامج الآخر أو `set SOUND_SYSTEM_PORT=8791` |
| يبدأ التطبيق لكن الصفحة فارغة                  | أعد بناء الواجهة (`npm --prefix frontend run build`) |
| «تعذّر تنزيل النماذج»                          | أعد المحاولة داخل التطبيق أو استورد حزمة نماذج دون اتصال |
| خطوات GPU تعمل على CPU                        | تعريف < 551 أو لا توجد بطاقة — حدّث التعريف وأعد التثبيت بنسخة gpu |
| SmartScreen يحظر المثبّت                       | **More info ← Run anyway** (نسخة غير موقعة) |
| ماك يقول التطبيق تالف/غير موثوق               | أعد تشغيل install.sh — يزيل الحجر عن ملفاتنا فقط |
| مضاد الفيروسات يعزل ملفات من المضغوط          | استرجعها وضع المجلد في الاستثناءات بعد تحقق SHA256 |
| أخطاء المسار الطويل / OneDrive                | انقل المجلد إلى مسار قصير خارج OneDrive |
| فشلت مهمة على GPU                             | تُعاد تلقائيًا على CPU؛ راجع `data/logs` |

## التطوير

```sh
uv sync --extra cu124        # بيئة التطوير (لـ CPU فقط: --extra cpu)
uv run pytest -m "not slow"  # المجموعة السريعة
cd frontend && npx vitest run && npm run build
```

السكربتات: `scripts/setup.ps1`، `start.bat`/`start.sh`،
`scripts/package.ps1` (حزم ويندوز)، `scripts/package-unix.sh`
(أرشيفات ماك/لينكس)، `scripts/build-installer.ps1` (مثبّتات Inno)،
`scripts/smoke_package.py` (فحص الحزمة الشامل).

خارطة الطريق: `docs/PLAN.md`. المواصفات: `docs/SPEC.md`.
