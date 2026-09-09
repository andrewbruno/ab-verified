"""End-to-end fixtures: a real server, a real browser, a throwaway database.

These tests drive the application the way a person does, through a browser,
over HTTP. That is the whole point of them, so nothing here reaches into the
application's internals: no TestClient, no direct database writes, no imports
from `app`. If a journey can be completed here it can be completed by a user.

The unit suite under `tests/` stays the place for policy and state machine
coverage. This suite exists to catch what that one structurally cannot: a
template that does not render, a form that posts to the wrong route, a button
the layout has covered up.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

from .server import free_port, running_app

ARTIFACTS = Path(__file__).parent / "artifacts"

# Playwright's default assertion timeout is 5 seconds, which is generous on a
# developer's laptop and tight on a shared CI runner starting a cold server.
# One run in twelve here timed out on an otherwise passing journey.
expect.set_options(timeout=15_000)


@pytest.fixture(scope="session")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    db = tmp_path_factory.mktemp("e2e-db") / "e2e.sqlite3"
    with running_app(db, free_port()) as url:
        yield url


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def page(browser: Browser, base_url: str, request: pytest.FixtureRequest) -> Iterator[Page]:
    """A fresh context per test, so no session leaks between journeys.

    A failing test leaves a trace behind. `playwright show-trace` replays it
    frame by frame, which is the difference between a CI failure you can read
    and one you have to reproduce locally first.
    """
    context = browser.new_context(base_url=base_url, viewport={"width": 1440, "height": 900})
    context.tracing.start(screenshots=True, snapshots=True)
    page = context.new_page()
    try:
        yield page
    finally:
        failed = getattr(request.node, "e2e_failed", False)
        if failed:
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            context.tracing.stop(path=str(ARTIFACTS / f"{request.node.name}.zip"))
            page.screenshot(path=str(ARTIFACTS / f"{request.node.name}.png"), full_page=True)
        else:
            context.tracing.stop()
        context.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        item.e2e_failed = True
