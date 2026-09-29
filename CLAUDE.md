# arcms — تعليمات التشغيل

منصة نشر إخبارية عربية بـ Django 5.2 وPython 3.11+. التفاصيل في README.md، والتجربة على خادم حقيقي في docs/PILOT.md.

## التشغيل المحلي (SQLite، دون Docker)

ملف `.env` في جذر المشروع يُقرأ تلقائياً. للتجربة المحلية يكفي أن يحتوي:

```
ARCMS_DEBUG=1
```

Windows (PowerShell):
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # إن رُفض: Set-ExecutionPolicy -Scope Process RemoteSigned
$env:PYTHONUTF8 = "1"
pip install -r requirements.txt
python manage.py migrate
python manage.py arcms_demo         # مرة واحدة: موقع تجريبي ومستخدمون وأسرار التحقق الثنائي
python manage.py runserver
```

macOS / Linux:
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py arcms_demo
python manage.py runserver
```

- الموقع: http://localhost:8000 — غرفة التحرير: http://localhost:8000/studio/
- الدخول: اسم مستخدم من ناتج `arcms_demo` (مثل `chief`) وكلمة المرور `zajel-demo-2026`، ثم رمز التحقق الثنائي من تطبيق مصادقة أُضيف إليه السر المطبوع (يُحفظ أيضاً في `var/demo-credentials.txt`).
- العامل الخلفي (النشر المجدول والتوزيع والنشرة) في طرفية ثانية بعد تفعيل البيئة: `python manage.py arcms_worker`
- لإعادة البدء من الصفر: احذف `var/arcms.sqlite3` ومجلد `media/` ثم أعد `migrate` و`arcms_demo`.
- هويات تجريبية أخرى: `python manage.py arcms_demo --brand sonbola` (أو `ofoq`). الصور الحقيقية تُنزّل من ويكيميديا كومنز وتُحفظ في `var/demo-photos`؛ دون إنترنت أضف `--no-photos`. التعريفات في `arcms/core/demo_brands.py` والشعارات في `arcms/core/demo_assets/logos`.

## الاختبارات والفحص

```bash
python manage.py test          # 418 اختباراً
ruff check arcms config
```

## البنية

- `arcms/arabic` مكتبة اللغة (تطبيع، تجذيع، بحث، تواريخ، مدقق الأسلوب `style.py`) — بلا اعتماد على Django إلا في التواريخ. دليل أسلوب المؤسسة في `SiteSettings.style_rules`، وواجهة الفحص `studio:api_style`.
- `arcms/content` المواد وسير العمل (`workflow.py`) والبحث (`search.py`) ونزع بيانات الصور (`imaging.py`) والصور القاسية (`sensitive.py`) وبطاقات المشاركة (`cards.py`، خطوطها في `card_fonts/`).
- `arcms/polls` استطلاعات القرّاء (رمز `[poll:رقم]` في المتن يُعرض عبر `services.render_shortcodes`).
- `arcms/planning` خطة التغطية (المرحلة تتبع المادة عبر `signals.py`).
- `arcms/wires` مكتب الوكالات (جلب RSS/Atom آمن في `feeds.py`، والاعتماد مسودةً في `services.py`).
- `arcms/studio` غرفة التحرير، `arcms/public` الموقع العام، `templates/` و`static/` بلا خطوة بناء.
- الصلاحيات في `arcms/accounts/roles.py`، والمهام الخلفية في `arcms/*/tasks.py` عبر `arcms/core/jobs.py`.
- الأسرار من البيئة فقط (`config/settings.py`، `.env.example`)، لا في قاعدة البيانات.
