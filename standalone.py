"""Run the sebi family without the hub: for owner use, testing, and one-off local mirrors. Resumable.

  python standalone.py --list                      show the categories and their live record counts
  python standalone.py                             scrape every category: records.jsonl + PDFs, under ./out
  python standalone.py --project sebi-orders-ao    scrape one category
  python standalone.py --max-pages 2 --no-pdf      first 2 pages of each category, metadata only (a fast check)

For each category it writes out/<project>/records.jsonl (one order per line) and out/<project>/pdf/<id>.pdf. It skips
orders already in records.jsonl and PDFs already on disk, so a stopped run resumes. It is polite: one request at a time,
at least one second between requests.
"""
import argparse
import json
import os
import sys
import time

import requests

import crawl
from family import FAMILY, PROJECTS

DELAY_S = 1.0


def live_total(session, project):
    body = crawl.listing_body(None, project["ssid"], project["smid"], 0)
    r = session.post(crawl.AJAX, data=body, timeout=45,
                     headers={"User-Agent": crawl.UA, "Referer": crawl.HOST + "/enforcement/orders.html",
                              "Content-Type": "application/x-www-form-urlencoded"})
    if r.status_code != 200 or crawl.is_blocked(r.text):
        return None, None
    return crawl.total_count(r.text)


def seen_ids(records_path):
    ids = set()
    if os.path.exists(records_path):
        for line in open(records_path, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    ids.add(json.loads(line)["id"])
                except Exception:
                    pass
    return ids


def scrape_project(session, project, out_root, max_pages, want_pdf):
    name = project["name"]
    out = os.path.join(out_root, name)
    os.makedirs(os.path.join(out, "pdf"), exist_ok=True)
    records_path = os.path.join(out, "records.jsonl")
    done = seen_ids(records_path)
    recs, pages = live_total(session, project)
    time.sleep(DELAY_S)
    if not pages:
        pages = project["approx_pages"]
    if max_pages:
        pages = min(pages, max_pages)
    print(f"[{name}] {recs if recs is not None else '?'} records, {pages} pages; {len(done)} already stored",
          file=sys.stderr)
    new = 0
    with open(records_path, "a", encoding="utf-8") as fh:
        for pg in range(pages):
            body = crawl.listing_body(None, project["ssid"], project["smid"], pg)
            r = session.post(crawl.AJAX, data=body, timeout=45,
                             headers={"User-Agent": crawl.UA, "Referer": crawl.HOST + "/enforcement/orders.html",
                                      "Content-Type": "application/x-www-form-urlencoded"})
            time.sleep(DELAY_S)
            if r.status_code != 200 or crawl.is_blocked(r.text):
                print(f"[{name}] page {pg}: blocked or {r.status_code}, stopping", file=sys.stderr)
                break
            rows = crawl.parse_listing(r.text)
            for rid, data in rows:
                if rid in done:
                    continue
                data["category"] = name
                if crawl.is_pdf_url(data["url"]):
                    data["pdf_url"] = data["url"]           # the row links the PDF directly; no detail page to read
                else:
                    detail = session.get(data["url"], timeout=45,
                                         headers={"User-Agent": crawl.UA, "Referer": crawl.HOST + "/enforcement/orders.html"})
                    time.sleep(DELAY_S)
                    if detail.status_code == 200 and not crawl.is_blocked(detail.text):
                        data["pdf_url"] = crawl.pdf_url_from_detail(detail.text)
                if want_pdf and data.get("pdf_url"):
                    dest = os.path.join(out, "pdf", f"{rid}.pdf")
                    if not (os.path.exists(dest) and os.path.getsize(dest) > 0):
                        pr = session.get(data["pdf_url"], timeout=90, headers={"User-Agent": crawl.UA})
                        time.sleep(DELAY_S)
                        if pr.status_code == 200 and pr.content:
                            with open(dest, "wb") as pf:
                                pf.write(pr.content)
                            data["pdf_path"] = dest
                            data["pdf_bytes"] = len(pr.content)
                fh.write(json.dumps(data, ensure_ascii=False) + "\n")
                fh.flush()
                done.add(rid)
                new += 1
    print(f"[{name}] added {new} records", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", help="one project name (default: all)")
    ap.add_argument("--out", default="out")
    ap.add_argument("--max-pages", type=int, default=0, help="limit pages per category (0 = all)")
    ap.add_argument("--no-pdf", action="store_true", help="store metadata only, do not download PDFs")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    session = requests.Session()
    chosen = [p for p in PROJECTS if not args.project or p["name"] == args.project]
    if not chosen:
        print("no such project; known:", ", ".join(p["name"] for p in PROJECTS), file=sys.stderr)
        sys.exit(2)
    if args.list:
        for p in chosen:
            recs, pages = live_total(session, p)
            time.sleep(DELAY_S)
            print(f"{p['name']:30s} sid={p['sid']} ssid={p['ssid']} smid={p['smid']} "
                  f"live={recs if recs is not None else '?'} (approx {p['approx_count']})")
        return
    for p in chosen:
        scrape_project(session, p, args.out, args.max_pages, not args.no_pdf)


if __name__ == "__main__":
    main()
