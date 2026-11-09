"""What the sebi family scrapes. Change this file first.

FAMILY must equal the repo name (s1d-labs/sebi) and the family the hub owner added on scrape.s1d.me/runners.
Every project is one SEBI listing (one sid/ssid/smid), read page by page. All projects hit one host, www.sebi.gov.in,
so the hub caps the workers that run at once on it. The site answers from GitHub runners (US/EU), so no geo and no proxy.

Each category below carries the record count SEBI showed on 2026-11-08; setup_family.py reads the live page count and
seeds one queue item per listing page. The counts give scale and a fallback if the live read fails.
"""
import json
from pathlib import Path

FAMILY = json.loads((Path(__file__).parent / "scrape.json").read_text())["family"]
TARGET = "www.sebi.gov.in"
PAGE_SIZE = 25

# name suffix, title, sid, ssid, smid, approx record count (2026-11-08), record label
CATEGORIES = [
    # Enforcement orders (sid=2, ssid=9): named companies and individuals in every title -> the graph gold.
    ("orders-ao",               "Enforcement: Adjudication Officer (AO) orders",            2, 9,   6, 12023, "order"),
    ("orders-wtm",              "Enforcement: Whole-Time Member / Chairperson orders",      2, 9,   2,  6433, "order"),
    ("orders-settlement-board", "Enforcement: Settlement orders passed by the Board",       2, 9,   3,  1981, "order"),
    ("orders-settlement-court", "Enforcement: Settlement orders passed by Tribunal/Court",  2, 9, 146,    27, "order"),
    ("orders-sat",              "Enforcement: Securities Appellate Tribunal (SAT) orders",  2, 9,   1,  2447, "order"),
    ("orders-court",            "Enforcement: Court orders",                                2, 9,   7,  1118, "order"),
    ("orders-special-court",    "Enforcement: Special Court orders",                        2, 9,  77,    91, "order"),
    ("orders-ed-cgm",           "Enforcement: ED / CGM (quasi-judicial) orders",            2, 9, 133,   447, "order"),
    ("orders-reg30a",           "Enforcement: Regulation 30A (Intermediaries) orders",      2, 9, 138,    52, "order"),
    # Recovery and defaulters (sid=2): recovery certificates, attachment, and defaulter entities.
    ("recovery",                "Enforcement: Recovery proceedings (defaulters, attachment)", 2, 50, 0, 19472, "entry"),
    ("recovery-auction",        "Enforcement: Auction notices under recovery proceedings",  2, 79,  0,   390, "entry"),
    ("orders-not-served",       "Enforcement: Orders that could not be served (defaulters)", 2, 12,  0,   636, "entry"),
    ("unserved-summons",        "Enforcement: Unserved summons / notices (defaulters)",      2, 13,  0,  4587, "entry"),
    # Caution / public notices (sid=6): warnings about unauthorised and illegal entities.
    ("public-notices",          "Media: Public notices (caution, unauthorised entities)",   6, 25,  0,   290, "notice"),
]

PROBE = f"{FAMILY}:{TARGET}"


def _pages(count):
    return max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)


PROJECTS = []
for suffix, title, sid, ssid, smid, count, label in CATEGORIES:
    name = f"{FAMILY}-{suffix}"
    PROJECTS.append({
        "name": name,
        "title": title,
        "task": "crawl",
        "target": TARGET,
        "probe": PROBE,
        "unit_label": "page",
        "record_label": label,
        "sid": sid, "ssid": ssid, "smid": smid,
        "approx_count": count,
        "approx_pages": _pages(count),
        # the SEBI listing the task needs; FETCH_PDF=0 would store metadata only (no PDF download)
        "env": {"SID": str(sid), "SSID": str(ssid), "SMID": str(smid), "FETCH_PDF": "1"},
        # one item per listing page; `page` is the 0-based page index the AJAX endpoint wants (doDirect)
        "items": [{"key": f"{suffix}-p{n:04d}",
                   "payload": {"page": n, "ssid": ssid, "smid": smid, "expected": PAGE_SIZE}}
                  for n in range(_pages(count))],
    })

# One health check for the host. Up = HTTP 200 within 20 s and the body contains `expect` (case-insensitive).
# The hub checks .gov.in probes through an Indian proxy; SEBI answers from India, so the check passes.
PROBES = [{"name": PROBE, "url": "https://www.sebi.gov.in/enforcement/orders.html", "expect": "Orders of AO"}]
