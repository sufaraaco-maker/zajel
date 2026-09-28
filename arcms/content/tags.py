"""أدوات الوسوم: اقتراح الوسوم المتشابهة لغوياً ودمجها.

الأرشيف المستورد ودفق الأخبار السريع يولّدان وسوماً متقاربة («الاعتقال»،
«اعتقالات»، «الاعتقالات») تشتت القارئ ومحركات البحث. نجمعها بجذعها الخفيف
ونقترح دمجها، والدمج يحفظ رابط الوسم القديم بتحويل دائم.
"""

from __future__ import annotations

from collections import defaultdict

from django.db import transaction
from django.db.models import Count

from arcms.arabic.normalize import hamza_skeleton
from arcms.arabic.stemmer import light_stem
from arcms.audit.models import Action
from arcms.audit.services import record

from .models import Tag


def tag_key(tag: Tag) -> str:
    """مفتاح التشابه: جذع كل كلمة من الهيكل بلا همزات."""
    words = hamza_skeleton(tag.name.replace("_", " ")).split()
    return " ".join(light_stem(w) for w in words)


def similar_groups(limit: int = 30) -> list[list[Tag]]:
    groups: dict[str, list[Tag]] = defaultdict(list)
    for tag in Tag.objects.annotate(n=Count("articles")).order_by("-n", "name"):
        groups[tag_key(tag)].append(tag)
    found = [g for g in groups.values() if len(g) > 1]
    found.sort(key=lambda g: -sum(t.n for t in g))
    return found[:limit]


@transaction.atomic
def merge_tags(source: Tag, target: Tag) -> int:
    """ينقل مواد الوسم source إلى target ثم يحذفه، ويحوّل رابطه القديم."""
    if source.pk == target.pk:
        raise ValueError("لا يمكن دمج الوسم في نفسه.")
    from arcms.importer.models import LegacyRedirect

    moved = 0
    for article in source.articles.all():
        article.tags.add(target)
        moved += 1
    old_url = source.get_absolute_url()
    name = source.name
    source.delete()
    from urllib.parse import unquote

    LegacyRedirect.objects.update_or_create(old_path=unquote(old_url).rstrip("/"), defaults={"new_path": target.get_absolute_url()})
    record(Action.UPDATE, target, message=f"دمج الوسم «{name}» في «{target.name}» ({moved} مادة)")
    return moved
