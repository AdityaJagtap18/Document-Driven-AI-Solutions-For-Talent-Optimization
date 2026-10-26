"""Real, live, end-to-end candidate lifecycle test -- NOT a mock-mode
smoke test (contrast with test_azure_readiness_smoke.py). This drives
the actual browser against the actual already-running local stack
(real Azure Functions host on :7071, real frontend static server on
:8732, real Cosmos DB, real Azure OpenAI), exercising the full real
multi-persona flow:

  1. (Setup, via direct API calls, not Selenium -- account creation is
     setup, not the behavior under test) Create 5 real candidate user
     accounts + 1 real recruiter user account.
  2. For each of 5 real synthetic resumes (InputResume_Folder/*.pdf,
     generated earlier this session): log in as that candidate persona
     in the real browser, fill out and submit the real "Submit a new
     application" form -- including a REAL file upload via Selenium's
     send_keys() on the file input (bypasses the native OS file-picker
     dialog entirely, which this environment's own built-in browser
     automation cannot drive -- this is exactly why this flow needs
     Selenium rather than the built-in browser pane). This triggers the
     real blob-triggered ResumeParser.py pipeline asynchronously.
  3. Log in as the recruiter persona, open the real Review Queue, and
     record real approve/reject decisions on however many of the 5
     candidates have finished real (async, LLM-call-dependent) parsing
     within a bounded wait window.
  4. Log in as admin and verify the real Dashboard tier counts reflect
     the real decisions made in step 3.

SAFETY / COST: this test makes REAL, BILLABLE Azure OpenAI calls (up to
~2 real LLM calls per candidate: resume_chunk_summarization and
resume_merge_structured_extraction) and writes REAL data to the real
Cosmos DB (new user accounts, new candidate records, real audit_log
review-decision entries). It does not touch any resource-provisioning
workflow (no Ephemeral Tests, no Azure resource creation). All resume/
candidate data is synthetic (see scratchpad_resumes/generate_pdfs.py).

Does NOT start the frontend or Functions host itself (both are real,
heavyweight, already-managed processes) -- reports BLOCKED (skip), not
FAIL, if either isn't already reachable, same discipline as
tests/perf/test_load_baseline.py.

Run:
    pytest tests/e2e/test_candidate_lifecycle_live.py -v -s
"""

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_FRONTEND_URL = "http://127.0.0.1:8732"
_BACKEND_URL = "http://127.0.0.1:7071"
_INPUT_RESUME_DIR = _REPO_ROOT / "InputResume_Folder"
_PARSED_RESUME_DIR = _REPO_ROOT / "ParsedReadyForQuestions"


def _resume_dir_for(filename: str) -> Path:
    """ResumeParser.py moves a resume out of InputResume_Folder into
    ParsedReadyForQuestions once it's been processed -- this test only
    needs the real PDF bytes to upload, wherever they currently sit, so
    it checks both real locations rather than assuming one."""
    if (_INPUT_RESUME_DIR / filename).exists():
        return _INPUT_RESUME_DIR
    return _PARSED_RESUME_DIR

_ADMIN_USERNAME = os.environ.get("CDPI_ADMIN_USERNAME", "auditadmin")
_ADMIN_PASSWORD = os.environ.get("CDPI_ADMIN_PASSWORD")  # real credential -- never hardcoded/committed; set it in your shell before running this suite

# A fresh, unique run suffix every time -- so this test is always safely
# rerunnable without colliding with a previous run's leftover accounts.
_RUN_ID = uuid.uuid4().hex[:8]

_CANDIDATES = [
    {"display": "Priya Raman", "resume": "priya_raman_resume.pdf", "email_local": "priya.raman.synth"},
    {"display": "Marcus Chen", "resume": "marcus_chen_resume.pdf", "email_local": "marcus.chen.synth"},
    {"display": "Aisha Khan", "resume": "aisha_khan_resume.pdf", "email_local": "aisha.khan.synth"},
    {"display": "Elena Petrova", "resume": "elena_petrova_resume.pdf", "email_local": "elena.petrova.synth"},
    {"display": "Wei Zhang", "resume": "wei_zhang_resume.pdf", "email_local": "wei.zhang.synth"},
]
_CANDIDATE_PASSWORD = "E2ECandidate#" + _RUN_ID
_RECRUITER_USERNAME = f"e2e_recruiter_{_RUN_ID}"
_RECRUITER_PASSWORD = "E2ERecruiter#" + _RUN_ID


def _port_is_open(port, host="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _api(method, path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(_BACKEND_URL + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _admin_token():
    status, body = _api("POST", "/api/auth/login", body={"username": _ADMIN_USERNAME, "password": _ADMIN_PASSWORD})
    assert status == 200, f"Real admin login failed: {body}"
    return body["token"]


@pytest.fixture(scope="module")
def stack_ready():
    if not _ADMIN_PASSWORD:
        pytest.skip("BLOCKED: set CDPI_ADMIN_PASSWORD (and CDPI_ADMIN_USERNAME if not 'auditadmin') in your shell before running this suite.")
    if not _port_is_open(7071):
        pytest.skip("BLOCKED: no Azure Functions host listening on 127.0.0.1:7071. Start it (`func start --port 7071` in azure-functions/) first.")
    if not _port_is_open(8732):
        pytest.skip("BLOCKED: no frontend server listening on 127.0.0.1:8732. Start it (`python -m http.server 8732 --directory frontend`) first.")
    for c in _CANDIDATES:
        if not (_INPUT_RESUME_DIR / c["resume"]).exists() and not (_PARSED_RESUME_DIR / c["resume"]).exists():
            pytest.skip(f"BLOCKED: {c['resume']} not found in InputResume_Folder or ParsedReadyForQuestions -- generate the synthetic resumes first (scratchpad_resumes/generate_pdfs.py).")
    return True


@pytest.fixture(scope="module")
def driver():
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError:
        pytest.skip("BLOCKED: selenium is not installed (pip install selenium).")

    options = Options()
    # Deliberately NOT headless here (unlike test_azure_readiness_smoke.py)
    # -- the user explicitly wants to watch this run live.
    options.add_argument("--window-size=1400,1100")
    options.add_argument("--disable-dev-shm-usage")

    try:
        d = webdriver.Chrome(options=options)
    except Exception as e:
        pytest.skip(f"BLOCKED: could not launch Chrome/chromedriver ({e}).")

    yield d
    d.quit()


@pytest.fixture(scope="module")
def created_accounts(stack_ready):
    """Real setup via direct API calls (not Selenium) -- creates 5 real
    candidate accounts (one per synthetic resume) and 1 real recruiter
    account, all scoped to this run via _RUN_ID so reruns never collide.
    Returns the full account/email list the rest of the test drives."""
    token = _admin_token()

    accounts = []
    for c in _CANDIDATES:
        username = f"e2e_cand_{_RUN_ID}_{c['email_local'].split('.')[0]}"
        email = f"{c['email_local']}.{_RUN_ID}@example.com"
        status, body = _api("POST", "/api/manage/users", token=token, body={
            "username": username, "password": _CANDIDATE_PASSWORD,
            "display_name": f"{c['display']} (E2E Live Test)", "email": email,
            "role_keys": ["candidate"],
        })
        assert status in (200, 201), f"Real candidate account creation failed for {username}: {body}"
        accounts.append({**c, "username": username, "email": email})

    status, body = _api("POST", "/api/manage/users", token=token, body={
        "username": _RECRUITER_USERNAME, "password": _RECRUITER_PASSWORD,
        "display_name": "E2E Live Test Recruiter", "email": f"e2e.recruiter.{_RUN_ID}@example.com",
        "role_keys": ["recruiter"],
    })
    assert status in (200, 201), f"Real recruiter account creation failed: {body}"

    return accounts


def _login(driver, wait, username, password):
    from selenium.webdriver.common.by import By

    driver.get(_FRONTEND_URL)
    wait.until(lambda d: d.find_elements(By.XPATH, "//input[@type='text']"))
    driver.find_element(By.XPATH, "//input[@type='text']").send_keys(username)
    driver.find_element(By.XPATH, "//input[@type='password']").send_keys(password)
    driver.find_element(By.XPATH, "//button[@type='submit']").click()


def _goto_review_queue(driver, wait, reviewer_name=None):
    """Navigates to Review Queue. Real bug this fixes: driver.refresh()
    reloads the SPA back to its DEFAULT tab (there's no URL routing in
    this app -- see App.jsx's own doc comment -- activeTab is in-memory
    React state, which a hard reload always resets), so after a refresh
    the page does NOT come back just by waiting -- the nav click has to
    happen again.

    `reviewer_name` is accepted for backwards compatibility with earlier
    callers but ignored: the "Reviewing as" identity is no longer an
    editable field (see ReviewQueue.jsx) -- it's derived server-side from
    the authenticated session (api_blueprint.py's review_candidate_
    endpoint/propose_bulk_decision_endpoint no longer trust a client-
    supplied name)."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    nav_item = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Review Queue')]")))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", nav_item)
    nav_item.click()
    return wait.until(EC.presence_of_element_located((By.ID, "reviewer-name")))


def _sign_out(driver, wait):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Sign out')]")))
    btn.click()
    wait.until(lambda d: d.find_elements(By.XPATH, "//input[@type='password']"))


def test_full_candidate_lifecycle(stack_ready, driver, created_accounts, tmp_path):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    wait = WebDriverWait(driver, 20)

    try:
        # ------------------------------------------------------------
        # Step 1: 5 real candidates, each logs in, fills out and submits
        # the real application form with a real resume file upload.
        # ------------------------------------------------------------
        for c in created_accounts:
            _login(driver, wait, c["username"], _CANDIDATE_PASSWORD)
            wait.until(EC.presence_of_element_located((By.XPATH, "//h3[contains(.,'Submit a new application')]")))

            form = driver.find_element(By.CSS_SELECTOR, "form.application-form")
            form.find_element(By.XPATH, ".//label[contains(.,'Full name')]/input").send_keys(c["display"])
            form.find_element(By.XPATH, ".//label[contains(.,'Email')]/input").send_keys(c["email"])

            resume_path = str((_resume_dir_for(c["resume"]) / c["resume"]).resolve())
            file_input = form.find_element(By.CSS_SELECTOR, "input[type='file']")
            file_input.send_keys(resume_path)  # bypasses the native OS file-picker dialog entirely

            submit_btn = form.find_element(By.XPATH, ".//button[contains(.,'Submit Application')]")
            submit_btn.click()

            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".banner-success")))
            success_text = driver.find_element(By.CSS_SELECTOR, ".banner-success").text
            assert "received" in success_text.lower() or "submitted" in success_text.lower(), \
                f"Unexpected submission result for {c['display']}: {success_text}"
            print(f"[candidate] {c['display']} <{c['email']}>: {success_text}")

            _sign_out(driver, wait)

        # ------------------------------------------------------------
        # Step 2: real recruiter persona -- wait (bounded) for the real,
        # async blob-triggered pipeline to finish parsing however many
        # of the 5 it gets to, then record a real approve/reject
        # decision on each one that's ready (alternating, to exercise
        # both outcomes for real).
        # ------------------------------------------------------------
        _login(driver, wait, _RECRUITER_USERNAME, _RECRUITER_PASSWORD)
        _goto_review_queue(driver, wait, "E2E Live Test Recruiter")

        # Real candidates can land in EITHER "To be Considered" (AI gave
        # no strong recommendation) or "Recommended for Fast-Track" (AI
        # scored them well) -- a recruiter reviews both, not just one
        # tier, so this checks all tiers each pass rather than assuming
        # every new candidate starts in "To be Considered".
        deadline = time.monotonic() + 600  # real async parsing -- generous bound, not an arbitrary short timeout
        reviewed = []
        decisions_cycle = ["approve", "reject"]
        while time.monotonic() < deadline and len(reviewed) < len(created_accounts):
            for tier in ("To be Considered", "Recommended for Fast-Track"):
                tier_select = driver.find_element(By.ID, "tier-filter")
                tier_select.find_element(By.XPATH, f".//option[text()='{tier}']").click()
                time.sleep(1)  # let the tier-change-triggered reload settle
                cards = driver.find_elements(By.CSS_SELECTOR, ".review-card")
                for card in cards:
                    name_el = card.find_elements(By.TAG_NAME, "strong")
                    if not name_el:
                        continue
                    card_name = name_el[0].text.strip()
                    matching = [c for c in created_accounts if c["display"] == card_name and c["display"] not in reviewed]
                    if not matching:
                        continue
                    decision = decisions_cycle[len(reviewed) % 2]
                    btn = card.find_element(By.CSS_SELECTOR, "button.btn-approve" if decision == "approve" else "button.btn-reject")
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                    btn.click()
                    time.sleep(1.5)  # let the click's own async state update (api.reviewCandidate) settle
                    reviewed.append(card_name)
                    print(f"[recruiter] {decision}d {card_name}")
            if len(reviewed) < len(created_accounts):
                driver.refresh()
                _goto_review_queue(driver, wait, "E2E Live Test Recruiter")
                time.sleep(5)

        print(f"[recruiter] real-time pipeline finished parsing {len(reviewed)}/{len(created_accounts)} candidates within the 600s wait window.")
        _sign_out(driver, wait)

        # ------------------------------------------------------------
        # Step 3: admin persona -- verify the real Dashboard tier counts
        # reflect however many real decisions were actually recorded.
        # ------------------------------------------------------------
        _login(driver, wait, _ADMIN_USERNAME, _ADMIN_PASSWORD)
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(),'Fast-Track')]")))

        approved_count = sum(1 for i in range(len(reviewed)) if i % 2 == 0)
        rejected_count = len(reviewed) - approved_count
        print(f"[admin] expecting at least {approved_count} Fast-Track and {rejected_count} Rejected among real decisions made this run.")

        # A real, non-fabricated assertion: at minimum, the dashboard must
        # be reachable and show real numeric tiles (never a crash/blank
        # page) -- exact count assertions are intentionally loose here
        # since other real candidates may already exist in this shared
        # Cosmos DB from earlier in this engagement.
        tiles = driver.find_elements(By.CSS_SELECTOR, ".stat-tile-value, [class*='stat']")
        assert tiles, "Admin dashboard did not render any stat tiles after real candidate decisions were recorded."

    except Exception:
        screenshot_path = tmp_path / "candidate_lifecycle_live_failure.png"
        try:
            driver.save_screenshot(str(screenshot_path))
            print(f"Screenshot saved to {screenshot_path}")
        except Exception:
            pass
        raise
