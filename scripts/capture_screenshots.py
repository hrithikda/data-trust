"""Capture README screenshots of a running DataTrust app.

Usage (app must be running on --url):
    pip install -e ".[docs]" && playwright install chromium   # or rely on a local Google Chrome
    python scripts/capture_screenshots.py --url http://localhost:8501
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import Browser, Error, Playwright, sync_playwright

SHOTS = {
    "overview": "",
    "issue": "issues?issue=DQ-0018",
    "prioritization": "priority",
    "impact": "impact?asset=stg_shopfront__orders",
    "lineage": "lineage?asset=int_orders__enriched",
    "catalog": "catalog",
    "dataset": "dataset?asset=fct_orders",
    "evaluation": "evaluation",
}


def launch(p: Playwright) -> Browser:
    """Playwright's bundled Chromium if installed, otherwise the system Google Chrome."""
    try:
        return p.chromium.launch()
    except Error:
        return p.chromium.launch(channel="chrome")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8501")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "docs" / "images")
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page(viewport={"width": 1500, "height": 1000}, device_scale_factor=1)
        for name, path in SHOTS.items():
            if args.only and name not in args.only:
                continue
            page.goto(f"{args.url.rstrip('/')}/{path}")
            page.wait_for_selector("h1", timeout=30_000)
            page.wait_for_function("!document.querySelector('[data-testid=\"stSkeleton\"]')", timeout=30_000)
            page.wait_for_timeout(3500)
            page.screenshot(path=str(args.out / f"{name}.png"), full_page=False)
            print(f"captured {name}")
        browser.close()


if __name__ == "__main__":
    main()
