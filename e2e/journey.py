"""The curated lifecycle as a sequence of browser steps.

Written once and used twice: the end-to-end test asserts it, and
scripts/record_demo.py films it. That is deliberate. A walkthrough video that
drifts away from what the application actually does is worse than no video,
and the only reliable way to stop it drifting is for the recording to be a
passing test.

Each step yields its caption before acting, so the recorder can title the
video and the test can name the step it failed on.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

from playwright.sync_api import Page, expect

# The seeded fixture is deterministic, so the journey can name its subject.
JOB = "Annual penetration test of the patient portal"

# The organisation behind the "Demo Contractor, invited" persona. Staff invite
# it by name so the same persona can carry the next step; the ranked candidate
# list for this job is all security specialists, so this contractor is reached
# through "invite someone else".
CONTRACTOR = "Meridian Cloud Works"

CLOSES_AT = "2026-09-30T17:00"
EXPIRES_AT = "2026-09-25T17:00"

Beat = Callable[[str], None]


def sign_in_as(page: Page, persona: str) -> None:
    """Sign in through the demo persona cards, the way a visitor does."""
    page.goto("/demo")
    card = page.locator("form[action='/demo/login']").filter(has_text=persona)
    card.get_by_role("button", name="Sign in").click()
    page.wait_for_load_state()


def sign_out(page: Page) -> None:
    page.get_by_role("button", name="Sign out").click()
    page.wait_for_load_state()


def lifecycle(page: Page, beat: Beat = lambda caption: None) -> Iterator[str]:
    """Carry one job from pending approval to a confirmed award.

    `beat` is called with a caption before each stage, which the recorder uses
    to hold the frame long enough to read.
    """
    # --- Staff approve the job and set a closing date --------------------
    beat("Staff review the job a client submitted")
    yield "staff approve the job"
    sign_in_as(page, "Demo Staff")
    page.goto("/staff/jobs/pending")
    expect(page.get_by_role("heading", name="Jobs pending approval")).to_be_visible()

    page.get_by_role("link", name=JOB).click()
    page.get_by_label(re.compile("approve", re.I)).check()
    page.locator("[name=bids_close_at]").fill(CLOSES_AT)
    page.get_by_role("button", name="Record decision").click()

    # Approval hands straight to the invitation step, which is the point: an
    # approved job nobody has been invited to is live to nobody.
    expect(page).to_have_url(re.compile(r"/candidates$"))
    job_id = re.search(r"/staff/jobs/([0-9a-f-]+)/candidates", page.url).group(1)

    # --- Staff invite the contractor -------------------------------------
    beat("Staff choose who is allowed to bid. Nobody else can see the job")
    yield "staff invite a contractor"
    # The option label carries a match score the fixture is free to change, so
    # find the option by business name and select it by value.
    option = page.locator("[name=other_org_id] option").filter(has_text=CONTRACTOR).first
    page.locator("[name=other_org_id]").select_option(value=option.get_attribute("value"))
    page.locator("[name=expires_at]").fill(EXPIRES_AT)
    page.get_by_role("button", name=re.compile("Invite the contractors")).click()
    page.wait_for_load_state()

    page.goto("/staff/jobs/pending")
    expect(page.get_by_role("link", name=JOB)).to_have_count(0)
    sign_out(page)

    # --- The invited contractor accepts, then bids -----------------------
    beat("The invited contractor accepts and lodges a bid")
    yield "the contractor accepts and bids"
    sign_in_as(page, "Demo Contractor, invited")
    page.goto("/invitations")
    invitation = page.locator("article,section,li").filter(has_text=JOB).last
    invitation.get_by_role("link", name=re.compile("Review invitation")).click()
    page.get_by_role("button", name=re.compile("^Accept")).click()
    page.wait_for_load_state()

    page.goto(f"/jobs/{job_id}/bid")
    page.locator("[name=amount]").fill("15000")
    page.locator("[name=proposed_start]").fill("2026-10-12")
    page.locator("[name=estimated_days]").fill("30")
    page.locator("[name=approach]").fill(
        "Grey-box test of the portal and its API over three weeks, with a "
        "retest of every finding once your team has remediated it."
    )
    page.get_by_role("button", name="Submit bid").click()
    page.wait_for_load_state()
    expect(page.get_by_text(re.compile("submitted", re.I)).first).to_be_visible()
    sign_out(page)

    # --- Staff screen that bid and release it to the client --------------
    beat("Staff screen the bids before the client ever sees them")
    yield "staff release the bids"
    sign_in_as(page, "Demo Staff")
    page.goto("/staff/bids")
    job_section = page.locator("section,article").filter(has_text=JOB).last
    job_section.get_by_role("button", name=re.compile(r"Release all \d+ bid")).click()
    page.wait_for_load_state()
    sign_out(page)

    # --- The client compares the released bids and picks one -------------
    beat("The client compares the released bids and selects one")
    yield "the client selects a winner"
    sign_in_as(page, "Demo Client")
    page.goto(f"/jobs/{job_id}/bids")
    expect(page.get_by_text("Compare released bids")).to_be_visible()

    # The contact embargo: the comparison carries no contact route to any
    # bidder, because details are released only on award (FR-501).
    expect(page.locator("a[href^='mailto:'], a[href^='tel:']")).to_have_count(0)

    page.get_by_role("link", name=re.compile("Select this bid")).first.click()
    page.get_by_role("button", name=re.compile("^Select ")).click()
    page.wait_for_load_state()
    sign_out(page)

    # --- Staff confirm the award, which releases contact details ---------
    beat("Staff confirm the award, which finally releases contact details")
    yield "staff confirm the award"
    sign_in_as(page, "Demo Staff")
    page.goto("/staff/awards")
    award = page.locator("section,article").filter(has_text=JOB).last
    award.get_by_role("button", name=re.compile("Confirm and release contact details")).click()
    page.wait_for_load_state()

    # --- The winning contractor sees they have won -----------------------
    beat("The contractor has won the work")
    yield "the contractor sees they have won"
    sign_out(page)
    sign_in_as(page, "Demo Contractor, invited")
    page.goto("/bids")
    expect(page.get_by_text(re.compile("won|awarded", re.I)).first).to_be_visible()
