"""Income side: pull daily POS sales from FastPOS into the ledger.

    python manage.py pull_fastpos                 # yesterday + today, every cafe with a token
    python manage.py pull_fastpos --days 30       # backfill a month
    python manage.py pull_fastpos --cafe cafegopala --from 2026-09-01 --to 2026-09-30

Calls FastPOS `/api/finance/summary/?from=D&to=D` per day with the cafe's owner
token and writes ONE confirmed income row per cafe-day (income_source=pos,
source=manual is NOT used — these are `source=app`, payee "FastPOS"). Re-running
updates the amount if the POS changed (refund, late payment); POS rows are
never hand-editable. Only counts `pos` collection — invoices and 'other income'
in FastPOS are booked here separately so nothing double-counts.
"""
from datetime import date, timedelta
from decimal import Decimal

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from ledger.models import Cafe, Category, LedgerEntry, Period


class Command(BaseCommand):
    help = "Pull daily POS sales from FastPOS as confirmed income rows."

    def add_arguments(self, p):
        p.add_argument("--cafe")
        p.add_argument("--days", type=int, default=2)
        p.add_argument("--from", dest="start")
        p.add_argument("--to", dest="end")
        p.add_argument("--dry-run", action="store_true")

    def handle(self, *a, **o):
        cafes = Cafe.objects.filter(is_active=True).exclude(fastpos_token="")
        if o["cafe"]:
            cafes = cafes.filter(slug=o["cafe"])
        if not cafes:
            self.stdout.write("no cafe has a fastpos_token — set it in the admin"); return
        today = timezone.localdate()
        start = date.fromisoformat(o["start"]) if o["start"] else today - timedelta(days=o["days"] - 1)
        end = date.fromisoformat(o["end"]) if o["end"] else today
        cat = Category.objects.filter(slug="pos-sales").first() or Category.objects.filter(kind="income").first()
        n = 0
        for cafe in cafes:
            d = start
            while d <= end:
                if Period.is_month_closed(cafe, d):
                    self.stdout.write(f"  {cafe.slug} {d}: month closed, skipping"); d += timedelta(days=1); continue
                try:
                    r = requests.get(f"{settings.FASTPOS_BASE_URL.rstrip('/')}/api/finance/summary/",
                                     params={"from": d.isoformat(), "to": d.isoformat()},
                                     headers={"Authorization": f"Token {cafe.fastpos_token}"}, timeout=60)
                    r.raise_for_status()
                    amount = Decimal(str(r.json().get("collection") or r.json().get("pos") or 0))
                except (requests.RequestException, ValueError) as e:
                    self.stderr.write(f"  {cafe.slug} {d}: {e}"); d += timedelta(days=1); continue
                if o["dry_run"]:
                    self.stdout.write(f"  {cafe.slug} {d}: ₹{amount}")
                else:
                    row, created = LedgerEntry.objects.update_or_create(
                        cafe=cafe, paid_on=d, kind=LedgerEntry.Kind.INCOME,
                        income_source=LedgerEntry.IncomeSource.POS,
                        defaults={"amount": amount, "category": cat, "payee_name": "FastPOS",
                                  "description": f"POS sales {d:%d %b}", "source": LedgerEntry.Source.APP,
                                  "status": LedgerEntry.Status.CONFIRMED, "confirmed_at": timezone.now(),
                                  "upi_app": LedgerEntry.UpiApp.OTHER, "confidence": 1.0},
                    )
                    n += 1
                    self.stdout.write(f"  {cafe.slug} {d}: ₹{amount} {'new' if created else 'updated'} {row.cf_tag}")
                d += timedelta(days=1)
        self.stdout.write(self.style.SUCCESS(f"{n} day-rows written"))
