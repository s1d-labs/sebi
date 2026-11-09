"""Put the sebi family's projects in the pool and register its health check. Run by the Setup workflow (GitHub Actions:
the hub trusts this repo, s1d-labs/sebi, through its OIDC token). Safe to run again: queue items that exist are kept.

Before it seeds, it reads the live record count of each SEBI listing and seeds one queue item per page. The live read
is best effort: if SEBI does not answer, the fixed page count in family.py is used instead.
"""
import time

import requests
from scrape_hub_client import register_project, seed_queue, set_pool, set_probes

import crawl
from family import FAMILY, PAGE_SIZE, PROBES, PROJECTS


def live_pages(project):
    """The number of listing pages SEBI reports now, or None if the read fails."""
    body = crawl.listing_body(None, project["ssid"], project["smid"], 0)
    try:
        r = requests.post(crawl.AJAX, data=body, timeout=45,
                          headers={"User-Agent": crawl.UA, "Referer": crawl.HOST + "/enforcement/orders.html",
                                   "Content-Type": "application/x-www-form-urlencoded"})
    except requests.RequestException:
        return None
    if r.status_code != 200 or crawl.is_blocked(r.text):
        return None
    _, pages = crawl.total_count(r.text)
    return pages


def items_for(project):
    """Queue items for the project: one per page, refreshed to the live page count when SEBI answers."""
    pages = live_pages(project)
    time.sleep(1.0)                                 # one host, stay polite during setup
    if not pages:
        return project["items"]                     # fall back to the fixed count from family.py
    suffix = project["name"][len(FAMILY) + 1:]
    return [{"key": f"{suffix}-p{n:04d}",
             "payload": {"page": n, "ssid": project["ssid"], "smid": project["smid"], "expected": PAGE_SIZE}}
            for n in range(pages)]


print("health checks:", set_probes(FAMILY, PROBES))
for p in PROJECTS:
    items = items_for(p)
    register_project(p["name"], title=p["title"], unit_label=p["unit_label"], record_label=p["record_label"],
                     expected_units=len(items), expected_records=sum(i["payload"].get("expected", 0) for i in items))
    set_pool(p["name"], FAMILY, p["task"], target=p["target"], env=p["env"], probe=p["probe"])
    print(p["name"], "pages:", len(items), "queue:", seed_queue(p["name"], items))
