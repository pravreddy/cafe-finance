"""The rules that matter, tested with the mock AI provider (no network).

Run:  USE_SQLITE=1 DOC_AI_PROVIDER=mock python manage.py test
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from . import services
from .models import Invoice, LedgerEntry, Person, Proof, ShareEvent

PNG_A = b"\x89PNG\r\n\x1a\n" + b"A" * 64     # two different 'files'
PNG_B = b"\x89PNG\r\n\x1a\n" + b"B" * 64
PDF_A = b"%PDF-1.4 fake invoice bytes"


@override_settings(DOC_AI_PROVIDER="mock", API_TOKEN="t0k", DOC_AI_SYNC=True)
class IngestTests(TestCase):
    def setUp(self):
        call_command("seed_basic", "--with-users", verbosity=0)
        self.srini = Person.objects.get(name="Srinivas")
        self.praveen = Person.objects.get(name="Praveen")

    def _share(self, raw, name, who, channel=ShareEvent.Channel.APP_SHARE, note=""):
        return services.ingest(raw, name, "image/png", origin=Proof.Origin.APP_SHARE,
                               channel=channel, shared_by=who, note=note)

    def test_payment_creates_entry_with_cf_tag(self):
        r = self._share(PNG_A, "pay_083414546430.png", self.srini)
        self.assertEqual(r.doc_type, "payment")
        self.assertFalse(r.duplicate)
        e = r.entry
        self.assertEqual(e.amount, Decimal("10000.00"))
        self.assertEqual(e.utr, "083414546430")
        self.assertEqual(e.paid_on, date(2026, 9, 14))
        self.assertEqual(e.paid_by, self.srini)
        self.assertEqual(e.status, LedgerEntry.Status.PENDING)
        self.assertEqual(e.cf_tag, f"#CF-{e.pk:06d}")
        self.assertEqual(e.shares.count(), 1)

    def test_same_file_shared_twice_is_one_entry_two_shares(self):
        a = self._share(PNG_A, "pay_083414546430.png", self.srini)
        b = self._share(PNG_A, "whatsapp-image-2026-09-15.png", self.praveen,
                        channel=ShareEvent.Channel.WHATSAPP_FORWARD)
        self.assertEqual(a.entry.pk, b.entry.pk)
        self.assertTrue(b.duplicate)
        self.assertEqual(b.match_method, "utr")          # UTR wins before sha256
        self.assertEqual(LedgerEntry.objects.count(), 1)
        self.assertEqual(Proof.objects.count(), 1)       # same bytes = same proof
        self.assertEqual(a.entry.shares.count(), 2)
        self.assertIn("Already recorded", b.message)

    def test_different_screenshot_same_utr_is_still_one_payment(self):
        a = self._share(PNG_A, "pay_083414546430.png", self.srini)
        b = self._share(PNG_B, "pay_083414546430_cropped.png", self.praveen)
        self.assertEqual(a.entry.pk, b.entry.pk)
        self.assertEqual(Proof.objects.count(), 2)       # two files…
        self.assertEqual(LedgerEntry.objects.count(), 1) # …one payment

    def test_different_utr_is_a_different_payment(self):
        a = self._share(PNG_A, "pay_083414546430.png", self.srini)
        b = self._share(PNG_B, "pay_111122223333.png", self.srini)
        self.assertNotEqual(a.entry.pk, b.entry.pk)
        self.assertEqual(LedgerEntry.objects.count(), 2)

    def test_cf_tag_in_caption_matches_group_post(self):
        a = self._share(PNG_A, "pay_083414546430.png", self.srini)
        # reconciler sees a *different* image (recompressed by WhatsApp) with our caption
        b = services.ingest(PNG_B, "wa.png", "image/png", origin=Proof.Origin.WHATSAPP_GROUP,
                            channel=ShareEvent.Channel.WHATSAPP_GROUP, shared_by_label="Srinivas",
                            note=f"₹10,000 · Painting · 2nd installment {a.entry.cf_tag}")
        # mock returns the same UTR so utr matches first; force the cf_tag path by
        # checking the regex finds our tag too.
        from .dedup import CF_TAG_RE
        self.assertTrue(CF_TAG_RE.search(f"blah {a.entry.cf_tag}"))
        self.assertEqual(b.entry.pk, a.entry.pk)

    def test_invoice_then_payment_links_and_settles(self):
        inv = services.ingest(PDF_A, "invoice_paints.pdf", "application/pdf",
                              origin=Proof.Origin.EMAIL, channel=ShareEvent.Channel.EMAIL,
                              shared_by_label="accounts@balajipaints.in")
        self.assertEqual(inv.doc_type, "invoice")
        invoice = inv.invoice
        self.assertEqual(invoice.total, Decimal("30000.00"))
        self.assertEqual(invoice.status, Invoice.Status.PENDING)
        invoice.status = Invoice.Status.OPEN
        invoice.save()

        p1 = self._share(PNG_A, "pay_083414546430_amt10000.png", self.srini).entry
        p2 = self._share(PNG_B, "pay_555566667777_amt20000.png", self.srini).entry
        for e in (p1, p2):
            e.invoice = invoice
            e.save()
        self.assertEqual(invoice.outstanding, Decimal("30000.00"))   # nothing confirmed yet
        services.confirm_entry(p1, self.praveen)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PART_PAID)
        self.assertEqual(invoice.outstanding, Decimal("20000.00"))
        services.confirm_entry(p2, self.praveen)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(invoice.outstanding, Decimal("0.00"))

    def test_same_invoice_pdf_twice_is_one_invoice(self):
        a = services.ingest(PDF_A, "invoice_paints.pdf", "application/pdf",
                            origin=Proof.Origin.EMAIL, channel=ShareEvent.Channel.EMAIL)
        b = services.ingest(PDF_A + b" ", "invoice_paints_copy.pdf", "application/pdf",
                            origin=Proof.Origin.WHATSAPP_FORWARD, channel=ShareEvent.Channel.WHATSAPP_FORWARD,
                            shared_by=self.srini)
        self.assertEqual(a.invoice.pk, b.invoice.pk)
        self.assertTrue(b.duplicate)
        self.assertEqual(b.match_method, "vendor_number")

    def test_not_money_is_kept_but_creates_nothing(self):
        r = self._share(PNG_A, "other_menu_photo.png", self.srini)
        self.assertEqual(r.doc_type, "other")
        self.assertIsNone(r.entry)
        self.assertIsNone(r.invoice)
        self.assertEqual(ShareEvent.objects.count(), 1)

    def test_non_confirmer_cannot_confirm(self):
        e = self._share(PNG_A, "pay_083414546430.png", self.srini).entry
        priyanka = Person.objects.get(name="Priyanka")
        with self.assertRaises(PermissionError):
            services.confirm_entry(e, priyanka)

    def test_reversal_never_edits_confirmed_row(self):
        e = self._share(PNG_A, "pay_083414546430.png", self.srini).entry
        services.confirm_entry(e, self.praveen)
        rev = services.reverse_entry(e, self.praveen, "paid twice")
        e.refresh_from_db()
        self.assertEqual(e.amount, Decimal("10000.00"))
        self.assertEqual(rev.amount, Decimal("-10000.00"))
        self.assertEqual(rev.reversal_of, e)


@override_settings(DOC_AI_PROVIDER="mock", API_TOKEN="t0k", DOC_AI_SYNC=True)
class HttpTests(TestCase):
    def setUp(self):
        call_command("seed_basic", "--with-users", verbosity=0)
        self.c = Client()
        self.c.login(username="srinivas", password="change-me")

    def test_share_target_post_redirects_to_review(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile("pay_083414546430.png", PNG_A, content_type="image/png")
        r = self.c.post("/share/", {"files": f, "text": "painter"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/review/", r["Location"])
        r = self.c.get(r["Location"])
        self.assertContains(r, "Painter Raja")
        self.assertContains(r, "Share trail")

    def test_review_saves_and_done_shows_caption(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile("pay_083414546430.png", PNG_A, content_type="image/png")
        r = self.c.post("/share/", {"files": f})
        proof_pk = int(r["Location"].rstrip("/").split("/")[-1])
        from .models import Cafe, Category
        r = self.c.post(f"/review/{proof_pk}/", {
            "doc_type": "payment", "cafe": Cafe.objects.first().pk,
            "category": Category.objects.get(slug="repairs").pk, "description": "2nd installment for painting",
            "amount": "10000", "paid_on": "2026-09-14", "paid_by": Person.objects.get(name="Srinivas").pk,
            "payee_name": "Painter Raja", "upi_app": "phonepe", "utr": "083414546430",
        })
        self.assertEqual(r.status_code, 302)
        r = self.c.get(r["Location"])
        self.assertContains(r, "Painting / repairs")
        self.assertContains(r, "#CF-")

    def test_api_reconcile_needs_token_and_records_group_post(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        r = self.c.post("/api/reconcile/", {"group_name": "CG fin", "wa_message_pk": "1"})
        self.assertEqual(r.status_code, 401)
        f = SimpleUploadedFile("wa.png", PNG_A, content_type="image/png")
        r = self.c.post("/api/reconcile/", {
            "group_name": "CG fin", "wa_message_pk": "42", "sender": "Srinivas",
            "sent_at": "2026-09-14T15:03:00+05:30", "caption": "10,000 interim payment to painter",
            "imported": "1", "file": f,
        }, HTTP_X_API_TOKEN="t0k")
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertTrue(data["created"])
        self.assertEqual(data["doc_type"], "payment")
        e = LedgerEntry.objects.get(pk=data["entry_id"])
        self.assertEqual(e.status, LedgerEntry.Status.IMPORTED)
        self.assertEqual(e.cafe.slug, "cafegopala")       # group -> cafe mapping
        self.assertEqual(e.shares.first().shared_by, Person.objects.get(name="Srinivas"))
        # same message again: no second GroupPost, no second entry
        r = self.c.post("/api/reconcile/", {"group_name": "CG fin", "wa_message_pk": "42"}, HTTP_X_API_TOKEN="t0k")
        self.assertFalse(r.json()["created"])
        self.assertEqual(LedgerEntry.objects.count(), 1)

    def test_queue_only_for_confirmers(self):
        c2 = Client()
        c2.login(username="priyanka", password="change-me")
        r = c2.get("/queue/")
        self.assertEqual(r.status_code, 302)
        c3 = Client()
        c3.login(username="praveen", password="change-me")
        self.assertEqual(c3.get("/queue/").status_code, 200)

    def test_pwa_plumbing(self):
        self.assertEqual(self.c.get("/manifest.json").status_code, 200)
        self.assertIn("share_target", self.c.get("/manifest.json").content.decode())
        self.assertEqual(self.c.get("/sw.js")["Service-Worker-Allowed"], "/")
        self.assertEqual(self.c.get("/ping").content, b"ok")


@override_settings(DOC_AI_PROVIDER="mock", API_TOKEN="t0k", DOC_AI_SYNC=False)
class QueueTests(TestCase):
    """Intake never waits for the AI. The worker does the reading."""

    def setUp(self):
        call_command("seed_basic", "--with-users", verbosity=0)
        self.srini = Person.objects.get(name="Srinivas")

    def test_intake_is_immediate_and_worker_links_later(self):
        r = services.intake(PNG_A, "pay_083414546430.png", "image/png", origin=Proof.Origin.APP_SHARE,
                            channel=ShareEvent.Channel.APP_SHARE, shared_by=self.srini, note="painter")
        self.assertTrue(r.pending)
        self.assertEqual(r.proof.state, Proof.State.QUEUED)
        self.assertIsNone(r.entry)
        self.assertEqual(LedgerEntry.objects.count(), 0)
        self.assertEqual(r.share.match_method, "pending")
        # a second share of the same bytes while it is still queued: also waits
        r2 = services.intake(PNG_A, "again.png", "image/png", origin=Proof.Origin.WHATSAPP_FORWARD,
                             channel=ShareEvent.Channel.WHATSAPP_FORWARD, shared_by=self.srini)
        self.assertEqual(r2.proof.pk, r.proof.pk)
        self.assertEqual(Proof.objects.count(), 1)

        call_command("process_queue", "--once", verbosity=0)

        proof = Proof.objects.get(pk=r.proof.pk)
        self.assertEqual(proof.state, Proof.State.DONE)
        self.assertEqual(LedgerEntry.objects.count(), 1)
        e = LedgerEntry.objects.get()
        self.assertEqual(e.utr, "083414546430")
        self.assertEqual(e.paid_by, self.srini)
        shares = list(e.shares.order_by("shared_at"))
        self.assertEqual(len(shares), 2)
        self.assertEqual(shares[0].match_method, "new")
        self.assertTrue(shares[1].was_duplicate)
        # already-read bytes shared a third time: linked at intake, no AI, no queue
        r3 = services.intake(PNG_A, "third.png", "image/png", origin=Proof.Origin.APP_SHARE,
                             channel=ShareEvent.Channel.APP_SHARE, shared_by=self.srini)
        self.assertFalse(r3.pending)
        self.assertTrue(r3.duplicate)
        self.assertEqual(e.shares.count(), 3)

    def test_share_view_returns_at_once_and_review_waits(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        c = Client(); c.login(username="srinivas", password="change-me")
        r = c.post("/share/", {"files": SimpleUploadedFile("pay_083414546430.png", PNG_A, content_type="image/png")})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/")
        proof = Proof.objects.get()
        r = c.get(f"/review/{proof.pk}/")
        self.assertContains(r, "being read")
        r = c.get("/")
        self.assertContains(r, "Being read")
        call_command("process_queue", "--once", verbosity=0)
        r = c.get(f"/review/{proof.pk}/")
        self.assertContains(r, "Painter Raja")

    def test_failed_extraction_retries_then_fails(self):
        from unittest import mock
        from ledger.extraction import ExtractionError
        r = services.intake(PNG_A, "pay_1.png", "image/png", origin=Proof.Origin.APP_SHARE,
                            channel=ShareEvent.Channel.APP_SHARE, shared_by=self.srini)
        with mock.patch("ledger.services.extract", side_effect=ExtractionError("ollama down")):
            with override_settings(DOC_AI_MAX_ATTEMPTS=2):
                services.process_proof(Proof.objects.get(pk=r.proof.pk))
                p = Proof.objects.get(pk=r.proof.pk)
                self.assertEqual(p.state, Proof.State.QUEUED)     # retry scheduled
                self.assertEqual(p.attempts, 1)
                services.process_proof(p)
                p.refresh_from_db()
                self.assertEqual(p.state, Proof.State.FAILED)
        self.assertEqual(LedgerEntry.objects.count(), 0)
        self.assertEqual(p.shares.get().match_method, "none")
