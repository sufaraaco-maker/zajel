"""رتب غرفة التحرير وصلاحياتها.

الصلاحيات معرّفة في الكود لا في قاعدة البيانات: هكذا تبقى مصفوفة
الصلاحيات قابلة للمراجعة في كل إصدار، ولا يستطيع أحد توسيعها بصمت
من واجهة الإدارة. تُعرض المصفوفة كاملة لمدير النظام في صفحة المستخدمين.
"""

from __future__ import annotations

from django.db import models


class Role(models.TextChoices):
    CONTRIBUTOR = "contributor", "كاتب متعاون"
    REPORTER = "reporter", "مراسل"
    EDITOR = "editor", "محرر"
    SOCIAL = "social", "محرر منصات"
    DESK_HEAD = "desk_head", "رئيس قسم"
    CHIEF = "chief", "رئيس التحرير"
    ADMIN = "admin", "مدير النظام"


class Cap:
    """أسماء الصلاحيات."""

    ARTICLE_CREATE = "article.create"
    ARTICLE_EDIT_OWN = "article.edit_own"
    ARTICLE_EDIT_ANY = "article.edit_any"
    ARTICLE_SUBMIT = "article.submit"
    ARTICLE_REVIEW = "article.review"
    ARTICLE_PUBLISH = "article.publish"
    ARTICLE_UNPUBLISH = "article.unpublish"
    ARTICLE_DELETE = "article.delete"
    SOURCES_VIEW = "article.sources_view"
    BREAKING = "breaking.manage"
    LIVE_POST = "live.post"
    LIVE_MANAGE = "live.manage"
    MEDIA_UPLOAD = "media.upload"
    MEDIA_MANAGE = "media.manage"
    TAGS = "taxonomy.tags"
    TAXONOMY = "taxonomy.manage"
    HOMEPAGE = "homepage.manage"
    PAGES = "pages.manage"
    DIST_SEND = "distribution.send"
    DIST_MANAGE = "distribution.manage"
    ANALYTICS = "analytics.view"
    USERS = "users.manage"
    SETTINGS = "settings.manage"
    AUDIT = "audit.view"
    BACKUPS = "backups.manage"
    IMPORT = "import.run"
    TIPS = "tips.manage"
    WIRES = "wires.view"
    WIRES_MANAGE = "wires.manage"


CAPABILITY_LABELS = {
    Cap.ARTICLE_CREATE: "إنشاء مواد",
    Cap.ARTICLE_EDIT_OWN: "تحرير موادّه",
    Cap.ARTICLE_EDIT_ANY: "تحرير مواد الآخرين",
    Cap.ARTICLE_SUBMIT: "إرسال للمراجعة",
    Cap.ARTICLE_REVIEW: "المراجعة والإعادة والاعتماد",
    Cap.ARTICLE_PUBLISH: "النشر والجدولة",
    Cap.ARTICLE_UNPUBLISH: "سحب المنشور",
    Cap.ARTICLE_DELETE: "حذف المواد",
    Cap.SOURCES_VIEW: "الاطلاع على ملاحظات مصادر الآخرين",
    Cap.BREAKING: "الأخبار العاجلة",
    Cap.LIVE_POST: "الكتابة في التغطيات المباشرة",
    Cap.LIVE_MANAGE: "إدارة التغطيات المباشرة",
    Cap.MEDIA_UPLOAD: "رفع الصور",
    Cap.MEDIA_MANAGE: "إدارة مكتبة الوسائط",
    Cap.TAGS: "إنشاء الوسوم",
    Cap.TAXONOMY: "الأقسام والملفات والوسوم",
    Cap.HOMEPAGE: "الصفحة الرئيسية والقوائم",
    Cap.PAGES: "الصفحات الثابتة",
    Cap.DIST_SEND: "إعادة الإرسال للمنصات",
    Cap.DIST_MANAGE: "إعدادات التوزيع والمشتركين",
    Cap.ANALYTICS: "أرقام الجمهور",
    Cap.USERS: "إدارة المستخدمين",
    Cap.SETTINGS: "إعدادات الموقع",
    Cap.AUDIT: "سجل التدقيق",
    Cap.BACKUPS: "النسخ الاحتياطي",
    Cap.IMPORT: "استيراد الأرشيف",
    Cap.TIPS: "صندوق المعلومات الآمن",
    Cap.WIRES: "مكتب الوكالات (القراءة والاعتماد كمسودة)",
    Cap.WIRES_MANAGE: "مصادر الوكالات وكلمات التنبيه",
}

_WRITER = {Cap.ARTICLE_CREATE, Cap.ARTICLE_EDIT_OWN, Cap.ARTICLE_SUBMIT, Cap.MEDIA_UPLOAD}
_REPORTER = _WRITER | {Cap.LIVE_POST, Cap.TAGS, Cap.WIRES}
_EDITOR = _REPORTER | {Cap.ARTICLE_EDIT_ANY, Cap.ARTICLE_REVIEW, Cap.ANALYTICS}
_DESK_HEAD = _EDITOR | {
    Cap.ARTICLE_PUBLISH,
    Cap.ARTICLE_UNPUBLISH,
    Cap.BREAKING,
    Cap.LIVE_MANAGE,
    Cap.DIST_SEND,
}
_CHIEF = _DESK_HEAD | {
    Cap.ARTICLE_DELETE,
    Cap.SOURCES_VIEW,
    Cap.MEDIA_MANAGE,
    Cap.TAXONOMY,
    Cap.HOMEPAGE,
    Cap.PAGES,
    Cap.DIST_MANAGE,
    Cap.TIPS,
    Cap.WIRES_MANAGE,
}
_SOCIAL = {Cap.ANALYTICS, Cap.DIST_SEND, Cap.DIST_MANAGE, Cap.MEDIA_UPLOAD, Cap.BREAKING}
_ADMIN = set(CAPABILITY_LABELS)

ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    Role.CONTRIBUTOR: frozenset(_WRITER),
    Role.REPORTER: frozenset(_REPORTER),
    Role.EDITOR: frozenset(_EDITOR),
    Role.SOCIAL: frozenset(_SOCIAL),
    Role.DESK_HEAD: frozenset(_DESK_HEAD),
    Role.CHIEF: frozenset(_CHIEF),
    Role.ADMIN: frozenset(_ADMIN),
}

# الرتب التي يُفرض عليها التحقق الثنائي دائماً، بصرف النظر عن إعدادات الموقع.
ROLES_REQUIRING_2FA = frozenset({Role.EDITOR, Role.SOCIAL, Role.DESK_HEAD, Role.CHIEF, Role.ADMIN})

# رتب يقتصر عملها على أقسامها إن حُدّدت لها أقسام.
DESK_SCOPED_ROLES = frozenset({Role.REPORTER, Role.EDITOR, Role.DESK_HEAD})

ROLE_RANK = {
    Role.CONTRIBUTOR: 10,
    Role.REPORTER: 20,
    Role.SOCIAL: 25,
    Role.EDITOR: 30,
    Role.DESK_HEAD: 40,
    Role.CHIEF: 50,
    Role.ADMIN: 60,
}


def capabilities_for(role: str) -> frozenset[str]:
    return ROLE_CAPABILITIES.get(role, frozenset())
