from pathlib import Path

from django.core.management.base import BaseCommand

from arcms.importer.loader import Loader
from arcms.importer.sources import read_jsonl, read_wxr


class Command(BaseCommand):
    help = "يستورد أرشيفاً من ووردبريس (wxr) أو JSON Lines (jsonl)، مع تحويل الروابط القديمة."

    def add_arguments(self, parser):
        parser.add_argument("source", choices=["wxr", "jsonl"])
        parser.add_argument("file")
        parser.add_argument("--download-media", action="store_true", help="نزّل الصور وانزع بياناتها المخفية")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--default-category", default="أخبار")
        parser.add_argument("--kind-map", default="", help='مثال: "مقالات=opinion,فيديو=video"')

    def handle(self, *args, **opts):
        path = Path(opts["file"])
        kind_map = dict(p.split("=", 1) for p in opts["kind_map"].split(",") if "=" in p)
        loader = Loader(
            download_media=opts["download_media"],
            dry_run=opts["dry_run"],
            kind_map=kind_map,
            default_category=opts["default_category"],
            source=opts["source"],
            filename=path.name,
        )
        reader = read_wxr if opts["source"] == "wxr" else read_jsonl

        def progress(run):
            self.stdout.write(f"… {run.created} جديدة، {run.updated} محدَّثة، {run.skipped} متجاوَزة")

        run = loader.load(reader(path), limit=opts["limit"], progress=progress)
        self.stdout.write(self.style.SUCCESS(
            f"انتهى: {run.created} جديدة، {run.updated} محدَّثة، {run.skipped} متجاوَزة، {len(run.errors)} خطأ."
        ))
        for e in run.errors[:20]:
            self.stdout.write(self.style.WARNING(f"  {e}"))
