from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def inr(v):
    """Indian grouping: 1234567.5 -> 12,34,568 (rounded to rupees)."""
    try:
        n = int(round(Decimal(str(v or 0))))
    except Exception:
        return v
    neg, s = n < 0, str(abs(n))
    if len(s) <= 3:
        out = s
    else:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:]); head = head[:-2]
        if head:
            parts.insert(0, head)
        out = ",".join(parts) + "," + tail
    return ("-" if neg else "") + out


@register.filter
def pct(v, total):
    try:
        v, total = Decimal(str(v or 0)), Decimal(str(total or 0))
        return int(min(100, max(0, v * 100 / total))) if total else 0
    except Exception:
        return 0
