# صورة arcms للإنتاج: Python + عميل PostgreSQL 16 (للنسخ الاحتياطي) + الخطوط والملفات الثابتة.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# pg_dump يجب أن يطابق إصدار خادم PostgreSQL أو يفوقه؛ نثبّت الإصدار 16 من مستودع PGDG.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-16 \
 && apt-get purge -y gnupg && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --system --uid 1000 --home /app arcms \
 && mkdir -p /data/media /data/backups /data/imports /app/staticfiles \
 && ARCMS_SECRET_KEY=build-only ARCMS_DATABASE_URL=sqlite:////tmp/build.sqlite3 ARCMS_STATIC_ROOT=/app/staticfiles \
    python manage.py collectstatic --noinput -v0 \
 && rm -f /tmp/build.sqlite3 \
 && chown -R arcms:arcms /data /app/staticfiles \
 && chmod +x deploy/entrypoint.sh

USER arcms
VOLUME ["/data/media", "/data/backups"]
EXPOSE 8000
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["web"]
