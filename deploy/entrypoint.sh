#!/bin/sh
# نقطة الدخول: web (خادم الويب)، worker (العامل الخلفي)، أو أي أمر manage.py.
set -e
cd /app

wait_for_db() {
  python - <<'PY'
import sys, time
import django
django.setup()
from django.db import connection
for attempt in range(60):
    try:
        connection.ensure_connection()
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"… بانتظار قاعدة البيانات ({exc.__class__.__name__})", flush=True)
        time.sleep(2)
sys.exit("تعذّر الاتصال بقاعدة البيانات")
PY
}
export DJANGO_SETTINGS_MODULE=config.settings

case "$1" in
  web)
    wait_for_db
    python manage.py migrate --noinput
    # الملفات الثابتة يقدّمها Caddy من القرص مباشرة (لا تشغل عمليات الويب). أسماؤها تحمل بصمة
    # محتواها، فتبقى القديمة للصفحات المفتوحة وتُضاف الجديدة مع كل إصدار.
    if [ -d /data/static ]; then cp -a /app/staticfiles/. /data/static/; fi
    # سجل الوصول بلا عناوين IP: لا نحتفظ بما قد يُطلب لاحقاً لمعرفة من قرأ ماذا.
    exec gunicorn config.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers "${ARCMS_WEB_WORKERS:-3}" \
      --threads "${ARCMS_WEB_THREADS:-4}" \
      --timeout 90 \
      --no-control-socket \
      --max-requests 20000 --max-requests-jitter 2000 \
      --backlog 4096 \
      --forwarded-allow-ips="*" \
      --access-logfile - \
      --access-logformat '%(t)s "%(r)s" %(s)s %(b)s %(L)ss'
    ;;
  worker)
    wait_for_db
    # ننتظر حتى تُطبَّق الترحيلات من حاوية الويب.
    until python manage.py migrate --check >/dev/null 2>&1; do
      echo "… بانتظار ترحيلات قاعدة البيانات"
      sleep 3
    done
    exec python manage.py arcms_worker
    ;;
  *)
    exec python manage.py "$@"
    ;;
esac
