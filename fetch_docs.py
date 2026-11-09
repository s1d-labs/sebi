"""Download the order PDFs that the listings link. Two uses:

  python fetch_docs.py <detail-url>                 print the PDF URL for one order detail page, and save the PDF
  python fetch_docs.py <records.jsonl> [out-dir]    download the PDF of every record in a jsonl (resumable)

The listing records that crawl.py and standalone.py store each hold the order's detail page URL. This tool reads that
page, finds the PDF, and saves it. It skips a record whose PDF is already on disk, so a stopped run resumes cleanly.
The pure helpers (resolve, the PDF URL parser) are shared with crawl.py and standalone.py.
"""
import json
import os
import sys
import time

import requests

from crawl import HOST, UA, is_blocked, is_pdf_url, pdf_url_from_detail

DELAY_S = 1.0


def resolve(session, detail_url):
    """The absolute PDF URL for one order, or None. A listing row links the PDF directly, or an HTML detail page
    that embeds it. Reports nothing: callers count their own requests."""
    if is_pdf_url(detail_url):
        return detail_url
    r = session.get(detail_url, timeout=45, headers={"User-Agent": UA, "Referer": HOST + "/enforcement/orders.html"})
    if r.status_code != 200 or is_blocked(r.text):
        return None
    return pdf_url_from_detail(r.text)


def download(session, pdf_url, dest):
    """Save one PDF to dest. Returns the byte count, or 0 on failure or an empty file."""
    r = session.get(pdf_url, timeout=90, headers={"User-Agent": UA})
    if r.status_code != 200 or not r.content:
        return 0
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(r.content)
    return len(r.content)


def from_jsonl(path, out_dir):
    """Download the PDF of every record in a jsonl (one record per line, with `id` and `url`). Resumable."""
    session = requests.Session()
    os.makedirs(out_dir, exist_ok=True)
    got = skipped = failed = 0
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        rid, detail = rec.get("id"), rec.get("url")
        if not rid or not detail:
            continue
        dest = os.path.join(out_dir, f"{rid}.pdf")
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            skipped += 1
            continue
        pdf_url = rec.get("pdf_url") or resolve(session, detail)
        time.sleep(DELAY_S)
        if not pdf_url:
            failed += 1
            print(f"  no PDF: {rid}", file=sys.stderr)
            continue
        n = download(session, pdf_url, dest)
        time.sleep(DELAY_S)
        if n:
            got += 1
        else:
            failed += 1
            print(f"  failed: {rid} {pdf_url}", file=sys.stderr)
    print(f"downloaded {got}, skipped {skipped} (already saved), failed {failed}", file=sys.stderr)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    arg = sys.argv[1]
    if arg.endswith(".jsonl"):
        from_jsonl(arg, sys.argv[2] if len(sys.argv) > 2 else "pdf")
        return
    session = requests.Session()
    pdf_url = resolve(session, arg)
    print("PDF URL:", pdf_url)
    if pdf_url:
        rid = pdf_url.rstrip("/").rsplit("/", 1)[-1]
        n = download(session, pdf_url, rid)
        print(f"saved {rid} ({n} bytes)" if n else "download failed")


if __name__ == "__main__":
    main()
