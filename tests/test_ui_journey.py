"""The full browser journey of the accepted-product contract, against a real
server: launch → authenticate from the launcher's preview bootstrap record →
create and save the product's primary outcome (a triage decision) → open the
product guide → restart in a fresh browser context → authenticate again →
recover the same saved outcome.

The outcome this product produces is a *decision*: the queue ranks spans, and
the only thing an operator makes that outlives the session is the decision row
plus its ledger event. That is what the journey saves and recovers.

Skips — never passes silently — when the browser, the built SPA, ffmpeg or the
test database are unavailable, exactly as ``test_e2e_playwright`` does.
"""
from __future__ import annotations

import json

import pytest

from tests.test_e2e_playwright import live_server  # noqa: F401  (the fixture)

BOOTSTRAP = {
    "username": "dana",
    "role": "reviewer",
    "session_token": "preview-session-token",
}
NOTE = "motion held steady; dog and person both visible"


def _seed_bootstrap(context) -> None:
    context.add_init_script(
        "window.localStorage.setItem('sneferu.preview.bootstrap.v1',"
        f" {json.dumps(json.dumps(BOOTSTRAP))});"
    )


def test_preview_bootstrap_journey(live_server, tmp_path):  # noqa: F811
    from playwright.sync_api import sync_playwright

    port = live_server["port"]
    base = f"http://127.0.0.1:{port}"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()

        # 1 + 2. launch and authenticate from the launcher's bootstrap record
        context = browser.new_context()
        _seed_bootstrap(context)
        page = context.new_page()
        page.goto(base)
        page.wait_for_selector('[data-testid="nav-ingest"]')
        assert "dana" in page.locator('[data-testid="service-state"]').inner_text(), (
            "the preview bootstrap identity did not reach the client"
        )
        assert page.locator('[data-testid="token-banner"]').count() == 0, (
            "the bootstrap session must not raise the token banner"
        )

        # 3. create and save the product's primary outcome
        page.click('[data-testid="demo-seed"]')
        page.wait_for_selector('[data-testid^="source-row-"]', timeout=60_000)
        page.wait_for_function(
            "() => { const el = document.querySelector('[data-testid^=\"source-state-\"]');"
            " return !!el && el.textContent.trim() === 'ready'; }",
            timeout=300_000,
        )
        page.click('[data-testid="nav-triage"]')
        page.wait_for_selector('[data-testid="queue-table"]')
        page.wait_for_function(
            "() => document.body.innerText.includes('stationary_animal_with_person')",
            timeout=60_000,
        )
        page.evaluate(
            """
            () => {
              const rows = Array.from(document.querySelectorAll('[data-testid^="queue-entry-"]'));
              const target = rows.find((row) => row.innerText.includes('stationary_animal_with_person'))
                || rows.find((row) => row.innerText.includes('candidate'));
              if (target) target.dispatchEvent(new MouseEvent('click', {bubbles: true}));
            }
            """
        )
        page.wait_for_selector('[data-testid="candidate-detail"]')
        page.keyboard.press("v")  # open the source-resolution pane first
        page.wait_for_selector('[data-testid="source-pane"]')
        page.keyboard.press("f")  # opens the note field and focuses it
        page.fill('[data-testid="note-input"]', NOTE)
        page.keyboard.press("Enter")  # records the flag with the note
        page.wait_for_function(
            "() => { const el = document.querySelector('[data-testid=\"triage-message\"]');"
            " return !!el && el.textContent.includes('flag recorded'); }",
            timeout=30_000,
        )

        # 4. the product guide
        page.click('[data-testid="nav-about"]')
        page.wait_for_selector('[data-testid="integrity-statement"]')
        assert page.locator('[data-testid="integrity-statement"]').inner_text().strip()
        assert page.locator('[data-testid="review-medium"]').inner_text().strip()
        assert page.locator('[data-testid="trust-boundary"]').inner_text().strip()

        # 5. restart in a fresh browser context and authenticate again
        context.close()
        context = browser.new_context()
        _seed_bootstrap(context)
        page = context.new_page()
        page.goto(f"{base}/#/triage")
        page.wait_for_selector('[data-testid="queue-table"]')
        assert "dana" in page.locator('[data-testid="service-state"]').inner_text()
        page.wait_for_function(
            "() => document.body.innerText.includes('flagged')", timeout=60_000
        )

        # 6. recover the same saved outcome
        page.evaluate(
            """
            () => {
              const rows = Array.from(document.querySelectorAll('[data-testid^="queue-entry-"]'));
              const target = rows.find((row) => row.innerText.includes('flagged'));
              if (target) target.dispatchEvent(new MouseEvent('click', {bubbles: true}));
            }
            """
        )
        page.wait_for_function(
            "() => { const el = document.querySelector('[data-testid=\"decision-history\"]');"
            f" return !!el && el.textContent.includes({json.dumps(NOTE)}); }}",
            timeout=60_000,
        )
        browser.close()
