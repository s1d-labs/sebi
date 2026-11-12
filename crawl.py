"""The sebi family's `crawl` task (see scrape.json). Try it without the hub: python crawl.py --local <project> [page]

The pool worker runs it with the assignment's env and SCRAPE_POOL_PROJECT (which project), SCRAPE_JOB_INDEX /
SCRAPE_TOTAL_JOBS (this worker's slice), SCRAPE_MAX_HOURS (time budget). The project's env carries SID / SSID / SMID
(the SEBI listing it covers). Each queue item is one listing page.

For each page the task:
  1. asks the SEBI AJAX listing endpoint for that page (POST, stateless, server-side pagination);
  2. parses one record per order/entry (date, title, the detail page URL, a stable order id);
  3. for each order (unless FETCH_PDF=0), reads the detail page, finds the order PDF, downloads it, and keeps it in the
     family bucket at <project>/pdf/<id>.pdf (the hub signs the upload; this code never holds a storage key);
  4. stores one record per order and reports the standard counters (n_http, t_http_s, http_error, http_<code>).

The entity names (company and individual) are in the order title: the downstream graph reads them from there.
"""
import gzip
import hashlib
import html as htmllib
import os
import re
import sys
import time
import urllib.parse

import requests
from scrape_hub_client import HubClient, LocalHub, count_request

PROJECT = os.environ.get("SCRAPE_POOL_PROJECT", "")
SID = os.environ.get("SID", "2")
SSID = os.environ.get("SSID", "9")
SMID = os.environ.get("SMID", "6")
FETCH_PDF = os.environ.get("FETCH_PDF", "1") != "0"
DEADLINE = time.time() + float(os.environ.get("SCRAPE_MAX_HOURS", "1")) * 3600 - 120

HOST = "https://www.sebi.gov.in"
AJAX = HOST + "/sebiweb/ajax/home/getnewslistinfo.jsp"
UA = "s1d-labs scraper (https://github.com/s1d-labs)"
PAGE_SIZE = 25

# One order/entry per table row: <tr role='row'><td>DATE</td><td><a href='DETAIL' ... title="TITLE" ...>.
# href quoting is single in the AJAX fragment and double in the full page; the title is always double-quoted.
ROW = re.compile(
    r"<tr[^>]*\brole=['\"]row['\"][^>]*>\s*"
    r"<td[^>]*>\s*(?P<date>[^<]+?)\s*</td>\s*"
    r"<td[^>]*>\s*<a\s+[^>]*?href=['\"](?P<url>[^'\"]+)['\"][^>]*?\btitle=\"(?P<title>[^\"]*)\"",
    re.I | re.S)
ID_IN_URL = re.compile(r"_(\d+)\.html?\b", re.I)
TOTAL_PAGES = re.compile(r"name=['\"]?totalpage['\"]?\s*value=['\"]?(\d+)", re.I)
TOTAL_RECORDS = re.compile(r"(\d[\d,]*)\s*to\s*(\d[\d,]*)\s*of\s*(\d[\d,]*)\s*records", re.I)
# The order PDF is embedded through a viewer: <iframe src='.../web/?file=<PDF URL>'>. A few pages link the PDF directly.
PDF_IN_VIEWER = re.compile(r"/web/\?file=([^'\"\s>]+)", re.I)
PDF_DIRECT = re.compile(r"(?:href|src)=['\"]([^'\"]+\.pdf)['\"]", re.I)


def _abs(url):
    url = htmllib.unescape(url.strip())
    if url.startswith("http"):
        return url
    if url.startswith("/"):
        return HOST + url
    return HOST + "/" + url


def is_blocked(page):
    """True when the WAF returned its 'Unauthorized Request Blocked' page instead of the listing."""
    return "Unauthorized Request Blocked" in page


def is_pdf_url(url):
    """True when the URL points straight at a PDF (some listing rows link the order PDF with no detail page)."""
    return url.split("?", 1)[0].lower().endswith(".pdf")


def total_count(page):
    """(records, pages) the listing reports, or (None, None). Reads the hidden totalpage field and the 'X to Y of Z' line."""
    page = page.split("#@#", 1)[0]
    recs = TOTAL_RECORDS.search(page)
    pages = TOTAL_PAGES.search(page)
    r = int(recs.group(3).replace(",", "")) if recs else None
    p = int(pages.group(1)) if pages else (((r + PAGE_SIZE - 1) // PAGE_SIZE) if r else None)
    return r, p


def parse_listing(page):
    """[(record_id, data)] for the orders on one listing page. Pure: test it on saved pages.

    data = {date, title, url (the detail page), id}. The record id is the order's own numeric id from its URL,
    stable across runs; a URL without one falls back to a hash so the record is still de-duplicated."""
    if is_blocked(page):
        return []
    page = page.split("#@#", 1)[0]          # the AJAX endpoint answers "<table div>#@#<breadcrumb div>"
    out = []
    for m in ROW.finditer(page):
        url = _abs(m.group("url"))
        title = re.sub(r"\s+", " ", htmllib.unescape(m.group("title"))).strip()
        date = re.sub(r"\s+", " ", m.group("date")).strip()
        idm = ID_IN_URL.search(url)
        # the order's own numeric id when the row links an HTML detail page; a stable hash of the URL otherwise
        # (some rows, e.g. exemption orders, link a PDF directly). hashlib, not hash(), so the id is stable across runs.
        rid = idm.group(1) if idm else "sha" + hashlib.sha1(url.encode()).hexdigest()[:12]
        data = {"id": rid, "date": date, "title": title, "url": url}
        if is_pdf_url(url):
            data["pdf_url"] = url                # the listing links the PDF directly; no detail page to read
        out.append((rid, data))
    return out


def pdf_url_from_detail(page):
    """The absolute URL of the order PDF on a detail page, or None. Pure: test it on saved pages."""
    m = PDF_IN_VIEWER.search(page)
    if m:
        return _abs(urllib.parse.unquote(m.group(1)))
    m = PDF_DIRECT.search(page)
    return _abs(m.group(1)) if m else None


def listing_body(page, ssid, smid, do_direct):
    return {
        "nextValue": "1", "next": "n", "doDirect": str(do_direct),
        "search": "", "fromDate": "", "toDate": "", "fromYear": "", "toYear": "", "deptId": "",
        "sid": str(SID), "ssid": str(ssid), "smid": str(smid), "ssidhidden": str(ssid),
        "intmid": "-1", "sText": "", "ssText": "", "smText": "",
    }


# ---- hub-driven run ---------------------------------------------------------------------------------------------

def get(hub, session, url, method="GET", data=None):
    """One polite request. Reports the counters the hub throttles on. Returns the text body, or None on any failure."""
    t = time.time()
    headers = {"User-Agent": UA, "Referer": HOST + "/enforcement/orders.html"}
    try:
        if method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            r = session.post(url, data=data, timeout=45, headers=headers)
        else:
            r = session.get(url, timeout=45, headers=headers)
    except requests.RequestException:
        count_request(hub, None)                # http_error + hub.error()
        return None
    count_request(hub, r.status_code, time.time() - t)   # n_http, t_http_s, http_<code>; 403/429/503 -> hub.block()
    if r.status_code != 200:
        return None
    if method == "POST":
        return r.text                           # the AJAX endpoint is UTF-8 text, not a document with a charset
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def fetch_pdf(hub, session, rid, detail_url, record):
    """Find an order's PDF and keep it in the bucket. The row links the PDF directly, or an HTML detail page that
    embeds it. Updates `record` in place (pdf_url, pdf_path, pdf_bytes)."""
    if is_pdf_url(detail_url):
        pdf_url = detail_url                        # the listing already pointed at the PDF
    else:
        detail = get(hub, session, detail_url)
        if detail is None or is_blocked(detail or ""):
            hub.failure("detail", rid, "detail page did not load", url=detail_url)
            return
        pdf_url = pdf_url_from_detail(detail)
        record["pdf_url"] = pdf_url
        if not pdf_url:
            hub.failure("pdf_link", rid, "no PDF on detail page", url=detail_url)
            return
    if not FETCH_PDF:
        return
    t = time.time()
    try:
        r = session.get(pdf_url, timeout=90, headers={"User-Agent": UA})
    except requests.RequestException:
        count_request(hub, None)
        hub.failure("pdf", rid, "pdf request failed", url=pdf_url)
        return
    count_request(hub, r.status_code, time.time() - t)
    if r.status_code != 200 or not r.content:
        if r.status_code == 200:
            hub.metric("http_200")                  # an empty 200 body is counted as before
        hub.failure("pdf", rid, f"pdf status {r.status_code}", url=pdf_url)
        return
    path = f"pdf/{rid}.pdf"
    hub.upload_many([(path, r.content, "application/pdf")])
    record["pdf_path"] = f"{PROJECT}/{path}"
    record["pdf_bytes"] = len(r.content)
    hub.clear_failure("pdf", rid) if hasattr(hub, "clear_failure") else None


def handle_page(hub, session, item):
    """One queue item = one listing page. Returns the number of records stored (for hub.complete)."""
    page = item["payload"]["page"]
    ssid = item["payload"].get("ssid", SSID)
    smid = item["payload"].get("smid", SMID)
    body = listing_body(None, ssid, smid, page)
    text = get(hub, session, AJAX, method="POST", data=body)
    if text is None:
        return None
    if is_blocked(text):
        hub.block()
        return None
    hub.upload_many([(f"pages/{item['key']}.html.gz", gzip.compress(text.encode()), "application/gzip")])
    rows = parse_listing(text)
    for rid, data in rows:
        data["category"] = PROJECT
        data["page_key"] = item["key"]
        if FETCH_PDF:                                        # read the detail page, find the PDF, store it in the bucket
            fetch_pdf(hub, session, rid, data["url"], data)
            time.sleep(max(0.5, hub.settings.get("delay_ms", 0) / 1000))     # SEBI's WAF refuses fast bursts
        # when FETCH_PDF=0 this is a fast metadata-only pass; pdf_url is kept only for rows that link a PDF directly
        hub.record(rid, data)
    return len(rows)


def local(project, page=0):
    global PROJECT
    PROJECT = PROJECT or project
    hub = LocalHub()     # counters in hub.counts, records to stdout, uploads only listed
    n = handle_page(hub, requests.Session(), {"key": f"{project}-p{page}", "payload": {"page": page}})
    print(f"{n} records, counters {hub.counts}", file=sys.stderr)


def main():
    hub = HubClient(PROJECT, job_index=int(os.environ.get("SCRAPE_JOB_INDEX", "0")),
                    total_jobs=int(os.environ.get("SCRAPE_TOTAL_JOBS", "1")))
    session = requests.Session()
    try:
        while time.time() < DEADLINE:
            if hub.settings.get("paused"):
                time.sleep(30)
                continue
            items = hub.claim(1, steal=True, retry_failed=True)
            if not items:
                break
            item = items[0]
            n = handle_page(hub, session, item)
            if n is None:
                hub.complete([item["key"]], "failed", error="listing page did not load")
            else:
                hub.complete([item["key"]], "done", fetched=n)
            time.sleep(max(1.0, hub.settings.get("delay_ms", 0) / 1000))     # SEBI: be sequential and polite
        hub.close()
    except BaseException as e:
        hub.close("failed", f"{type(e).__name__}: {e}"[:200])
        raise


if __name__ == "__main__":
    if sys.argv[1:2] == ["--local"]:
        proj = sys.argv[2] if len(sys.argv) > 2 else "sebi-orders-ao"
        pg = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        local(proj, pg)
    else:
        main()
