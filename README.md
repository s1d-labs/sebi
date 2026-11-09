# sebi

This family scrapes SEBI (www.sebi.gov.in). It collects the entity-rich listings, and the enforcement listings first.
Each record is one order or one entry. The record holds the date, the title, the detail page URL, and the order PDF URL.
The task stores the order PDF in the family bucket at `<project>/pdf/<id>.pdf`. The company and individual names are in
the title. The downstream graph reads the names from there.

The site answers from GitHub runners (US and EU IP addresses). The family needs no geo and no proxy (`geo=""`, `proxy=""`).

## How SEBI serves the listings

Every listing is one SEBI section, chosen by three numbers: `sid`, `ssid`, `smid`. The full page only ever shows page 1.
The site pages the list with an AJAX endpoint:

    POST https://www.sebi.gov.in/sebiweb/ajax/home/getnewslistinfo.jsp
    sid=<sid>&ssid=<ssid>&smid=<smid>&next=n&nextValue=1&doDirect=<page>   (page is 0-based)

The endpoint needs no cookie and no token. The reply is `<table div>#@#<breadcrumb div>`. The table holds one row per
order: a date cell and a title cell with the detail page link. The reply also gives the total record count and the page
count. A listing row links one of two things:

- an HTML detail page (URL ends `_<id>.html`); the page embeds the PDF through a viewer: `.../web/?file=<PDF URL>`;
- the order PDF directly (URL ends `.pdf`); these are exemption orders and some recovery documents.

The record id is the order's own numeric id from the URL. A direct PDF URL has no numeric id, so the id is a stable
hash of the URL (`sha...`). The id is stable across runs, so a re-run updates the same record.

SEBI runs a WAF. The WAF blocks fast request bursts with an "Unauthorized Request Blocked" page. The task is sequential
and polite (at least 0.5 s between requests), so the WAF does not block it.

## Projects

`family.py` lists the projects. Each project is one SEBI listing. Each queue item is one listing page.

| Project | Listing (sid/ssid/smid) | Records (2026-11-08) |
|---|---|---|
| sebi-orders-ao | 2/9/6 — Adjudication Officer (AO) orders | 12,023 |
| sebi-orders-wtm | 2/9/2 — Whole-Time Member / Chairperson orders | 6,433 |
| sebi-orders-settlement-board | 2/9/3 — Settlement orders (Board) | 1,981 |
| sebi-orders-settlement-court | 2/9/146 — Settlement orders (Tribunal/Court) | 27 |
| sebi-orders-sat | 2/9/1 — Securities Appellate Tribunal (SAT) orders | 2,447 |
| sebi-orders-court | 2/9/7 — Court orders | 1,118 |
| sebi-orders-special-court | 2/9/77 — Special Court orders | 91 |
| sebi-orders-ed-cgm | 2/9/133 — ED / CGM (quasi-judicial) orders | 447 |
| sebi-orders-reg30a | 2/9/138 — Regulation 30A (Intermediaries) orders | 52 |
| sebi-recovery | 2/50/0 — Recovery proceedings (defaulters, attachment) | 19,472 |
| sebi-recovery-auction | 2/79/0 — Auction notices under recovery | 390 |
| sebi-orders-not-served | 2/12/0 — Orders that could not be served | 636 |
| sebi-unserved-summons | 2/13/0 — Unserved summons / notices | 4,587 |
| sebi-public-notices | 6/25/0 — Public notices (caution, unauthorised entities) | 290 |

The total is about 50,000 orders and entries. All projects hit one host, so the hub caps the workers that run at once.

## Files

- `crawl.py` — the pool task. It reads one listing page, stores one record per order, and stores the PDF in the bucket.
  `parse_listing()` and `pdf_url_from_detail()` are pure functions; `tests/` checks them on saved pages.
  `python crawl.py --local <project> [page]` runs one page without the hub.
- `fetch_docs.py` — download the order PDFs. `python fetch_docs.py <detail-url>` saves one PDF. `python fetch_docs.py
  <records.jsonl> [out-dir]` saves the PDF of every record (resumable).
- `standalone.py` — run the whole family without the hub, for owner use and tests. It writes `out/<project>/records.jsonl`
  and `out/<project>/pdf/<id>.pdf`. It skips orders and PDFs it already has, so a stopped run resumes.
- `family.py` — the projects and the health check. `setup_family.py` — register, seed the queue, set the pool.

## Environment

- `SID`, `SSID`, `SMID` — the SEBI listing the project covers (set per project in `family.py`).
- `FETCH_PDF` — `1` (default) reads the detail page and downloads the PDF. `0` stores the metadata only and does not
  read detail pages; it keeps a PDF URL only when the listing links the PDF directly.

## Test

- `python -m pytest tests` — the pure parsers on saved pages, and the PDF path with a fake hub.
- `scrape-check --items 2 --project sebi-orders-settlement-court --env FETCH_PDF=0` — the hub contract on a local stand-in
  hub, on a small category. The **check** workflow runs both on every push.

## Scale note

SEBI lists the newest order first. New orders push the older orders to a higher page number. Re-run **setup** now and then
to grow the queue as SEBI adds orders. A re-run updates records by their stable id, so overlapping pages do not duplicate.
