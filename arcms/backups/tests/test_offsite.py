import datetime as dt
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import override_settings

from arcms.backups import offsite
from arcms.backups.models import BackupRecord
from arcms.core.jobs import run_pending
from arcms.core.models import Job
from arcms.core.testing import ArcmsTestCase

S3 = {
    "ARCMS_OFFSITE_ENDPOINT": "https://s3.example.org", "ARCMS_OFFSITE_BUCKET": "newsroom-backups",
    "ARCMS_OFFSITE_ACCESS_KEY": "AK", "ARCMS_OFFSITE_SECRET_KEY": "SK", "ARCMS_OFFSITE_REGION": "eu-central-1",
    "ARCMS_OFFSITE_PREFIX": "arcms", "ARCMS_OFFSITE_KEEP": 0,
}


def resp(status=200, headers=None, content=b""):
    r = mock.Mock()
    r.status_code = status
    r.headers = headers or {}
    r.content = content
    r.text = content.decode() if content else ""
    return r


class SigV4VectorTests(ArcmsTestCase):
    """أمثلة التوقيع المنشورة في توثيق AWS."""

    def sig(self, headers):
        return headers["authorization"].rsplit("Signature=", 1)[1]

    def test_get_vanilla_from_aws_test_suite(self):
        h = offsite.sign_v4("GET", "https://example.amazonaws.com/", region="us-east-1", service="service",
                            access_key="AKIDEXAMPLE", secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
                            now=dt.datetime(2015, 8, 30, 12, 36, tzinfo=dt.timezone.utc), s3=False)
        self.assertEqual(self.sig(h), "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31")
        self.assertIn("SignedHeaders=host;x-amz-date", h["authorization"])

    def test_s3_get_object_example(self):
        h = offsite.sign_v4("GET", "https://examplebucket.s3.amazonaws.com/test.txt", region="us-east-1", service="s3",
                            access_key="AKIAIOSFODNN7EXAMPLE", secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                            headers={"Range": "bytes=0-9"}, now=dt.datetime(2013, 5, 24, tzinfo=dt.timezone.utc))
        self.assertEqual(self.sig(h), "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")

    def test_s3_list_objects_example_query_canonicalization(self):
        h = offsite.sign_v4("GET", "https://examplebucket.s3.amazonaws.com/?max-keys=2&prefix=J", region="us-east-1",
                            service="s3", access_key="AKIAIOSFODNN7EXAMPLE",
                            secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                            now=dt.datetime(2013, 5, 24, tzinfo=dt.timezone.utc))
        self.assertEqual(self.sig(h), "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7")


@override_settings(**S3)
class UploadTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.dir = Path(settings.ARCMS_BACKUP_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "arcms-20260929-030000.arcbak"
        self.file.write_bytes(b"x" * 23)

    def tearDown(self):
        self.file.unlink(missing_ok=True)

    def test_single_put_then_verify_size(self):
        calls = []

        def fake(method, url, data=None, headers=None, timeout=None):
            calls.append((method, url, headers))
            if method == "HEAD":
                return resp(200, {"Content-Length": "23"})
            return resp(200)

        with mock.patch("arcms.backups.offsite.requests.request", side_effect=fake):
            offsite.upload(self.file, offsite.object_key(self.file.name))
        self.assertEqual([c[0] for c in calls], ["PUT", "HEAD"])
        self.assertEqual(calls[0][1], "https://s3.example.org/newsroom-backups/arcms/arcms-20260929-030000.arcbak")
        self.assertIn("eu-central-1/s3/aws4_request", calls[0][2]["authorization"])

    def test_multipart_for_large_files_and_abort_on_failure(self):
        calls = []
        upload_xml = b"<InitiateMultipartUploadResult><UploadId>abc/123</UploadId></InitiateMultipartUploadResult>"

        def fake(method, url, data=None, headers=None, timeout=None):
            calls.append((method, url, data))
            if method == "POST" and "uploads=" in url:
                return resp(200, content=upload_xml)
            if method == "PUT":
                return resp(200, {"ETag": f'"etag{len(calls)}"'})
            if method == "HEAD":
                return resp(200, {"Content-Length": "23"})
            return resp(200)

        with mock.patch.object(offsite, "PART_SIZE", 10), mock.patch.object(offsite, "SINGLE_PUT_LIMIT", 10), \
                mock.patch("arcms.backups.offsite.requests.request", side_effect=fake):
            offsite.upload(self.file, "arcms/f.arcbak")
        parts = [c for c in calls if c[0] == "PUT"]
        self.assertEqual(len(parts), 3)  # 10 + 10 + 3
        self.assertIn("uploadId=abc%2F123", parts[0][1])
        complete = [c for c in calls if c[0] == "POST" and "uploadId=" in c[1]][0]
        self.assertIn(b"<PartNumber>3</PartNumber>", complete[2])

        calls.clear()

        def failing(method, url, data=None, headers=None, timeout=None):
            calls.append((method, url, data))
            if method == "POST" and "uploads=" in url:
                return resp(200, content=upload_xml)
            if method == "PUT":
                return resp(500, content=b"<Error><Code>InternalError</Code></Error>")
            return resp(204)

        with mock.patch.object(offsite, "PART_SIZE", 10), mock.patch.object(offsite, "SINGLE_PUT_LIMIT", 10), \
                mock.patch("arcms.backups.offsite.requests.request", side_effect=failing):
            with self.assertRaises(offsite.OffsiteError):
                offsite.upload(self.file, "arcms/f.arcbak")
        self.assertEqual(calls[-1][0], "DELETE")  # أُلغي الرفع المتعدد فلا تبقى أجزاء يتيمة

    def test_size_mismatch_detected(self):
        def fake(method, url, data=None, headers=None, timeout=None):
            return resp(200, {"Content-Length": "5"}) if method == "HEAD" else resp(200)

        with mock.patch("arcms.backups.offsite.requests.request", side_effect=fake):
            with self.assertRaises(offsite.OffsiteError):
                offsite.upload(self.file, "k")

    def test_job_marks_record_and_never_deletes_by_default(self):
        rec = BackupRecord.objects.create(filename=self.file.name, status=BackupRecord.Status.OK, size=23,
                                          offsite_status="pending")
        from arcms.core.jobs import enqueue

        enqueue("backup.offsite", {"record": rec.pk})
        methods = []

        def fake(method, url, data=None, headers=None, timeout=None):
            methods.append(method)
            return resp(200, {"Content-Length": "23"}) if method == "HEAD" else resp(200)

        with mock.patch("arcms.backups.offsite.requests.request", side_effect=fake):
            run_pending()
        rec.refresh_from_db()
        self.assertEqual(rec.offsite_status, "ok")
        self.assertEqual(rec.offsite_key, "arcms/" + self.file.name)
        self.assertNotIn("DELETE", methods)
        self.assertNotIn("GET", methods)  # لا قائمة ولا حذف مع KEEP=0

    def test_failure_recorded_and_retried(self):
        rec = BackupRecord.objects.create(filename=self.file.name, status=BackupRecord.Status.OK, size=23)
        from arcms.core.jobs import enqueue

        enqueue("backup.offsite", {"record": rec.pk})
        denied = resp(403, content=b"<Error><Code>AccessDenied</Code><Message>denied</Message></Error>")
        with mock.patch("arcms.backups.offsite.requests.request", return_value=denied):
            run_pending()
        rec.refresh_from_db()
        self.assertEqual(rec.offsite_status, "failed")
        self.assertIn("AccessDenied", rec.offsite_error)
        self.assertEqual(Job.objects.get(kind="backup.offsite").status, Job.Status.QUEUED)

    @override_settings(ARCMS_OFFSITE_KEEP=1)
    def test_rotation_when_enabled(self):
        listing = (b"<ListBucketResult><Contents><Key>arcms/arcms-20260901-030000.arcbak</Key></Contents>"
                   b"<Contents><Key>arcms/arcms-20260902-030000.arcbak</Key></Contents></ListBucketResult>")
        calls = []

        def fake(method, url, data=None, headers=None, timeout=None):
            calls.append((method, url))
            return resp(200, content=listing) if method == "GET" else resp(204)

        with mock.patch("arcms.backups.offsite.requests.request", side_effect=fake):
            self.assertEqual(offsite.rotate_remote(), 1)
        self.assertTrue(calls[-1][1].endswith("arcms-20260901-030000.arcbak"))
        self.assertEqual(calls[-1][0], "DELETE")


class ScheduleTests(ArcmsTestCase):
    def test_finished_backup_queues_offsite_only_when_configured(self):
        from arcms.backups.tasks import run_backup

        fake_rec = BackupRecord.objects.create(filename="arcms-x.arcbak", status=BackupRecord.Status.OK)
        with mock.patch("arcms.backups.service.create_backup", return_value=fake_rec):
            run_backup({"trigger": "test"})
            self.assertFalse(Job.objects.filter(kind="backup.offsite").exists())
            with override_settings(**S3):
                run_backup({"trigger": "test"})
        self.assertTrue(Job.objects.filter(kind="backup.offsite").exists())
        fake_rec.refresh_from_db()
        self.assertEqual(fake_rec.offsite_status, "pending")
