# mail — invoices that arrive by email

`imap_pull.py` reads one Gmail label over IMAP and posts every PDF / image
attachment to the server. No Google API project needed — an App Password and
a label are enough. First run with `--all --dry-run`, then `--all`, then put
it on cron (Hetzner box or Mac) every 15 minutes with no flags.

Invoices arriving in WhatsApp don't come through here at all: a photo of a
bill shared to the app is read as an invoice by the same document AI.
