"""Is this the same company? People say "Kestrel", "Kestrel Inc." and "Kestrel Labs" for one job."""

import unicodedata

LEGAL_WORDS = {"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "gmbh", "plc", "sa", "ag", "bv", "pty"}
# People drop these words from a name. "Kestrel Capital" is not "Kestrel": a move there must not look like the same job.
NAME_ENDINGS = {"labs", "lab", "data", "ai", "tech", "technologies", "technology", "software", "systems", "solutions", "group",
                "holdings", "global", "international", "studio", "studios", "hq", "io", "digital", "cloud", "networks"}


def norm(text):
    """Lowercase words in any script, without punctuation: "Kestrel, Inc." -> "kestrel inc", "三星" -> "三星"."""
    kept = "".join(c if unicodedata.category(c)[0] in "LMN" else " " for c in (text or "").lower())
    return " ".join(kept.split())


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


def typo(a, b):
    """One letter off, not in the first letter: "Cork" and "York" are two places."""
    return a[:1] == b[:1] and one_edit(a, b)


def same_company(a, b):
    """Two normalized names: equal, or the same but for common last words. "status labs" and "stratus labs" stay two."""
    a, b = core(a), core(b)
    short, long = sorted((a, b), key=len)
    return a == b or (long.startswith(short + " ") and set(long[len(short) + 1:].split()) <= NAME_ENDINGS)


def near_company(a, b):
    """One letter off in a long name. It can be a typo or another company, so only the user can say."""
    a, b = core(a), core(b)
    wa, wb = a.split(), b.split()
    return (a != b and len(a) >= 8 and len(wa) == len(wb)
            and all(x == y or (min(len(x), len(y)) >= 5 and typo(x, y)) for x, y in zip(wa, wb)))
