from pathlib import Path

from arcms.core.jobs import handler


@handler("import.run")
def run_import(payload):
    from .loader import Loader
    from .sources import read_jsonl, read_wxr

    path = Path(payload["path"])
    reader = read_wxr if payload["source"] == "wxr" else read_jsonl
    loader = Loader(
        download_media=payload.get("download_media", False),
        dry_run=payload.get("dry_run", False),
        source=payload["source"],
        filename=path.name,
    )
    run = loader.load(reader(path))
    return {"created": run.created, "updated": run.updated, "skipped": run.skipped}
