"""Parsing tests on saved SEBI pages: fast, no network. The pages in tests/pages/ are real responses captured on
2026-11-08. Add a saved page for every layout the site shows."""
from pathlib import Path

import crawl

PAGES = Path(__file__).parent / "pages"


def _read(name):
    return (PAGES / name).read_text(encoding="utf-8", errors="replace")


def test_ao_listing_gives_one_record_per_order():
    rows = crawl.parse_listing(_read("listing-ao.html"))
    assert 20 <= len(rows) <= 25                                    # a full AO listing page
    ids = [rid for rid, _ in rows]
    assert len(set(ids)) == len(ids)                               # no duplicate ids on a page
    assert all(rid.isdigit() for rid in ids)                       # the order's own numeric id
    for rid, data in rows:
        assert data["url"].startswith("https://www.sebi.gov.in/")
        assert data["url"].endswith(".html")
        assert data["date"] and data["title"]
        assert "id" in data and data["id"] == rid


def test_ao_listing_keeps_the_entity_in_the_title():
    rows = crawl.parse_listing(_read("listing-ao.html"))
    titles = " || ".join(d["title"] for _, d in rows).lower()
    assert "adjudication order" in titles                           # AO orders
    assert "in the matter of" in titles or "in respect of" in titles  # the entity clause


def test_ao_total_count_is_read():
    recs, pages = crawl.total_count(_read("listing-ao.html"))
    assert recs and recs > 10000                                   # AO had 12,023 orders on 2026-11-08
    assert pages and pages > 400


def test_wtm_listing_has_a_company_name():
    rows = crawl.parse_listing(_read("listing-wtm.html"))
    assert rows
    titles = " ".join(d["title"] for _, d in rows).lower()
    assert "limited" in titles or "ltd" in titles                  # WTM orders name companies


def test_recovery_listing_parses_defaulter_entries():
    rows = crawl.parse_listing(_read("listing-recovery.html"))
    assert 20 <= len(rows) <= 25
    titles = " ".join(d["title"] for _, d in rows).lower()
    assert "recovery certificate" in titles or "defaulter" in titles
    recs, _ = crawl.total_count(_read("listing-recovery.html"))
    assert recs and recs > 10000                                   # recovery had 19,472 entries


def test_waf_block_page_yields_no_records_and_is_detected():
    page = _read("waf-block.html")
    assert crawl.is_blocked(page) is True
    assert crawl.parse_listing(page) == []


def test_empty_page_gives_nothing():
    assert crawl.parse_listing("<html><body>nothing here</body></html>") == []


def test_pdf_url_from_ao_detail_page():
    url = crawl.pdf_url_from_detail(_read("detail-ao.html"))
    assert url and url.lower().endswith(".pdf")
    assert url.startswith("https://www.sebi.gov.in/sebi_data/")     # the real attachment, not the viewer wrapper
    assert "/web/?file=" not in url


def test_pdf_url_from_recovery_and_wtm_detail_pages():
    for name in ("detail-recovery.html", "detail-wtm.html"):
        url = crawl.pdf_url_from_detail(_read(name))
        assert url and url.lower().endswith(".pdf"), name
        assert url.startswith("https://www.sebi.gov.in/"), name


def test_pdf_url_is_none_when_absent():
    assert crawl.pdf_url_from_detail("<html><body>no attachment</body></html>") is None


def test_record_id_matches_the_url_suffix():
    rows = crawl.parse_listing(_read("listing-ao.html"))
    rid, data = rows[0]
    assert data["url"].rstrip(".html").endswith("_" + rid)


def test_direct_pdf_rows_carry_the_pdf_link_and_a_stable_id():
    # Some orders (e.g. exemptions) are linked straight to a PDF with no HTML detail page.
    rows = crawl.parse_listing(_read("listing-wtm.html"))
    direct = [(rid, d) for rid, d in rows if crawl.is_pdf_url(d["url"])]
    assert direct, "the WTM fixture has rows that link a PDF directly"
    for rid, d in direct:
        assert rid.startswith("sha")                          # no numeric id in a bare PDF URL
        assert d["pdf_url"] == d["url"]                       # the PDF link is kept without a detail fetch
        assert d["pdf_url"].lower().split("?")[0].endswith(".pdf")


def test_is_pdf_url():
    assert crawl.is_pdf_url("https://www.sebi.gov.in/x/order.pdf")
    assert crawl.is_pdf_url("https://www.sebi.gov.in/x/ORDER.PDF?v=2")
    assert not crawl.is_pdf_url("https://www.sebi.gov.in/x/order_12345.html")


def test_hash_fallback_id_is_deterministic_across_processes():
    # The id must not depend on Python's randomized hash(); re-running must give the same id for the same URL.
    import subprocess, sys
    code = ("import crawl;"
            "print(crawl.parse_listing(open('tests/pages/listing-wtm.html',errors='replace').read())"
            "[0][0] + '|' + [r for r,d in crawl.parse_listing("
            "open('tests/pages/listing-wtm.html',errors='replace').read()) if r.startswith('sha')][0])")
    env = {"PYTHONPATH": ".", "PYTHONHASHSEED": "random", "PATH": __import__("os").environ["PATH"]}
    a = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env).stdout.strip()
    env["PYTHONHASHSEED"] = "12345"
    b = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env).stdout.strip()
    assert a and a == b, (a, b)
