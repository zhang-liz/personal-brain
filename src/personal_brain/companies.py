"""Is this the same company? People say "Kestrel", "Kestrel Inc." and "Kestrel Labs" for one job."""

LEGAL_WORDS = {"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "gmbh", "plc", "sa", "ag", "bv", "pty"}


def core(company_id):
    """A normalized company name without the legal words at its end: "kestrel labs inc" -> "kestrel labs"."""
    words = company_id.split()
    while len(words) > 1 and words[-1] in LEGAL_WORDS:
        words.pop()
    return " ".join(words)


def one_edit(a, b):
    """True when b is a with at most one letter added, dropped or changed."""
    if abs(len(a) - len(b)) > 1:
        return False
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    return a[i + 1:] == b[i + 1:] or a[i:] == b[i + 1:] or a[i + 1:] == b[i:]


def same_company(a, b):
    """Two normalized names: equal, one the start of the other, or one typo in a long name. "apple" and "apply" stay two."""
    a, b = core(a), core(b)
    if a == b or a.startswith(b + " ") or b.startswith(a + " "):
        return True
    wa, wb = a.split(), b.split()
    return (len(a) >= 8 and len(wa) == len(wb)
            and all(x == y or (min(len(x), len(y)) >= 5 and one_edit(x, y)) for x, y in zip(wa, wb)))
