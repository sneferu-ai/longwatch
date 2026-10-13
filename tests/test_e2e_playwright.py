"""E2E — the browser smoke plan of §19, driven against a real server.

Every step is reachable in a fresh environment given only ``{port}``, the
managed Postgres connection and the declared ``env_defaults``:

1. the Ingest page, the persistent model-limits disclosure, the demo-seed button;
2. demo-seed → a source badged "demo · synthetic" reaching ``ready`` (≤ 300 s);
3. Triage: the p85-sentence candidate and the synthetic flagship candidate;
4. ``v`` opens the source-resolution pane and streams the original;
5. ``a`` accepts (reviewer + source-viewed), ``f`` flags with a note that
   persists across a reload;
6. Export with default settings yields the ZIP with all twelve bundle entries;
7. ``/healthz`` returns 200.

The test skips — never passes silently — when the browser, the built SPA or
ffmpeg are unavailable.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC_INDEX = REPO / "longwatch" / "static" / "index.html"

BUNDLE_ENTRIES = {
    "manifest.json", "manifest.canonical", "manifest.sig", "public.pem",
    "candidates.csv", "ledger.jsonl", "checksums.sha256", "verify_bundle.py",
    "ed25519_verifier.py", "VERIFY.md", "README.txt",
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_healthy(port: int, timeout_s: float = 180.0) -> bool:
    deadline = time.monotonic() + timeout_s
    url = f"http://127.0.0.1:{port}/healthz"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - the server is still booting
            time.sleep(1.0)
    return False


@pytest.fixture(scope="module")
def live_server(dsn, tmp_path_factory):
    if not STATIC_INDEX.is_file():
        pytest.skip("the SPA is not built (longwatch/static/index.html is absent)")
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("playwright is not installed in this environment")
    if not (Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/nonexistent"))).is_dir():
        pytest.skip("no prepared browser is available (PLAYWRIGHT_BROWSERS_PATH unset)")
    import shutil

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not available")

    import psycopg

    schema = f"lw_e2e_{os.urandom(3).hex()}"
    admin = psycopg.connect(dsn, autocommit=True)
    try:
        with admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        admin.close()

    data_dir = tmp_path_factory.mktemp("e2e-data")
    port = _free_port()
    model_fixture = REPO / "tests" / "fixtures" / "model_fixture.onnx"
    env = dict(os.environ)
    env.update({
        "DATABASE_URL": dsn + ("&" if "?" in dsn else "?") + f"options=-csearch_path%3D{schema}",
        "LONGWATCH_DATA_DIR": str(data_dir),
        "LONGWATCH_OFFLINE": "0",
        "PORT": str(port),
        "LONGWATCH_REVIEWER_NAME": "operator",
        # the digest-pinned YOLOv8n is fetched into the *runtime image* by the
        # Dockerfile's models stage; the offline construction environment loads
        # the in-repo contract fixture through the same three-variable swap path.
        "LONGWATCH_MODEL_PATH": str(model_fixture),
        "LONGWATCH_CLASS_MAP": str(REPO / "models" / "coco80.json"),
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "longwatch", "serve", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(REPO), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        if not _wait_healthy(port):
            process.terminate()
            output = ""
            try:
                output = process.stdout.read() if process.stdout else ""
            except Exception:  # noqa: BLE001
                pass
            detail = ""
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as response:
                    detail = response.read().decode("utf-8")
            except Exception as exc:  # noqa: BLE001
                try:
                    import urllib.error

                    detail = exc.read().decode("utf-8") if isinstance(exc, urllib.error.HTTPError) else str(exc)
                except Exception:  # noqa: BLE001
                    detail = str(exc)
            pytest.fail(f"server never became healthy; healthz={detail}\noutput tail:\n{output[-3000:]}")
        yield {"port": port, "data_dir": data_dir, "schema": schema}
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()
        admin = psycopg.connect(dsn, autocommit=True)
        try:
            with admin.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            admin.close()


def test_browser_smoke_plan(live_server, tmp_path):
    from playwright.sync_api import sync_playwright

    port = live_server["port"]
    base = f"http://127.0.0.1:{port}"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        try:
            # 1. the Ingest page, the disclosure, the demo-seed button
            page.goto(base)
            page.wait_for_selector('[data-testid="nav-ingest"]')
            assert page.is_visible('[data-testid="model-limits-disclosure"]')
            page.wait_for_selector('[data-testid="demo-seed"]')

            # 2. demo-seed → a badged source reaching ready
            page.click('[data-testid="demo-seed"]')
            page.wait_for_selector('[data-testid^="source-row-"]', timeout=60_000)
            row = page.locator('[data-testid^="source-row-"]').first
            assert "demo · synthetic" in row.inner_text()
            state = page.locator('[data-testid^="source-state-"]').first
            page.wait_for_function(
                "() => { const el = document.querySelector('[data-testid^=\"source-state-\"]');"
                " return !!el && el.textContent.trim() === 'ready'; }",
                timeout=300_000)

            # 3. Triage: the p85 sentence and the synthetic flagship
            page.click('[data-testid="nav-triage"]')
            page.wait_for_selector('[data-testid="queue-table"]')
            page.wait_for_function(
                "() => document.body.innerText.includes(\"this recording's p85\")",
                timeout=60_000)
            page.wait_for_function(
                "() => document.body.innerText.includes('stationary_animal_with_person')",
                timeout=60_000)
            assert page.locator('[data-testid="candidate-explanation"]').count() >= 1

            # select the flagship (synthetic) candidate
            page.evaluate(
                """
                () => {
                  const rows = Array.from(document.querySelectorAll('[data-testid^="queue-entry-"]'));
                  const target = rows.find((row) => row.innerText.includes('stationary_animal_with_person'))
                    || rows.find((row) => row.innerText.includes("p85"));
                  if (target) target.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                }
                """
            )
            page.wait_for_selector('[data-testid="candidate-detail"]')
            assert page.locator('[data-testid="candidate-sentence"]').inner_text().strip()
            assert page.locator('[data-testid="candidate-evidence"]').inner_text().strip()

            # 4. v opens the source-resolution pane and streams the original
            page.keyboard.press("v")
            page.wait_for_selector('[data-testid="source-pane"]')
            assert page.locator('[data-testid="src-viewed-indicator"]').inner_text().endswith("true")

            # 5. a accepts; f flags with a note that persists across a reload
            page.keyboard.press("a")
            try:
                page.wait_for_function(
                    "() => { const el = document.querySelector('[data-testid=\"triage-message\"]');"
                    " return !!el && el.textContent.includes('accept recorded'); }",
                    timeout=30_000)
            except Exception:  # noqa: BLE001 - surface what the page actually said
                message = ""
                if page.locator('[data-testid="triage-message"]').count():
                    message = page.locator('[data-testid="triage-message"]').inner_text()
                pytest.fail(f"accept was not recorded; triage-message={message!r};"
                            f" body={page.inner_text('body')[:1500]!r}")
            # the decision history refreshes after the POST; wait for it rather
            # than racing the refetch
            page.wait_for_function(
                "() => { const el = document.querySelector('[data-testid=\"decision-history\"]');"
                " return !!el && el.textContent.includes('operator')"
                " && el.textContent.includes('src_viewed true'); }",
                timeout=30_000)

            page.evaluate(
                """
                () => {
                  const rows = Array.from(document.querySelectorAll('[data-testid^="queue-entry-"]'));
                  const target = rows.find((row) => !row.innerText.includes('accepted'));
                  if (target) target.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                }
                """
            )
            page.wait_for_selector('[data-testid="candidate-detail"]')
            page.keyboard.press("f")   # opens the note field and focuses it
            page.fill('[data-testid="note-input"]', "motion held steady; dog and person both visible")
            page.keyboard.press("Enter")   # records the flag with the note
            page.wait_for_function(
                "() => { const el = document.querySelector('[data-testid=\"triage-message\"]');"
                " return !!el && el.textContent.includes('flag recorded'); }",
                timeout=30_000)
            page.reload()
            page.click('[data-testid="nav-triage"]')
            page.wait_for_selector('[data-testid="queue-table"]')
            page.wait_for_function(
                "() => document.body.innerText.includes('flagged')", timeout=60_000)
            page.evaluate(
                """
                () => {
                  const rows = Array.from(document.querySelectorAll('[data-testid^="queue-entry-"]'));
                  const target = rows.find((row) => row.innerText.includes('flagged'));
                  if (target) target.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                }
                """
            )
            # the note survived the reload because it lives in the decision row
            page.wait_for_function(
                "() => { const el = document.querySelector('[data-testid=\"decision-history\"]');"
                " return !!el && el.textContent.includes('dog and person both visible'); }",
                timeout=60_000)

            # 6. export with default settings → the ZIP with every bundle entry
            page.click('[data-testid="nav-exports"]')
            page.wait_for_selector('[data-testid="export-create"]')
            page.click('[data-testid="export-create"]')
            page.wait_for_selector('[data-testid^="export-download-"]', timeout=300_000)
            link = page.locator('[data-testid^="export-download-"]').first
            href = link.get_attribute("href")
            assert href
            target = tmp_path / "bundle.zip"
            with urllib.request.urlopen(base + href, timeout=120) as response:
                target.write_bytes(response.read())
            with zipfile.ZipFile(target) as archive:
                raw_names = archive.namelist()
                names = {Path(name).name for name in raw_names}
                assert BUNDLE_ENTRIES <= names, BUNDLE_ENTRIES - names
                assert any("/clips/" in name or name.startswith("clips/")
                           for name in raw_names), raw_names
                manifest = json.loads(archive.read(
                    next(name for name in raw_names if name.endswith("manifest.json"))))
            assert manifest["candidates"], "the accepted and flagged decisions must ship"
            assert {entry["decision_state"] for entry in manifest["candidates"]} <= {
                "accepted", "flagged"}

            # 7. /healthz is 200
            with urllib.request.urlopen(base + "/healthz", timeout=10) as response:
                assert response.status == 200
        finally:
            browser.close()
