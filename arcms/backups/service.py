"""إنشاء النسخ الاحتياطية المشفّرة واستعادتها."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone

from arcms.audit.models import Action
from arcms.audit.services import record

from . import crypto
from .models import BackupRecord

FORMAT_VERSION = 1


class BackupError(Exception):
    pass


def _db() -> dict:
    return connection.settings_dict


def _pg_env(db: dict) -> dict:
    env = os.environ.copy()
    env.update(
        {
            "PGHOST": db.get("HOST") or "",
            "PGPORT": str(db.get("PORT") or ""),
            "PGUSER": db.get("USER") or "",
            "PGPASSWORD": db.get("PASSWORD") or "",
            "PGDATABASE": db["NAME"],
        }
    )
    return {k: v for k, v in env.items() if v != ""}


def dump_database(workdir: Path) -> Path:
    db = _db()
    if connection.vendor == "postgresql":
        out = workdir / "db.pgdump"
        pg_dump = shutil.which("pg_dump")
        if not pg_dump:
            raise BackupError("الأداة pg_dump غير مثبتة على الخادم.")
        proc = subprocess.run(
            [pg_dump, "--format=custom", "--no-owner", "--no-privileges", "--file", str(out)],
            env=_pg_env(db),
            capture_output=True,
            text=True,
            timeout=6 * 3600,
        )
        if proc.returncode != 0:
            raise BackupError(f"فشل pg_dump: {proc.stderr[-500:]}")
        return out
    out = workdir / "db.sqlite3"
    name = str(db["NAME"])
    dst = sqlite3.connect(out)
    try:
        if Path(name).is_file():
            # اتصال قراءة مستقل: ينسخ ما أُكّد فقط، ولا يتعطل بمعاملة مفتوحة في اتصال التطبيق.
            src = sqlite3.connect(f"file:{name}?mode=ro", uri=True, timeout=60)
            try:
                src.backup(dst)
            finally:
                src.close()
        else:  # قاعدة في الذاكرة (الاختبارات)
            connection.ensure_connection()
            dst.executescript("\n".join(connection.connection.iterdump()))
    finally:
        dst.close()
    return out


def _counts() -> dict:
    from arcms.content.models import Article, MediaAsset

    return {"articles": Article.objects.count(), "media": MediaAsset.objects.count()}


def create_backup(*, include_media: bool = True, trigger: str = "manual", public_key: str | None = None,
                  passphrase: str | None = None) -> BackupRecord:
    public_key = public_key or settings.ARCMS_BACKUP_PUBLIC_KEY
    if not public_key and not passphrase:
        raise BackupError("لا يوجد مفتاح عام للنسخ (ARCMS_BACKUP_PUBLIC_KEY). أنشئه بـ manage.py arcms_backup_keygen")
    backup_dir = Path(settings.ARCMS_BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    filename = f"arcms-{stamp}.arcbak"
    rec = BackupRecord.objects.create(
        filename=filename,
        includes_media=include_media,
        trigger=trigger,
        key_fingerprint=crypto.fingerprint(public_key) if public_key else "passphrase",
    )
    target = backup_dir / filename
    partial = target.with_suffix(".partial")
    try:
        with tempfile.TemporaryDirectory(prefix="arcms-bk-") as tmp:
            os.chmod(tmp, 0o700)
            workdir = Path(tmp)
            db_file = dump_database(workdir)
            manifest = {
                "format": FORMAT_VERSION,
                "created_at": timezone.now().isoformat(),
                "database": connection.vendor,
                "db_file": db_file.name,
                "media": include_media,
                "counts": _counts(),
                "site_url": settings.SITE_URL,
            }
            (workdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            with open(partial, "wb") as raw:
                os.chmod(partial, 0o600)
                enc = crypto.EncryptingWriter(raw, public_key=public_key, passphrase=passphrase)
                with tarfile.open(fileobj=enc, mode="w|gz") as tar:
                    tar.add(workdir / "manifest.json", arcname="manifest.json")
                    tar.add(db_file, arcname=db_file.name)
                    media_root = Path(settings.MEDIA_ROOT)
                    if include_media and media_root.exists():
                        tar.add(media_root, arcname="media")
                enc.close()
        partial.rename(target)
        digest = hashlib.sha256()
        with open(target, "rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(block)
        rec.status = BackupRecord.Status.OK
        rec.size = target.stat().st_size
        rec.sha256 = digest.hexdigest()
    except Exception as exc:
        partial.unlink(missing_ok=True)
        rec.status = BackupRecord.Status.FAILED
        rec.error = str(exc)[:2000]
        rec.finished_at = timezone.now()
        rec.save()
        record(Action.BACKUP, rec, message=f"فشل النسخ الاحتياطي: {exc}"[:500])
        raise
    rec.finished_at = timezone.now()
    rec.save()
    record(Action.BACKUP, rec, message=f"نسخة احتياطية مشفّرة ({rec.size // 1024} ك.ب)")
    rotate()
    return rec


def rotate(keep: int | None = None) -> int:
    keep = keep or settings.ARCMS_BACKUP_KEEP
    backup_dir = Path(settings.ARCMS_BACKUP_DIR)
    files = sorted(backup_dir.glob("arcms-*.arcbak"), reverse=True)
    removed = 0
    for old in files[keep:]:
        old.unlink(missing_ok=True)
        removed += 1
    return removed


def inspect_backup(path: Path, *, private_key: str | None = None, passphrase: str | None = None) -> dict:
    """يفك النسخة ويتحقق من سلامتها كاملة دون استعادتها، ويعيد البيان."""
    manifest = None
    members = 0
    with open(path, "rb") as raw:
        dec = crypto.DecryptingReader(raw, private_key=private_key, passphrase=passphrase)
        with tarfile.open(fileobj=dec, mode="r|gz") as tar:
            for member in tar:
                members += 1
                if member.name == "manifest.json":
                    manifest = json.loads(tar.extractfile(member).read().decode("utf-8"))
                elif member.isfile():
                    fh = tar.extractfile(member)
                    while fh.read(1024 * 1024):
                        pass
    if manifest is None:
        raise BackupError("النسخة لا تحتوي بياناً (manifest).")
    manifest["members"] = members
    return manifest


def restore_backup(path: Path, *, private_key: str | None = None, passphrase: str | None = None,
                   restore_media: bool = True) -> dict:
    """استعادة كاملة: تستبدل قاعدة البيانات والوسائط الحالية. عملية مدمّرة."""
    with tempfile.TemporaryDirectory(prefix="arcms-rs-") as tmp:
        os.chmod(tmp, 0o700)
        workdir = Path(tmp)
        with open(path, "rb") as raw:
            dec = crypto.DecryptingReader(raw, private_key=private_key, passphrase=passphrase)
            with tarfile.open(fileobj=dec, mode="r|gz") as tar:
                tar.extractall(workdir, filter="data")
        manifest = json.loads((workdir / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("database") != connection.vendor:
            raise BackupError(
                f"النسخة من قاعدة {manifest.get('database')} والخادم يعمل على {connection.vendor}."
            )
        name = str(manifest.get("db_file", ""))
        if not name or name != Path(name).name or name.startswith("."):
            raise BackupError("اسم ملف القاعدة في بيان النسخة غير صالح.")
        db_file = workdir / name
        if not db_file.is_file():
            raise BackupError("ملف القاعدة غير موجود في النسخة.")
        db = _db()
        if connection.vendor == "postgresql":
            connection.close()
            proc = subprocess.run(
                [shutil.which("pg_restore") or "pg_restore", "--clean", "--if-exists", "--no-owner",
                 "--no-privileges", "--single-transaction", "--dbname", db["NAME"], str(db_file)],
                env=_pg_env(db),
                capture_output=True,
                text=True,
                timeout=6 * 3600,
            )
            if proc.returncode != 0:
                raise BackupError(f"فشل pg_restore: {proc.stderr[-800:]}")
        else:
            connection.close()
            shutil.copyfile(db_file, db["NAME"])
        media_src = workdir / "media"
        if restore_media and media_src.exists():
            media_root = Path(settings.MEDIA_ROOT)
            media_root.mkdir(parents=True, exist_ok=True)
            # نفرّغ المحتوى لا المجلد نفسه: قد يكون نقطة تركيب (volume) لا تُحذف.
            for child in media_root.iterdir():
                shutil.rmtree(child) if child.is_dir() else child.unlink()
            shutil.copytree(media_src, media_root, dirs_exist_ok=True)
    return manifest
