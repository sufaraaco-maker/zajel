from django.db.models import Count
from django.urls import path

from arcms.accounts.roles import Cap
from arcms.content.models import Author, Category, Dossier, Page, Tag
from arcms.core.models import AdSlot, MenuItem
from arcms.wires.models import WireSource

from . import views_admin as admin
from . import views_articles as articles
from . import views_media as media
from . import views_newsroom as newsroom
from . import views_planning as planning
from . import views_polls as polls
from . import views_setup as setup_views
from . import views_tips as tips_views
from . import views_tools as tools
from . import views_wires as wires
from .base import Crud
from .forms import AdSlotForm, AuthorForm, CategoryForm, DossierForm, MenuItemForm, PageForm, TagForm, WireSourceForm

app_name = "studio"

categories = Crud(
    model=Category, form=CategoryForm, cap=Cap.TAXONOMY, name="categories", title="الأقسام", singular="القسم",
    columns=[("name", "الاسم"), ("parent", "تحت"), ("n", "المواد"), ("order", "الترتيب"), ("is_active", "مفعّل")],
    queryset=lambda: Category.objects.select_related("parent").annotate(n=Count("articles")).order_by("order", "name"),
    search_field="name",
    help_text="الأقسام تبني قائمة الموقع وروابطه. طاقم القسم يحدد من يراجع وينشر فيه.",
)
tags = Crud(
    model=Tag, form=TagForm, cap=Cap.TAXONOMY, name="tags", title="الوسوم", singular="الوسم",
    columns=[("name", "الوسم"), ("n", "المواد"), ("slug", "الرابط")],
    queryset=lambda: Tag.objects.annotate(n=Count("articles")).order_by("-n", "name"),
    search_field="name",
    template_list="studio/tags_list.html",
    template_form="studio/tag_form.html",
    list_extra=tools.tags_list_extra,
    form_extra=tools.tag_form_extra,
)
authors = Crud(
    model=Author, form=AuthorForm, cap=Cap.TAXONOMY, name="authors", title="الكتّاب", singular="الكاتب",
    columns=[("name", "الاسم"), ("title", "الصفة"), ("is_columnist", "كاتب رأي"), ("user", "حساب الطاقم")],
    queryset=lambda: Author.objects.select_related("user"),
    search_field="name",
    help_text="الكاتب هو الاسم في سطر التوقيع؛ قد يكون من الطاقم أو كاتب رأي من خارجه بلا حساب.",
)
dossiers = Crud(
    model=Dossier, form=DossierForm, cap=Cap.TAXONOMY, name="dossiers", title="الملفات الخاصة", singular="الملف",
    columns=[("title", "العنوان"), ("is_active", "مفعّل"), ("order", "الترتيب")],
    search_field="title",
)
pages = Crud(
    model=Page, form=PageForm, cap=Cap.PAGES, name="pages", title="الصفحات الثابتة", singular="الصفحة",
    columns=[("title", "العنوان"), ("slug", "الرابط"), ("is_published", "منشورة")],
    template_form="studio/page_form.html",
    search_field="title",
)
menus = Crud(
    model=MenuItem, form=MenuItemForm, cap=Cap.HOMEPAGE, name="menus", title="القوائم", singular="العنصر",
    columns=[("get_location_display", "الموقع"), ("label", "النص"), ("parent", "تحت"), ("get_link_type_display", "يشير إلى"),
             ("order", "الترتيب"), ("is_active", "مفعّل")],
    queryset=lambda: MenuItem.objects.select_related("parent").order_by("location", "parent_id", "order"),
    help_text="القائمة الرئيسية تظهر تحت الشعار، والعلوية فوقه، وقائمة التذييل أسفل كل صفحة.",
)
ads = Crud(
    model=AdSlot, form=AdSlotForm, cap=Cap.HOMEPAGE, name="ads", title="المساحات الإعلانية", singular="الإعلان",
    columns=[("name", "الاسم"), ("get_placement_display", "المكان"), ("is_active", "مفعّل"), ("ends_at", "ينتهي")],
    help_text="الإعلان بصورة ورابط لا يحمّل أي سكربت خارجي. شيفرات شبكات الإعلان تخفف حماية الخصوصية للقرّاء، "
              "ولا يضيفها إلا مدير النظام.",
    queryset=lambda: AdSlot.objects.order_by("placement", "name"),
    form_takes_user=True,
)

wiresources = Crud(
    model=WireSource, form=WireSourceForm, cap=Cap.WIRES_MANAGE, name="wiresources", title="مصادر الوكالات",
    singular="المصدر",
    columns=[("name", "المصدر"), ("category", "القسم"), ("poll_minutes", "كل (دقيقة)"), ("status_label", "الحالة"),
             ("last_ok_at", "آخر جلب ناجح")],
    queryset=lambda: WireSource.objects.select_related("category"),
    template_list="studio/wire_sources.html",
    list_extra=wires.sources_extra,
    public_url=False,
    help_text="خلاصات RSS أو Atom من الوكالات. تُجلب تلقائياً من العامل الخلفي وتصل إلى «مكتب الوكالات».",
)

urlpatterns = [
    path("", articles.home, name="home"),
    path("articles/", articles.article_list, name="articles"),
    path("articles/new/", articles.article_edit, name="article_new"),
    path("articles/<int:pk>/", articles.article_edit, name="article_edit"),
    path("articles/<int:pk>/heartbeat/", articles.article_heartbeat, name="article_heartbeat"),
    path("articles/<int:pk>/autosave/", articles.article_autosave, name="article_autosave"),
    path("articles/<int:pk>/note/", articles.article_note, name="article_note"),
    path("articles/<int:pk>/delete/", articles.article_delete, name="article_delete"),
    path("articles/<int:pk>/distribute/", articles.article_distribute, name="article_distribute"),
    path("articles/<int:pk>/revisions/<int:rev>/", articles.article_revision, name="article_revision"),
    path("api/tags", articles.api_tags, name="api_tags"),
    path("api/articles", articles.api_articles, name="api_articles"),
    path("media/", media.library, name="media"),
    path("media/upload/", media.upload, name="media_upload"),
    path("media/picker/", media.picker, name="media_picker"),
    path("media/<int:pk>/", media.edit, name="media_edit"),
    path("media/<int:pk>/delete/", media.delete, name="media_delete"),
    path("breaking/", newsroom.breaking, name="breaking"),
    path("breaking/<int:pk>/toggle/", newsroom.breaking_toggle, name="breaking_toggle"),
    path("breaking/<int:pk>/card/", newsroom.breaking_card, name="breaking_card"),
    path("articles/<int:pk>/card/", newsroom.article_card, name="article_card"),
    path("live/", newsroom.live_list, name="live_list"),
    path("live/<int:pk>/", newsroom.live_detail, name="live_detail"),
    path("live/<int:pk>/settings/", newsroom.live_update, name="live_update"),
    path("live/<int:pk>/entry/<int:entry>/", newsroom.live_entry_action, name="live_entry_action"),
    path("homepage/", admin.homepage, name="homepage"),
    path("homepage/new/", admin.homepage_block, name="homepage_new"),
    path("homepage/<int:pk>/", admin.homepage_block, name="homepage_edit"),
    path("homepage/<int:pk>/action/", admin.homepage_action, name="homepage_action"),
    path("settings/", admin.site_settings, name="settings"),
    path("setup/", setup_views.setup, name="setup"),
    path("users/", admin.users, name="users"),
    path("users/new/", admin.user_edit, name="user_new"),
    path("users/<int:pk>/", admin.user_edit, name="user_edit"),
    path("users/<int:pk>/reset-2fa/", admin.user_reset_2fa, name="user_reset_2fa"),
    path("users/<int:pk>/end-sessions/", admin.user_sessions_end, name="user_sessions_end"),
    path("me/", admin.profile, name="profile"),
    path("distribution/", admin.distribution, name="distribution"),
    path("distribution/<str:channel>/", admin.channel_edit, name="channel_edit"),
    path("distribution/<str:channel>/test/", admin.channel_test, name="channel_test"),
    path("newsletter/send/", admin.newsletter_send_now, name="newsletter_send"),
    path("newsletter/preview/", admin.newsletter_preview, name="newsletter_preview"),
    path("whatsapp/", admin.whatsapp_subscribers, name="whatsapp_subscribers"),
    path("analytics/", admin.analytics, name="analytics"),
    path("audit/", admin.audit, name="audit"),
    path("backups/", admin.backups, name="backups"),
    path("backups/run/", admin.backup_now, name="backup_now"),
    path("backups/<int:pk>/download/", admin.backup_download, name="backup_download"),
    path("import/", admin.importer, name="importer"),
    path("inbox/", admin.inbox, name="inbox"),
    path("health/", admin.health, name="health"),
    path("tags/<int:pk>/merge/", tools.tag_merge, name="tag_merge"),
    path("tags/merge-group/", tools.tag_merge_group, name="tag_merge_group"),
    path("subscribers/", tools.subscribers, name="subscribers"),
    path("subscribers/export.csv", tools.subscribers_export, name="subscribers_export"),
    path("subscribers/<int:pk>/delete/", tools.subscriber_delete, name="subscriber_delete"),
    path("calendar/", tools.calendar, name="calendar"),
    path("tips/", tips_views.tips_list, name="tips"),
    path("tips/<int:pk>/", tips_views.tip_detail, name="tip_detail"),
    path("tips/<int:pk>/delete/", tips_views.tip_delete, name="tip_delete"),
    path("tips/<int:pk>/files/<int:att>/", tips_views.tip_attachment, name="tip_attachment"),
    path("planning/", planning.board, name="planning"),
    path("planning/new/", planning.edit, name="assignment_new"),
    path("planning/<int:pk>/", planning.edit, name="assignment_edit"),
    path("planning/<int:pk>/start/", planning.start, name="assignment_start"),
    path("planning/<int:pk>/action/", planning.action, name="assignment_action"),
    path("polls/", polls.poll_list, name="polls"),
    path("polls/new/", polls.poll_edit, name="poll_new"),
    path("polls/<int:pk>/", polls.poll_edit, name="poll_edit"),
    path("polls/<int:pk>/delete/", polls.poll_delete, name="poll_delete"),
    path("wires/", wires.desk, name="wires"),
    path("wires/action/", wires.action, name="wires_action"),
    path("wiresources/keywords/", wires.keywords, name="wire_keywords"),
    path("wiresources/<int:pk>/poll/", wires.poll_now, name="wire_poll"),
    *wiresources.urls(),
    *categories.urls(),
    *tags.urls(),
    *authors.urls(),
    *dossiers.urls(),
    *pages.urls(),
    *menus.urls(),
    *ads.urls(),
]
