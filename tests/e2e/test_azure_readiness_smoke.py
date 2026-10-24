"""Selenium end-to-end smoke test for the Azure Readiness page
(frontend/src/components/AzureReadiness.jsx).

Inspected before writing this (CDPI Azure Integration Readiness spec,
Section 9.1): this repo's frontend is a bundler-free static app
(frontend/index.html, plain <script> tags, no React Router -- see
App.jsx's own doc comment) served locally via `python -m http.server`
(see .claude/launch.json's "frontend" entry, port 8732). There is no
existing Selenium/UI-test framework in this repo to reuse -- this is the
first one, using Python Selenium + pytest exactly as the spec's fallback
instructs. Login is mock-mode (window.CDPI_MOCK_API, config.js defaults
to mode:"mock") with a real admin demo account (mockApiClient.js's
MOCK_USERS: admin/Admin#12345) -- no real backend credentials are
involved.

This test starts its OWN server instance on a dedicated port (8733, not
the port a developer might already have 8732 bound to) so it never
collides with a long-running dev session, and tears it down afterward.

SAFETY: this test only ever clicks "Validate all" (a read-only check --
see AzureReadiness.jsx; the real backend's azure_readiness.py never
creates a resource from a validate call) and explicitly never clicks
"Plan"/"Approve"/"Execute" on the Ephemeral Tests tab -- the only action
in the whole app that can provision real Azure infrastructure. Mock mode
could not provision anything even if it tried (no real Azure SDK calls
exist in mockApiClient.js), but this test does not rely on that --
the test's human-readable steps simply never trigger that workflow at
all, matching the "must not automatically create Azure resources"
requirement literally, not just incidentally.

Run:
    pytest tests/e2e/test_azure_readiness_smoke.py -v

Requires Chrome (or Chromium) installed locally and Selenium's bundled
Selenium Manager to be able to fetch a matching chromedriver (needs
outbound network access the first time; cached after). If Chrome isn't
installed or chromedriver can't be resolved, the test reports BLOCKED
(via pytest.skip with a clear reason), not a false FAIL -- an
environmental prerequisite failure is not the same as the application
being broken.
"""

import http.server
import socket
import socketserver
import threading
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_FRONTEND_DIR = _REPO_ROOT / "frontend"
_TEST_PORT = 8733
_BASE_URL = f"http://127.0.0.1:{_TEST_PORT}"

# Mock-mode demo admin account -- see frontend/src/mockApiClient.js's
# MOCK_USERS. Not a real credential, not a real backend.
_ADMIN_USERNAME = "admin"
_ADMIN_PASSWORD = "Admin#12345"


def _port_is_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass  # keep pytest -v output readable -- no per-request access log


@pytest.fixture(scope="module")
def frontend_server():
    """Starts the real documented local-development server (`python -m
    http.server`, same command .claude/launch.json's "frontend" entry
    runs) against the real frontend/ directory, on a dedicated test port.
    Confirms the server actually answers before yielding -- 'the
    application has started' is verified, not assumed."""
    if _port_is_open(_TEST_PORT):
        # Another instance (e.g. a prior interrupted run) is already
        # serving this port -- reuse it rather than failing to bind.
        yield _BASE_URL
        return

    handler = lambda *args, **kwargs: _QuietHandler(*args, directory=str(_FRONTEND_DIR), **kwargs)  # noqa: E731
    httpd = socketserver.TCPServer(("127.0.0.1", _TEST_PORT), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _port_is_open(_TEST_PORT):
            break
        time.sleep(0.1)
    else:
        httpd.shutdown()
        pytest.fail("BLOCKED: local frontend server did not start within 10s.")

    yield _BASE_URL
    httpd.shutdown()
    thread.join(timeout=5)


@pytest.fixture
def driver():
    """Launches Chrome via Selenium Manager (bundled with Selenium 4.6+,
    auto-resolves a matching chromedriver). Reports BLOCKED (skip), not
    FAIL, if Chrome/chromedriver genuinely isn't available in this
    environment -- an environmental prerequisite, not an application bug."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError:
        pytest.skip("BLOCKED: selenium is not installed (pip install selenium).")

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1400,1000")

    try:
        d = webdriver.Chrome(options=options)
    except Exception as e:
        pytest.skip(f"BLOCKED: could not launch Chrome/chromedriver ({e}).")

    yield d
    d.quit()


# The statuses azure_readiness.py's STATUS_DEFINITIONS documents -- the
# ONLY values a status badge on this page should ever render (see
# AzureReadiness.jsx's READINESS_BADGE_CLASS, which only has entries for
# these). Asserting against this exact set is what would catch the
# application "misrepresenting missing authentication as a verified
# connection" (spec Section 9.5's explicit failure condition).
_DOCUMENTED_STATUSES = {
    "NOT_INTEGRATED", "NOT_CONFIGURED", "AUTHENTICATION_REQUIRED", "PERMISSION_DENIED",
    "CONNECTION_FAILED", "PARTIALLY_VERIFIED", "READY", "DEGRADED", "UNKNOWN",
}


def test_azure_readiness_page_loads_and_runs_safe_check(frontend_server, driver, tmp_path):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    wait = WebDriverWait(driver, 15)

    try:
        # Step 1-3: app starts, browser launches, app loads without a fatal error.
        driver.get(frontend_server)
        wait.until(EC.presence_of_element_located((By.XPATH, "//input[@type='text']")))
        assert "CDPI" in driver.title or driver.find_elements(By.XPATH, "//*[contains(text(),'CDPI')]"), \
            "App did not render its own branding -- looks like a fatal load error, not the login screen."

        # Log in as the mock-mode admin demo account.
        username_field = driver.find_element(By.XPATH, "//input[@type='text']")
        password_field = driver.find_element(By.XPATH, "//input[@type='password']")
        username_field.send_keys(_ADMIN_USERNAME)
        password_field.send_keys(_ADMIN_PASSWORD)
        driver.find_element(By.XPATH, "//button[@type='submit']").click()

        # Step 4: navigate to Azure Readiness via the app's own nav (no URL routing exists in this app -- see App.jsx's doc comment).
        # XPath note: contains(., 'x') (string-value of the whole node),
        # NOT contains(text(), 'x') -- the nav button's children are
        # [<Icon/>, " ", label] (see App.jsx), so text() is a NODE-SET of
        # two text nodes and XPath 1.0 silently compares contains()
        # against only the FIRST one (here, a lone space) when given a
        # node-set, never matching the label at all.
        nav_item = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Azure Readiness')]")))
        # The sidebar nav now has enough items to overflow a short viewport
        # -- scroll the target into view first, or Chrome correctly refuses
        # the click as "element not interactable" (confirmed hitting this
        # for real while writing this test, not a hypothetical).
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", nav_item)
        nav_item.click()

        page = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='azure-readiness-page']")))
        assert page.is_displayed()

        # Step 5: verify readiness information -- either real service rows
        # with documented statuses, or a meaningful empty state (this
        # repo always has at least the registered-by-default integrations,
        # so real rows are the expected case here).
        result_container = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='readiness-check-result']")))
        status_badges = wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, "[data-testid='azure-readiness-status']") or False)
        assert status_badges, "Expected at least one service readiness row (or an explicit empty state) -- found neither."
        observed_statuses = {el.text.strip() for el in status_badges}
        unexpected = observed_statuses - _DOCUMENTED_STATUSES
        assert not unexpected, f"Page rendered undocumented status value(s): {unexpected} -- not one of {_DOCUMENTED_STATUSES}."

        # Step 6: run the read-only readiness check, if the control exists
        # (it requires azure.manage -- the admin demo account has it).
        check_buttons = driver.find_elements(By.CSS_SELECTOR, "[data-testid='run-readiness-check']")
        if check_buttons:
            before_text = result_container.text
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", check_buttons[0])
            check_buttons[0].click()
            # Explicit wait for the result to actually change (a real
            # "Last validated" timestamp appears), not an arbitrary sleep.
            wait.until(lambda d: d.find_element(By.CSS_SELECTOR, "[data-testid='readiness-check-result']").text != before_text)

        # Step 7: verify a meaningful result -- AUTHENTICATION_REQUIRED/
        # NOT_CONFIGURED is a legitimate, correct outcome in an environment
        # with no secrets configured (this one): the assertion is that the
        # vocabulary stayed within the documented set post-check, re-read
        # fresh rather than reusing the pre-check `status_badges` elements.
        status_badges_after = driver.find_elements(By.CSS_SELECTOR, "[data-testid='azure-readiness-status']")
        observed_after = {el.text.strip() for el in status_badges_after}
        assert observed_after - _DOCUMENTED_STATUSES == set(), \
            f"After running the check, page rendered undocumented status value(s): {observed_after - _DOCUMENTED_STATUSES}."
        assert "READY" not in observed_after or True  # READY is a legitimate value IF genuinely earned; not asserted against here (no secrets are configured in this environment, so it should not appear -- checked explicitly below).
        assert "READY" not in observed_after, (
            "Page reported READY for at least one service with no Azure secrets configured anywhere in this "
            "environment -- this would be exactly the fabricated-success failure mode the spec prohibits."
        )

        # Step 8: confirm the read-only check did not navigate away, throw,
        # or otherwise indicate a deployment started -- and that the
        # Ephemeral Tests tab (the only resource-creating workflow) was
        # never touched by this test at all.
        ephemeral_tab_buttons = driver.find_elements(By.XPATH, "//button[contains(.,'Ephemeral Tests')]")
        assert ephemeral_tab_buttons, "Ephemeral Tests tab control should exist (not clicked -- this test never provisions anything)."

    except Exception:
        screenshot_path = tmp_path / "azure_readiness_smoke_failure.png"
        try:
            driver.save_screenshot(str(screenshot_path))
            print(f"Screenshot saved to {screenshot_path}")
        except Exception:
            pass
        raise
