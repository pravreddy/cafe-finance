"""Seed the few rows the app needs to be usable: cafes, categories, people.

    python manage.py seed_basic
    python manage.py seed_basic --with-users   # also creates logins praveen/srinivas/priyanka (password: change-me)

Idempotent — safe to run on every deploy. Edit names in the admin afterwards.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from ledger.models import Cafe, Category, Person

CAFES = [
    # name, slug, fastpos tenant, WhatsApp groups the reconciler should map here
    ("Cafe Gopala", "cafegopala", "cafegopala", ["CG fin"]),
    ("Krishna Kitchen", "krishna-kitchen", "", ["Krishna Kitchen Finance"]),
    # third business — rename in the admin once decided (scope: three cafes only)
    ("Cafe 3 (rename me)", "cafe-3", "", []),
]

CATEGORIES = [
    # name, slug, kind, sort
    ("Staff salary", "salary", "expense", 10),
    ("Vegetables", "vegetables", "expense", 20),
    ("Groceries / provisions", "groceries", "expense", 21),
    ("Dairy", "dairy", "expense", 22),
    ("Gas cylinder", "gas", "expense", 23),
    ("Packaging", "packaging", "expense", 24),
    ("Rent", "rent", "expense", 30),
    ("Electricity / utilities", "utilities", "expense", 31),
    ("Painting / repairs", "repairs", "expense", 40),
    ("Equipment", "equipment", "expense", 41),
    ("Transport", "transport", "expense", 50),
    ("Marketing", "marketing", "expense", 51),
    ("Licences / fees", "fees", "expense", 52),
    ("Misc", "misc", "expense", 90),
    ("Partner money in", "partner-in", "transfer", 100),
    ("Partner money out", "partner-out", "transfer", 101),
    ("Cash sales (no POS)", "cash-sales", "income", 110),
    ("Other income", "other-income", "income", 111),
]

PEOPLE = [
    # name, role, can_confirm, default cafe slug
    ("Praveen", "partner", True, "cafegopala"),
    ("Srinivas", "partner", True, "cafegopala"),
    ("Priyanka", "partner", False, "cafegopala"),
    ("Dhiren", "staff", False, "cafegopala"),
]


class Command(BaseCommand):
    help = "Seed cafes, categories and people (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--with-users", action="store_true")

    def handle(self, *args, **opts):
        cafes = {}
        for name, slug, tenant, groups in CAFES:
            c, _ = Cafe.objects.update_or_create(slug=slug, defaults={
                "name": name, "fastpos_tenant": tenant, "whatsapp_groups": groups})
            cafes[slug] = c
        for name, slug, kind, sort in CATEGORIES:
            Category.objects.update_or_create(slug=slug, defaults={"name": name, "kind": kind, "sort": sort})
        User = get_user_model()
        for name, role, confirm, cafe_slug in PEOPLE:
            p, _ = Person.objects.get_or_create(name=name, defaults={
                "role": role, "can_confirm": confirm, "default_cafe": cafes.get(cafe_slug)})
            if opts["with_users"] and p.user_id is None:
                u, created = User.objects.get_or_create(username=name.lower(), defaults={"first_name": name})
                if created:
                    u.set_password("change-me")
                    u.save()
                p.user = u
                p.save(update_fields=["user"])
        self.stdout.write(self.style.SUCCESS(
            f"seeded {Cafe.objects.count()} cafes, {Category.objects.count()} categories, {Person.objects.count()} people"))
