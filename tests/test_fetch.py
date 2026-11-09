"""The PDF download path (FETCH_PDF=1), tested with fake hub and session, no network. It checks both kinds of row:
one that links a PDF directly, and one that links an HTML detail page that embeds the PDF."""
from pathlib import Path

import crawl

PAGES = Path(__file__).parent / "pages"


class FakeResp:
    def __init__(self, status=200, content=b"", text=""):
        self.status_code = status
        self.content = content
        self.text = text
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"


class FakeSession:
    """Answers each URL from a canned map; records the order of calls."""
    def __init__(self, by_url):
        self.by_url = by_url
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        return self.by_url.get(url, FakeResp(404))

    def post(self, url, **kw):
        self.calls.append(("POST", url))
        return self.by_url.get(url, FakeResp(404))


class FakeHub:
    settings = {"threads": 1, "delay_ms": 0, "paused": False}

    def __init__(self):
        self.uploads = []
        self.failures = []
        self.cleared = []
        self.counts = {}
        self.records = []

    def record(self, rid, data):
        self.records.append((rid, data))

    def metric(self, name, value=1):
        self.counts[name] = self.counts.get(name, 0) + value

    def error(self, n=1):
        self.metric("errors", n)

    def block(self, n=1):
        self.metric("blocked", n)

    def upload_many(self, files):
        self.uploads += files
        return [{"path": p} for p, _b, _t in files]

    def failure(self, kind, key, reason, **detail):
        self.failures.append((kind, key, reason))

    def clear_failure(self, kind, key):
        self.cleared.append((kind, key))


def test_fetch_pdf_for_a_direct_pdf_row(monkeypatch):
    monkeypatch.setattr(crawl, "FETCH_PDF", True)
    monkeypatch.setattr(crawl, "PROJECT", "sebi-orders-wtm")
    pdf_url = "https://www.sebi.gov.in/sebi_data/attachdocs/nov-2026/exemption_order.pdf"
    hub = FakeHub()
    session = FakeSession({pdf_url: FakeResp(200, b"%PDF-1.6 ...bytes...")})
    rec = {"id": "sha123", "url": pdf_url}
    crawl.fetch_pdf(hub, session, "sha123", pdf_url, rec)
    assert session.calls == [pdf_url]                         # no detail page fetched, straight to the PDF
    assert hub.uploads and hub.uploads[0][0] == "pdf/sha123.pdf"
    assert hub.uploads[0][2] == "application/pdf"
    assert rec["pdf_path"] == "sebi-orders-wtm/pdf/sha123.pdf"
    assert rec["pdf_bytes"] == len(b"%PDF-1.6 ...bytes...")
    assert not hub.failures


def test_fetch_pdf_through_a_detail_page(monkeypatch):
    monkeypatch.setattr(crawl, "FETCH_PDF", True)
    monkeypatch.setattr(crawl, "PROJECT", "sebi-orders-ao")
    detail_url = "https://www.sebi.gov.in/enforcement/orders/sep-2026/adjudication-order_104282.html"
    detail_html = (PAGES / "detail-ao.html").read_text(encoding="utf-8", errors="replace")
    pdf_url = crawl.pdf_url_from_detail(detail_html)
    hub = FakeHub()
    session = FakeSession({detail_url: FakeResp(200, text=detail_html),
                           pdf_url: FakeResp(200, b"%PDF-1.6 order bytes")})
    rec = {"id": "104282", "url": detail_url}
    crawl.fetch_pdf(hub, session, "104282", detail_url, rec)
    assert session.calls == [detail_url, pdf_url]             # detail page first, then the PDF it embeds
    assert rec["pdf_url"] == pdf_url
    assert rec["pdf_path"] == "sebi-orders-ao/pdf/104282.pdf"
    assert hub.uploads[0][0] == "pdf/104282.pdf"
    assert not hub.failures


def test_fetch_pdf_records_a_failure_when_no_pdf(monkeypatch):
    monkeypatch.setattr(crawl, "FETCH_PDF", True)
    detail_url = "https://www.sebi.gov.in/enforcement/orders/x_1.html"
    hub = FakeHub()
    session = FakeSession({detail_url: FakeResp(200, text="<html>no attachment here</html>")})
    rec = {"id": "1", "url": detail_url}
    crawl.fetch_pdf(hub, session, "1", detail_url, rec)
    assert hub.failures and hub.failures[0][0] == "pdf_link"
    assert not hub.uploads


def test_metadata_only_mode_downloads_nothing(monkeypatch):
    # FETCH_PDF=0: handle_page must not fetch detail pages or PDFs; it keeps a direct PDF link only.
    monkeypatch.setattr(crawl, "FETCH_PDF", False)
    monkeypatch.setattr(crawl, "PROJECT", "sebi-orders-wtm")
    listing = (PAGES / "listing-wtm.html").read_text(encoding="utf-8", errors="replace")
    hub = FakeHub()
    session = FakeSession({crawl.AJAX: FakeResp(200, text=listing)})
    n = crawl.handle_page(hub, session, {"key": "wtm-p0000", "payload": {"page": 0, "ssid": 9, "smid": 2}})
    assert n == 25
    assert session.calls == [("POST", crawl.AJAX)]            # only the listing request, no detail or PDF fetches
    # the gzipped listing page is still uploaded; no PDF uploads
    assert any(p.startswith("pages/") for p, _b, _t in hub.uploads)
    assert not any(p.startswith("pdf/") for p, _b, _t in hub.uploads)
