import getpass

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from arcms.accounts.models import User
from arcms.accounts.roles import Role
from arcms.audit.services import acting_as
from arcms.content.models import Category, Page
from arcms.core.models import HomeBlock, MenuItem, SiteSettings
from arcms.core.presets import PAGES, PRESETS, THEMES, apply_theme, create_blocks
from arcms.distribution.models import Channel, ChannelConfig


class Command(BaseCommand):
    help = "التهيئة الأولى لموقع جديد: الهوية، الأقسام، القوائم، الصفحة الرئيسية، الصفحات، وحساب المدير."

    def add_arguments(self, parser):
        parser.add_argument("--preset", choices=list(PRESETS), default="palestine")
        parser.add_argument("--site-name")
        parser.add_argument("--short-name")
        parser.add_argument("--admin-username")
        parser.add_argument("--admin-email", default="")
        parser.add_argument("--admin-password", help="للأتمتة فقط؛ يُفضَّل تركه ليُسأل عنه")
        parser.add_argument("--force", action="store_true", help="أعد إنشاء القوائم والكتل حتى لو وُجدت")
        parser.add_argument("--theme", help="مظهر جاهز: " + "، ".join(THEMES))

    @transaction.atomic
    def handle(self, *args, **opts):
        preset = PRESETS[opts["preset"]]
        with acting_as(label="التهيئة الأولى"):
            site = SiteSettings.load()
            site = SiteSettings.objects.get(pk=site.pk)
            if opts["site_name"]:
                site.name = opts["site_name"]
                site.short_name = opts["short_name"] or opts["site_name"].split()[0]
            site.save()
            if opts.get("theme"):
                apply_theme(site, opts["theme"])

            cats = {}
            for order, (name, color) in enumerate(preset["categories"]):
                cat, _ = Category.objects.get_or_create(name=name, defaults={"color": color, "order": order * 10})
                cats[name] = cat

            pages = {}
            for order, (title, slug, contact, body) in enumerate(PAGES):
                page, _ = Page.objects.get_or_create(
                    slug=slug, defaults={"title": title, "body": body, "show_contact_form": contact, "order": order}
                )
                pages[slug] = page

            if opts["force"] or not MenuItem.objects.exists():
                MenuItem.objects.all().delete()
                for order, (ltype, label, target) in enumerate(preset["main_menu"]):
                    item = MenuItem(location=MenuItem.Location.MAIN, label=label, order=order * 10)
                    if ltype == "category":
                        item.link_type, item.category = MenuItem.LinkType.CATEGORY, cats[target]
                    elif ltype == "kind":
                        item.link_type, item.kind = MenuItem.LinkType.KIND, target
                        item.highlight = target == "video"
                    elif ltype == "live":
                        item.link_type = MenuItem.LinkType.LIVE
                    else:
                        item.link_type, item.url = MenuItem.LinkType.URL, "/latest/"
                    item.save()
                for order, slug in enumerate(("about", "contact", "advertise")):
                    MenuItem.objects.create(
                        location=MenuItem.Location.TOP, label=pages[slug].title, link_type=MenuItem.LinkType.PAGE,
                        page=pages[slug], order=order,
                    )
                MenuItem.objects.create(
                    location=MenuItem.Location.TOP, label="أرسل معلومة بأمان", link_type=MenuItem.LinkType.URL,
                    url="/tips/", order=10,
                )
                for order, slug in enumerate(("about", "privacy", "terms", "contact")):
                    MenuItem.objects.create(
                        location=MenuItem.Location.FOOTER, label=pages[slug].title, link_type=MenuItem.LinkType.PAGE,
                        page=pages[slug], order=order,
                    )
                MenuItem.objects.create(
                    location=MenuItem.Location.FOOTER, label="سجل التصحيحات", link_type=MenuItem.LinkType.URL,
                    url="/corrections/", order=9,
                )

            if opts["force"] or not HomeBlock.objects.exists():
                HomeBlock.objects.all().delete()
                create_blocks(preset["blocks"], cats)

            for channel in Channel.values:
                ChannelConfig.get(channel)

            if opts["admin_username"]:
                self._admin(opts)

        self.stdout.write(self.style.SUCCESS(f"اكتملت التهيئة ({preset['label']})."))
        self.stdout.write("الخطوة التالية: شغّل الخادم وادخل إلى /studio/ لتفعيل التحقق الثنائي وضبط الهوية.")

    def _admin(self, opts):
        username = opts["admin_username"]
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"المستخدم {username} موجود؛ لم يُعدَّل.")
            return
        password = opts["admin_password"]
        if not password:
            password = getpass.getpass("كلمة مرور المدير: ")
            if password != getpass.getpass("أعدها: "):
                raise CommandError("كلمتا المرور غير متطابقتين.")
        from django.contrib.auth.password_validation import validate_password

        validate_password(password)
        User.objects.create_user(
            username=username,
            email=opts["admin_email"],
            password=password,
            role=Role.ADMIN,
            display_name="مدير النظام",
            is_staff=True,
        )
        self.stdout.write(self.style.SUCCESS(f"أُنشئ حساب المدير «{username}». سيُطلب تفعيل التحقق الثنائي عند أول دخول."))
