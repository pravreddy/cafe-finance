# 04 — Data model (as built in `server/ledger/models.py`)

```
Cafe              name, slug, fastpos_tenant, whatsapp_groups (list of group names → this cafe)
Person            user (login, optional), name, phone, whatsapp_jid, role (partner/staff/other),
                  default_cafe, can_confirm
Category          name, slug, kind (expense/income/transfer), parent, sort
                  seed: staff salary, vegetables, groceries, dairy, gas, packaging, rent,
                        utilities, painting/repairs, equipment, transport, marketing,
                        licences/fees, misc, partner money in/out (transfer),
                        cash sales / other income (income)

Invoice           MONEY OWED. cafe, vendor_name, vendor_gstin, invoice_number, invoice_date,
                  due_date, subtotal, tax, total, category, description,
                  status (pending|open|part_paid|paid|rejected), extracted_json, created_by
                  unique (vendor_name, invoice_number) when number is not blank
                  paid_total = sum of CONFIRMED payments; outstanding = total − paid_total

LedgerEntry       MONEY MOVED. cafe, paid_on (from the receipt), paid_at, amount, category,
                  paid_by (Person), payee_name, payee_phone_masked, description (the comment),
                  invoice (FK, nullable — the invoice this payment settles, in whole or part),
                  source (app|whatsapp|email|manual), status (pending|confirmed|rejected|imported),
                  utr (unique), txn_id, upi_app, debited_from_last4, confidence,
                  cf_tag (#CF-000123, unique, auto), reversal_of (self FK),
                  created_by, confirmed_by, confirmed_at

Proof             A FILE THAT REACHED US. file, original_name, mime, sha256 (unique),
                  kind (payment|invoice_pdf|invoice_img|other),
                  origin (app_share|wa_forward|wa_group|email|upload), origin_ref,
                  entry (FK) or invoice (FK), extracted_json, extracted_at, extraction_error

ShareEvent        ONE ROW PER ARRIVAL. proof, entry / invoice, shared_by (Person) or
                  shared_by_label (raw sender), shared_at, channel
                  (app_share|wa_forward|wa_group|email|manual), note (caption),
                  was_duplicate, match_method — never merged, never counted.
                  This is the "shared on this day by this person" trail.

GroupPost         WHAT THE RECONCILER SAW. group_name, wa_message_pk (unique with group),
                  sender, sent_at, caption, media_path, proof, matched_entry / matched_invoice,
                  match_method, dismissed ("not money")
```

Email invoices are not a separate table: a Proof with `origin=email` and
`origin_ref=<Message-ID>` linked to an Invoice.

## Rules

- **Dedup order (`dedup.py`):** UTR → app txn id → file sha256 → `#CF` tag →
  amount + paid_on + comment (only when the receipt carried no id) →
  weak (amount + date ± 1 + payee) as candidates for a human, never auto-merged.
- A receipt that has a UTR / txn id and matches nothing is a **new payment**,
  however alike the amount, date and comment look.
- A duplicate share is **never an error**: it becomes a ShareEvent on the existing
  entry and the user sees "already recorded on 14 Sep by Srinivas — added your share to the trail".
- Payment date comes from the receipt; share date from the event; both are kept.
- **Nothing hits reports until `status = confirmed`.** The review queue is the product.
- Confirmed entries are not edited; corrections are a reversal row (negative amount,
  `reversal_of`) + a new row — same principle as FastPOS bills.
- An invoice's status is derived from confirmed payments only: open → part_paid → paid.
- `paid_by` is what makes the **partner balance** possible: per cafe, per month,
  contributions by person vs expenses paid by person → who is owed what.

## Reports (not built yet — the models carry everything needed)

- Monthly P&L per cafe: income (FastPOS API by day) − confirmed expenses by category
- Partner balance: running total per person per cafe, with proofs
- Open invoices and outstanding amounts
- "Unproven": entries with no proof, and group posts with no entry
