"""The curated lifecycle and the authorisation boundary, through a browser.

The lifecycle steps live in `journey.py` because scripts/record_demo.py films
the same sequence. Keeping one copy is what stops the walkthrough video and
the application drifting apart.

The lifecycle is one test rather than seven because each step only exists once
the previous one has happened. Seven tests sharing a database would pass or
fail depending on the order pytest happened to run them in.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from .journey import JOB, lifecycle, sign_in_as, sign_out

JOB_UUID = re.compile(r"/jobs/([0-9a-f]{8}-[0-9a-f-]+)")


def test_the_full_curated_lifecycle(page: Page) -> None:
    for step in lifecycle(page):
        # Naming the step makes a failure read as "failed during: staff
        # release the bids" rather than as a line number in a long function.
        print(f"  · {step}")


class TestTheAuthorisationBoundary:
    """The promise the marketplace is built on, asserted from the browser."""

    def test_an_uninvited_contractor_sees_no_invitations(self, page: Page) -> None:
        sign_in_as(page, "Demo Contractor, not invited")
        page.goto("/invitations")

        expect(page.get_by_text(re.compile("no invitations yet", re.I))).to_be_visible()
        # Empty because the row is refused, not because a filter hid it.
        expect(page.get_by_role("link", name=JOB)).to_have_count(0)

    def test_an_uninvited_contractor_cannot_reach_a_job_by_link(self, page: Page) -> None:
        # Learn a real job id as someone entitled to see it.
        sign_in_as(page, "Demo Client")
        page.goto("/jobs")
        hrefs = page.locator("a[href*='/jobs/']").evaluate_all(
            "els => els.map(e => e.getAttribute('href'))"
        )
        job_id = next(m.group(1) for m in (JOB_UUID.search(h or "") for h in hrefs) if m)
        sign_out(page)

        sign_in_as(page, "Demo Contractor, not invited")
        response = page.goto(f"/jobs/{job_id}")

        # 404 rather than 403: a 403 would confirm the job exists.
        assert response is not None
        assert response.status == 404, f"expected 404, got {response.status}"
        expect(page.get_by_text(re.compile("not available to you", re.I))).to_be_visible()

    def test_a_contractor_cannot_reach_the_staff_console(self, page: Page) -> None:
        sign_in_as(page, "Demo Contractor, not invited")
        response = page.goto("/staff/audit")

        assert response is not None
        assert response.status == 403, f"expected 403, got {response.status}"
