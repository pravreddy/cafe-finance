"""The worker. Reads queued proofs with the document AI and updates the records.

    python manage.py process_queue            # run forever (the `worker` container)
    python manage.py process_queue --once     # drain what is queued now, then exit

Why a table and not Kafka/Redis: three cafes produce a few dozen documents a
day. A `state` column on Proof, claimed with SELECT … FOR UPDATE SKIP LOCKED,
gives us durability, retries, ordering and "what is waiting" in the admin —
with zero extra infrastructure on the box. If volume ever needs it, the only
thing to swap is claim_next(); intake and process_proof stay the same.
"""
import signal
import time

from django.core.management.base import BaseCommand

from ledger import services
from ledger.models import Proof


class Command(BaseCommand):
    help = "Read queued proofs with the document AI (Ollama) and update the ledger."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="process what is queued now and exit")
        parser.add_argument("--sleep", type=float, default=3.0, help="idle poll interval in seconds")

    def handle(self, *args, **opts):
        stop = {"now": False}

        def _sig(*_):
            stop["now"] = True
            self.stdout.write("stopping after the current document…")

        signal.signal(signal.SIGTERM, _sig)
        signal.signal(signal.SIGINT, _sig)

        # A previous worker that died mid-read leaves PROCESSING rows; re-queue them.
        n = Proof.objects.filter(state=Proof.State.PROCESSING).update(state=Proof.State.QUEUED)
        if n:
            self.stdout.write(f"re-queued {n} proof(s) left in 'processing'")

        done = 0
        while not stop["now"]:
            proof = services.claim_next()
            if proof is None:
                if opts["once"]:
                    break
                time.sleep(opts["sleep"])
                continue
            t0 = time.monotonic()
            r = services.process_proof(proof)
            dt = time.monotonic() - t0
            what = (r.entry.cf_tag if r.entry else r.invoice if r.invoice else r.doc_type)
            self.stdout.write(f"proof {proof.pk} [{proof.state}] {dt:.1f}s → {what} "
                              f"{'(dup, ' + r.match_method + ')' if r.duplicate else ''} {r.extraction_error}")
            done += 1
        self.stdout.write(self.style.SUCCESS(f"processed {done}"))
