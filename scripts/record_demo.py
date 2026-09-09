#!/usr/bin/env python3
"""Record the walkthrough video the landing page links to.

It films `e2e/journey.py`, the same sequence the end-to-end test asserts, so
the video cannot show a flow the application no longer has: if the journey
breaks, the test goes red and the recording fails in the same way.

Usage:

    python -m pip install -r requirements-dev.txt
    python -m playwright install chromium
    python scripts/record_demo.py

Writes public/static/demo/journey.webm, which the walkthrough page plays. The
recording runs against a throwaway database, never the development one.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from e2e.journey import lifecycle  # noqa: E402
from e2e.server import free_port, running_app  # noqa: E402

OUT = ROOT / "public" / "static" / "demo" / "journey.webm"
POSTER = ROOT / "public" / "static" / "demo" / "journey-poster.png"

# Slow enough to follow without turning the video into a long sit. slow_mo
# spaces out every browser action; the beat pause holds the frame at the start
# of each stage so a caption has time to be read.
SLOW_MO_MS = 550
BEAT_PAUSE_MS = 1_600
VIEWPORT = {"width": 1440, "height": 900}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ab-demo-") as tmp:
        tmp_path = Path(tmp)
        db = tmp_path / "demo.sqlite3"
        videos = tmp_path / "video"

        with running_app(db, free_port()) as base_url, sync_playwright() as p:
            browser = p.chromium.launch(slow_mo=SLOW_MO_MS)
            context = browser.new_context(
                base_url=base_url,
                viewport=VIEWPORT,
                record_video_dir=str(videos),
                record_video_size=VIEWPORT,
            )
            page = context.new_page()

            def beat(caption: str) -> None:
                print(f"  · {caption}")
                page.wait_for_timeout(BEAT_PAUSE_MS)

            # Open on the landing page, so the video starts where a visitor does.
            page.goto("/")
            page.wait_for_timeout(BEAT_PAUSE_MS)

            # The still the <video> element shows before anyone presses play.
            POSTER.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(POSTER))

            for _step in lifecycle(page, beat=beat):
                pass

            page.wait_for_timeout(BEAT_PAUSE_MS)

            # The video is only written out when the context closes.
            video = page.video
            context.close()
            browser.close()

            if video is None:
                print("no video was recorded", file=sys.stderr)
                return 1

            OUT.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(video.path(), OUT)

    size_mb = OUT.stat().st_size / 1_048_576
    print(f"\nWrote {OUT.relative_to(ROOT)} ({size_mb:.1f} MB)")
    print(f"Wrote {POSTER.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
