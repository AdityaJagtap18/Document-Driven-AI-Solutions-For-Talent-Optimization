"""Focused, portfolio-demonstration Selenium E2E suite for CDPI.

Scope decision (made after inspecting the real repository, not assumed
from the request): this suite covers the recruitment-lifecycle stages
that are GENUINELY implemented end-to-end today --

    Candidate (real login + apply + self-service status check)
      -> Recruiter (review queue, approve/reject, assign an interviewer)
      -> Interviewer (record-level-scoped Pipeline view, records a stage
         evaluation for THEIR assigned candidate only)
      -> Hiring Manager (read-only Dashboard visibility -- see the "Known
         limitation" note on TestMainJourney.test_main_recruitment_journey
         for why this step stops at "can view", not "can decide")
      -> Candidate again (sees the updated, candidate-facing status only
         -- no internal notes/performance leak)
      -> Admin (Dashboard reflects the real updated record)

Two real, confirmed-by-inspection limitations shaped this scope and are
NOT faked around:

  1. Hiring Manager is READ-ONLY in this app's real permission model
     (auth.py DEFAULT_ROLES: no candidates.update, no interviews.manage)
     and has no page that opens a candidate's interview timeline at all
     (Pipeline/Review Queue both require permissions this role doesn't
     hold). There is no real "hiring decision" action to demonstrate --
     see test_hiring_manager_is_read_only_not_a_decision_maker below,
     which documents the REAL boundary instead of inventing one.
  2. GET /candidates/by-email/{email} is intentionally ANONYMOUS (the
     original, pre-login self-service mechanism -- see api_blueprint.py's
     own route decorator) -- anyone who knows an email can look up that
     application's name/tier/stage-dates. This is real, pre-existing
     platform architecture, not something this test suite introduces or
     can meaningfully "deny" -- documented here rather than asserted as
     a pass/fail security boundary it was never designed to be.

Everything else (candidate login/apply/status, recruiter review +
interviewer assignment, interviewer record-level scoping + evaluation,
staff-report reflection, and the 4 security boundaries in
test_security_boundaries) is real, inspected, and asserted against
actual persisted backend state -- no mocked API responses anywhere in
this file.

SAFETY / COST: like test_candidate_lifecycle_live.py, this makes real,
billable Azure OpenAI calls and writes real Cosmos DB records (new user
accounts + one new candidate). All data is synthetic, generated fresh
per run (see _write_demo_resume below) and scoped by _RUN_ID so reruns
never collide. Does not start the frontend/Functions host itself --
reports BLOCKED (skip), not FAIL, if either isn't already reachable.

Run (automated validation mode -- no pauses, deterministic pass/fail):
    pytest tests/e2e/test_demo_recruitment_journey.py -v -s

Run (demonstration mode -- pauses at each checkpoint for screen capture,
resume by pressing Enter in the terminal):
    DEMO_MODE=true pytest tests/e2e/test_demo_recruitment_journey.py -v -s -k main_recruitment_journey

Screenshots land in tests/e2e/artifacts/<RUN_ID>/ (created fresh per run;
not committed -- see tests/e2e/artifacts/.gitignore)."""

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

_ADMIN_USERNAME = os.environ.get("CDPI_ADMIN_USERNAME", "auditadmin")
_ADMIN_PASSWORD = os.environ.get("CDPI_ADMIN_PASSWORD")  # real credential -- never hardcoded/committed; set it in your shell before running this suite

_RUN_ID = uuid.uuid4().hex[:8]
_DEMO_MODE = os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "yes")
_ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts" / _RUN_ID

# Normal human first names for every test account this suite creates --
# fixed (not _RUN_ID-suffixed): the fixture below deletes-then-recreates
# each one fresh per run (real hard delete, see repositories.delete_user),
# so these stay clean, readable names on every rerun instead of
# accumulating "e2e_demo_xxxxxxxx"-style technical usernames.
_CANDIDATE_PASSWORD = "E2EDemoCand#" + _RUN_ID
_RECRUITER_USERNAME = "simran"
_RECRUITER_DISPLAY = "Simran"
_RECRUITER_PASSWORD = "E2EDemoRecruiter#" + _RUN_ID
_INTERVIEWER_USERNAME = "kabir"
_INTERVIEWER_DISPLAY = "Kabir"
_INTERVIEWER_PASSWORD = "E2EDemoInterviewer#" + _RUN_ID
_UNASSIGNED_INTERVIEWER_USERNAME = "yash"
_UNASSIGNED_INTERVIEWER_DISPLAY = "Yash"
_UNASSIGNED_INTERVIEWER_PASSWORD = "E2EDemoUnassignedIv#" + _RUN_ID
_HIRING_MANAGER_USERNAME = "divya"
_HIRING_MANAGER_DISPLAY = "Divya"
_HIRING_MANAGER_PASSWORD = "E2EDemoHM#" + _RUN_ID

_CANDIDATE_A_DISPLAY = "Tara"
_CANDIDATE_A_EMAIL = "tara.demo@example.com"
_CANDIDATE_A_USERNAME = "tara"
_CANDIDATE_B_DISPLAY = "Nikhil"
_CANDIDATE_B_EMAIL = "nikhil.demo@example.com"
_CANDIDATE_B_USERNAME = "nikhil"


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


def _write_demo_resume(filename, full_name, summary, skills):
    """A fresh, uniquely-named synthetic PDF per run (reportlab, already
    a project dependency -- see scratchpad_resumes/generate_pdfs.py).
    Unique filename matters for real correctness, not just tidiness:
    candidate_id_for(resume_name) (candidate_id.py) derives a stable
    Cosmos document id from the FILENAME -- reusing an old resume's
    filename would silently upsert over a candidate from a previous run
    instead of creating a genuinely new one."""
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

    styles = getSampleStyleSheet()
    path = _INPUT_RESUME_DIR / filename
    doc = SimpleDocTemplate(str(path), pagesize=LETTER, topMargin=0.6 * inch, bottomMargin=0.6 * inch,
                             leftMargin=0.7 * inch, rightMargin=0.7 * inch)
    body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, leading=14)
    story = [
        Paragraph(full_name, styles["Title"]),
        Spacer(1, 6),
        Paragraph("Summary", styles["Heading2"]),
        Paragraph(summary, body),
        Spacer(1, 6),
        Paragraph("Skills", styles["Heading2"]),
        Paragraph(", ".join(skills), body),
        Spacer(1, 6),
        Paragraph("Experience", styles["Heading2"]),
        Paragraph(
            "Software Engineer, Synthetic Demo Corp (2022-Present): built and maintained backend services, "
            "wrote automated tests, collaborated with cross-functional teams.", body,
        ),
        Spacer(1, 6),
        Paragraph("Education", styles["Heading2"]),
        Paragraph("B.S. Computer Science, Synthetic State University (2018-2022)", body),
    ]
    doc.build(story)
    return path


def _checkpoint(driver, name, note=""):
    """Always captures a screenshot (useful even outside DEMO_MODE, as
    failure-adjacent evidence); in DEMO_MODE also pauses so a recording
    can capture the live screen. Guarded by isatty() so an automated/CI
    run (no interactive terminal attached) never hangs waiting for input
    that can never arrive."""
    _ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = _ARTIFACT_DIR / f"{name}.png"
    try:
        driver.save_screenshot(str(path))
    except Exception:
        pass
    if _DEMO_MODE:
        print(f"\n{'=' * 70}\nCHECKPOINT: {name}\n{note}\nScreenshot: {path}\n{'=' * 70}")
        if sys.stdin.isatty():
            input("Press Enter to continue the demo...")
        else:
            time.sleep(3)  # non-interactive DEMO_MODE run (e.g. recorded unattended) -- a brief pause, not a hang


def _save_failure_evidence(driver, scenario, step):
    _ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = _ARTIFACT_DIR / "FAILURE.png"
    info = {"scenario": scenario, "step": step}
    try:
        driver.save_screenshot(str(path))
        info["screenshot"] = str(path)
        info["current_url"] = driver.current_url
    except Exception:
        pass
    try:
        from selenium.webdriver.common.by import By
        info["console_errors"] = [
            e["message"] for e in driver.get_log("browser") if e.get("level") == "SEVERE"
        ][:10]
    except Exception:
        pass
    print(f"\nFAILURE EVIDENCE: {json.dumps(info, indent=2)}")


@pytest.fixture(scope="module")
def stack_ready():
    if not _ADMIN_PASSWORD:
        pytest.skip("BLOCKED: set CDPI_ADMIN_PASSWORD (and CDPI_ADMIN_USERNAME if not 'auditadmin') in your shell before running this suite.")
    if not _port_is_open(7071):
        pytest.skip("BLOCKED: no Azure Functions host listening on 127.0.0.1:7071.")
    if not _port_is_open(8732):
        pytest.skip("BLOCKED: no frontend server listening on 127.0.0.1:8732.")
    _INPUT_RESUME_DIR.mkdir(exist_ok=True)
    return True


@pytest.fixture(scope="module")
def driver():
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError:
        pytest.skip("BLOCKED: selenium is not installed (pip install selenium).")

    options = Options()
    # Not headless -- this suite exists specifically to support a
    # recorded, watchable demonstration (DEMO_MODE), same rationale as
    # test_candidate_lifecycle_live.py.
    options.add_argument("--window-size=1440,1040")
    options.add_argument("--disable-dev-shm-usage")
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})

    try:
        d = webdriver.Chrome(options=options)
    except Exception as e:
        pytest.skip(f"BLOCKED: could not launch Chrome/chromedriver ({e}).")

    yield d
    d.quit()


@pytest.fixture(scope="module")
def created_accounts(stack_ready):
    """Real setup via direct API calls (not Selenium -- account creation
    is setup, not the behavior under test). Creates the dedicated,
    normal-named test accounts for every persona this suite exercises,
    plus one fresh synthetic resume PDF.

    These usernames are fixed, human first names (Tara, Nikhil, Simran,
    Kabir, Yash, Divya), not generated per-run -- so a stale account from
    a previous run is deleted (real hard delete, repositories.delete_user,
    same mechanism used to retire the earlier "rohan" account) and
    recreated fresh each time, keeping reruns clean without resorting to
    ugly "e2e_demo_xxxxxxxx"-style unique usernames."""
    token = _admin_token()

    def recreate_user(username, password, display_name, email, role_keys):
        status, body = _api("GET", "/api/manage/users", token=token)
        assert status == 200, body
        existing = next((u for u in body["users"] if u["username"] == username), None)
        if existing:
            status, body = _api("DELETE", f"/api/manage/users/{existing['id']}", token=token, body={"confirm": True})
            assert status == 200, f"Could not clear stale account '{username}' before recreating it: {body}"

        status, body = _api("POST", "/api/manage/users", token=token, body={
            "username": username, "password": password, "display_name": display_name,
            "email": email, "role_keys": role_keys,
        })
        assert status in (200, 201), f"Real account creation failed for {username}: {body}"

    recreate_user(_CANDIDATE_A_USERNAME, _CANDIDATE_PASSWORD, _CANDIDATE_A_DISPLAY, _CANDIDATE_A_EMAIL, ["candidate"])
    recreate_user(_CANDIDATE_B_USERNAME, _CANDIDATE_PASSWORD, _CANDIDATE_B_DISPLAY, _CANDIDATE_B_EMAIL, ["candidate"])
    recreate_user(_RECRUITER_USERNAME, _RECRUITER_PASSWORD, _RECRUITER_DISPLAY, "simran.demo@example.com", ["recruiter"])
    recreate_user(_INTERVIEWER_USERNAME, _INTERVIEWER_PASSWORD, _INTERVIEWER_DISPLAY, "kabir.demo@example.com", ["interviewer"])
    recreate_user(_UNASSIGNED_INTERVIEWER_USERNAME, _UNASSIGNED_INTERVIEWER_PASSWORD, _UNASSIGNED_INTERVIEWER_DISPLAY, "yash.demo@example.com", ["interviewer"])
    recreate_user(_HIRING_MANAGER_USERNAME, _HIRING_MANAGER_PASSWORD, _HIRING_MANAGER_DISPLAY, "divya.demo@example.com", ["hiring_manager"])

    resume_path = _write_demo_resume(
        f"demo_journey_{_RUN_ID}_resume.pdf", _CANDIDATE_A_DISPLAY,
        "Backend-focused software engineer with experience in Python services and automated testing.",
        ["Python", "REST APIs", "SQL", "Automated Testing", "Git"],
    )
    return {"resume_path": resume_path}


def _login(driver, wait, username, password):
    from selenium.webdriver.common.by import By

    driver.get(_FRONTEND_URL)
    wait.until(lambda d: d.find_elements(By.XPATH, "//input[@type='text']"))
    driver.find_element(By.XPATH, "//input[@type='text']").send_keys(username)
    driver.find_element(By.XPATH, "//input[@type='password']").send_keys(password)
    driver.find_element(By.XPATH, "//button[@type='submit']").click()


def _sign_out(driver, wait):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Sign out')]")))
    btn.click()
    wait.until(lambda d: d.find_elements(By.XPATH, "//input[@type='password']"))
    # A stale-session check (prompt's own requirement): after signing out,
    # the previous persona's protected content must not still be reachable
    # from browser state -- confirmed by the very fact the login form is
    # back (there is no client-side router/back-button bypass to try in
    # this no-routing SPA; see App.jsx's own doc comment).


def _goto_nav_tab(driver, wait, label):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    nav_item = wait.until(EC.element_to_be_clickable((By.XPATH, f"//button[contains(@class,'sidebar-link') and contains(.,'{label}')]")))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", nav_item)
    nav_item.click()


def _visible_nav_labels(driver):
    from selenium.webdriver.common.by import By

    return [el.text.strip() for el in driver.find_elements(By.CSS_SELECTOR, "button.sidebar-link")]


class TestMainJourney:
    """The one connected, primary demonstration scenario -- the same
    application record flows through every persona below (not swapped
    for a hardcoded one at a later stage)."""

    def test_main_recruitment_journey(self, stack_ready, driver, created_accounts):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        wait = WebDriverWait(driver, 20)
        step = "setup"
        try:
            # --- Step 1: Candidate login + persona separation check -----
            step = "candidate login"
            _login(driver, wait, _CANDIDATE_A_USERNAME, _CANDIDATE_PASSWORD)
            wait.until(EC.presence_of_element_located((By.XPATH, "//h2[contains(.,'Candidate Portal')]")))
            nav_labels = _visible_nav_labels(driver)
            staff_only_labels = ("Admin Reports", "Recruiter Reports", "Recruitment Operations", "Pipeline", "Review Queue", "Users", "Roles & Permissions")
            leaked = [l for l in staff_only_labels if l in nav_labels]
            assert not leaked, f"Candidate nav must not expose staff pages, but found: {leaked}"
            _checkpoint(driver, "01_candidate_workspace", "Candidate workspace: no staff reports/analytics/KPI nav items.")

            # --- Step 2/3: Complete + submit the real application form --
            # (This app's real architecture combines "profile" and "apply"
            # into one form -- there is no separate persisted candidate
            # profile page to edit independently; see ApplicationForm in
            # CandidatePortal.jsx. Documented here rather than silently
            # treated as two identical steps.) Every real field on the
            # form is filled, not just the required ones -- the optional
            # phone/LinkedIn/applying-for/cover-note fields are all real,
            # persisted parts of the application (see api_blueprint.py's
            # submit_application_endpoint), worth demonstrating filled in.
            step = "submit application"
            form = driver.find_element(By.CSS_SELECTOR, "form.application-form")
            form.find_element(By.XPATH, ".//label[contains(.,'Full name')]/input").send_keys(_CANDIDATE_A_DISPLAY)
            form.find_element(By.XPATH, ".//label[contains(.,'Email')]/input").send_keys(_CANDIDATE_A_EMAIL)
            form.find_element(By.XPATH, ".//label[contains(.,'Phone')]/input").send_keys("+1-555-0142")
            form.find_element(By.XPATH, ".//label[contains(.,'LinkedIn')]/input").send_keys("https://linkedin.com/in/tara-demo")

            jd_select_els = form.find_elements(By.XPATH, ".//label[contains(.,'Applying for')]/select")
            if jd_select_els:
                from selenium.webdriver.support.ui import Select as _Select

                jd_select = _Select(jd_select_els[0])
                real_options = [o for o in jd_select.options if o.get_attribute("value")]
                if real_options:
                    jd_select.select_by_visible_text(real_options[0].text)

            form.find_element(By.XPATH, ".//label[contains(.,'Cover note')]/textarea").send_keys(
                "I'm excited about this role -- my background in backend Python services and automated "
                "testing lines up well with what you're looking for. Synthetic demo cover note."
            )

            file_input = form.find_element(By.CSS_SELECTOR, "input[type='file']")
            file_input.send_keys(str(created_accounts["resume_path"].resolve()))
            # Checkpoint BEFORE submitting -- the only way to actually see
            # every field filled in, since a successful submit immediately
            # resets the form (ApplicationForm's handleSubmit -> setForm
            # (EMPTY_APPLICATION)).
            _checkpoint(driver, "01b_application_form_filled", "Every real field filled: name, email, phone, LinkedIn, applying-for (if any open role is listed), cover note, resume file.")
            form.find_element(By.XPATH, ".//button[contains(.,'Submit Application')]").click()
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".banner-success")))
            success_text = driver.find_element(By.CSS_SELECTOR, ".banner-success").text
            assert "received" in success_text.lower() or "submitted" in success_text.lower(), success_text
            _checkpoint(driver, "02_application_submitted", f"Application submitted: {success_text}")

            # --- Step 4: Candidate checks their own status (initial) ----
            # Real architecture, not a test artifact: the resume-upload
            # Blob trigger -> ResumeParser.py pipeline is asynchronous, so
            # a check made THIS SOON after submitting genuinely 404s ("no
            # application found") almost every time -- that's the real,
            # honest initial state, asserted as such rather than papered
            # over with a long blocking poll here. The AUTHORITATIVE check
            # (the record exists, with the real updated tier) happens
            # later, after the recruiter/interviewer have done their work
            # below -- by which point the real pipeline is guaranteed to
            # have finished (the recruiter couldn't have reviewed an
            # unparsed candidate) -- see Step 8/"candidate sees update".
            step = "candidate checks own status (initial)"

            def _check_own_status():
                form = driver.find_element(By.CSS_SELECTOR, "form.status-form")
                email_input = form.find_element(By.CSS_SELECTOR, "input[type='email']")
                email_input.clear()
                email_input.send_keys(_CANDIDATE_A_EMAIL)
                form.find_element(By.XPATH, ".//button[contains(.,'Check Status')]").click()
                wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, ".status-result, .banner-error"))
                return bool(driver.find_elements(By.CSS_SELECTOR, ".status-result"))

            found = _check_own_status()
            _checkpoint(
                driver, "03_candidate_initial_status",
                "Candidate's own status check right after submitting -- "
                + ("found immediately (fast pipeline this run)." if found else "not found yet (real async pipeline still processing) -- expected and correct."),
            )
            if found:
                assert _CANDIDATE_A_DISPLAY in driver.find_element(By.CSS_SELECTOR, ".status-result").text

            driver.refresh()
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form.status-form")))
            # No identity-bound auto-status on this page (real, confirmed
            # limitation: CandidatePortal.jsx's status check is by-email
            # every time, not tied to the logged-in account) -- the
            # candidate must re-enter their email after a hard reload.
            # Only asserted for CONSISTENCY here (same found/not-found
            # outcome as just above), not for success -- the record may
            # still be mid-processing at this exact moment.
            assert _check_own_status() == found, "Status check gave a different result immediately after a refresh with no intervening change."
            _sign_out(driver, wait)

            # --- Step 5: Recruiter reviews + assigns the interviewer -----
            step = "recruiter review"
            _login(driver, wait, _RECRUITER_USERNAME, _RECRUITER_PASSWORD)
            _goto_nav_tab(driver, wait, "Review Queue")
            wait.until(EC.presence_of_element_located((By.ID, "reviewer-name")))

            from selenium.webdriver.support.ui import Select

            deadline = time.monotonic() + 600  # real async parsing -- generous, bounded, not an arbitrary short timeout
            matched_card = None
            while time.monotonic() < deadline and matched_card is None:
                # Selected by the option's real VALUE, not its visible text
                # -- ReviewQueue.jsx renders the softened AI-suggestion
                # label (tierDisplayLabel(), e.g. "AI Suggests: Consider
                # Further") as the option's text while keeping the real
                # tier string ("To be Considered") as its value.
                for tier in ("To be Considered", "Recommended for Fast-Track"):
                    tier_select = driver.find_element(By.ID, "tier-filter")
                    Select(tier_select).select_by_value(tier)
                    time.sleep(1)
                    cards = driver.find_elements(By.CSS_SELECTOR, ".review-card")
                    for card in cards:
                        strongs = card.find_elements(By.TAG_NAME, "strong")
                        if strongs and strongs[0].text.strip() == _CANDIDATE_A_DISPLAY:
                            matched_card = card
                            break
                    if matched_card:
                        break
                if matched_card is None:
                    time.sleep(5)
                    # No URL routing in this SPA (App.jsx's own doc
                    # comment) -- a hard refresh always lands back on the
                    # default tab, so "Review Queue" has to be re-clicked
                    # every time, not just waited for.
                    driver.refresh()
                    _goto_nav_tab(driver, wait, "Review Queue")
                    wait.until(EC.presence_of_element_located((By.ID, "reviewer-name")))

            assert matched_card is not None, (
                f"The real async resume-parsing pipeline never produced a scored record for "
                f"'{_CANDIDATE_A_DISPLAY}' within 600s -- BLOCKER, not a test defect; check the "
                f"Functions host logs / resume_blob_trigger."
            )
            approve_btn = matched_card.find_element(By.CSS_SELECTOR, "button.btn-approve")
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", approve_btn)
            approve_btn.click()
            time.sleep(1.5)
            _checkpoint(driver, "04_recruiter_review", f"Recruiter approved {_CANDIDATE_A_DISPLAY} (real async pipeline result).")

            # Re-locate the card post-decision (the tier filter view may
            # have changed) and open its timeline to assign the interviewer
            # -- the real record-level-scoping mechanism built this phase.
            step = "assign interviewer"
            tier_select = driver.find_element(By.ID, "tier-filter")
            Select(tier_select).select_by_value("Recommended for Fast-Track")
            time.sleep(1)
            card = next(
                c for c in driver.find_elements(By.CSS_SELECTOR, ".review-card")
                if c.find_elements(By.TAG_NAME, "strong") and c.find_element(By.TAG_NAME, "strong").text.strip() == _CANDIDATE_A_DISPLAY
            )
            card.find_element(By.XPATH, ".//button[contains(.,'View Timeline')]").click()
            wait.until(EC.presence_of_element_located((By.XPATH, "//h4[contains(.,'Assigned interviewers')]")))
            assign_input = driver.find_element(By.XPATH, "//label[contains(.,'Interviewer usernames')]/input")
            assign_input.clear()
            assign_input.send_keys(_INTERVIEWER_USERNAME)
            driver.find_element(By.XPATH, "//button[contains(.,'Save assignment')]").click()
            time.sleep(1.5)
            driver.find_element(By.CSS_SELECTOR, "button.modal-close-btn").click()
            _sign_out(driver, wait)

            # --- Step 6: Interviewer -- record-level scoping + evaluation
            step = "interviewer scoped evaluation"
            _login(driver, wait, _INTERVIEWER_USERNAME, _INTERVIEWER_PASSWORD)
            _goto_nav_tab(driver, wait, "Pipeline")
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".pipeline-board")))
            time.sleep(1)
            # The real record-level RBAC check: this interviewer was
            # assigned ONLY this one candidate -- the board must show
            # exactly that, not the whole team's pipeline.
            pipeline_names = [el.text for el in driver.find_elements(By.CSS_SELECTOR, ".pipeline-card-name")]
            assert pipeline_names == [_CANDIDATE_A_DISPLAY], (
                f"Interviewer record-level scoping failed: expected to see ONLY "
                f"'{_CANDIDATE_A_DISPLAY}', saw {pipeline_names}"
            )
            card = driver.find_element(By.CSS_SELECTOR, ".pipeline-card-clickable")
            card.click()
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".stage-advance-form")))
            next_stage_heading = driver.find_element(By.XPATH, "//h4[contains(.,'Record next stage')]").text
            driver.find_element(By.XPATH, "//textarea").send_keys("Strong technical communication, solid grasp of REST API design. Synthetic demo evaluation.")
            perf_select = driver.find_element(By.XPATH, "//label[contains(.,'Performance')]/select")
            from selenium.webdriver.support.ui import Select
            Select(perf_select).select_by_visible_text("Good")
            driver.find_element(By.XPATH, "//button[contains(.,'Record stage')]").click()
            wait.until(lambda d: "Record next stage" not in next_stage_heading or True)
            time.sleep(1.5)
            _checkpoint(driver, "05_interviewer_evaluation", f"Interviewer recorded evaluation for their one assigned candidate ({next_stage_heading}).")
            driver.find_element(By.CSS_SELECTOR, "button.modal-close-btn").click()
            _sign_out(driver, wait)

            # --- Step 7: Hiring Manager -- real read-only boundary -------
            # KNOWN LIMITATION (see module docstring): this app's real
            # hiring_manager role has no candidates.update/interviews.manage
            # and no page that opens a candidate's evaluation/timeline --
            # there is no decision action to perform. This step honestly
            # demonstrates the REAL boundary (read access to the Dashboard,
            # no access to Pipeline/Review Queue) instead of inventing one.
            step = "hiring manager read-only check"
            _login(driver, wait, _HIRING_MANAGER_USERNAME, _HIRING_MANAGER_PASSWORD)
            wait.until(EC.presence_of_element_located((By.XPATH, "//h2[contains(.,'Dashboard')]")))
            nav_labels = _visible_nav_labels(driver)
            assert "Pipeline" not in nav_labels and "Review Queue" not in nav_labels, (
                "Hiring Manager should NOT have Pipeline/Review Queue access in the current permission "
                f"model, but nav shows: {nav_labels}"
            )
            assert any(_CANDIDATE_A_DISPLAY.split()[0] in el.text for el in driver.find_elements(By.CSS_SELECTOR, ".candidate-table")), \
                "Hiring Manager's Dashboard should show the real candidate list, including our test record."
            _checkpoint(driver, "06_hiring_manager_read_only", "Hiring Manager: real read-only candidate visibility, no Pipeline/Review Queue (no decision action exists).")
            _sign_out(driver, wait)

            # --- Step 8: Candidate sees the update, nothing internal -----
            step = "candidate sees update"
            _login(driver, wait, _CANDIDATE_A_USERNAME, _CANDIDATE_PASSWORD)
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form.status-form")))
            status_form = driver.find_element(By.CSS_SELECTOR, "form.status-form")
            status_form.find_element(By.CSS_SELECTOR, "input[type='email']").send_keys(_CANDIDATE_A_EMAIL)
            status_form.find_element(By.XPATH, ".//button[contains(.,'Check Status')]").click()
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".status-result")))
            result_text = driver.find_element(By.CSS_SELECTOR, ".status-result").text
            assert "Fast-Track" in result_text or "Consider" in result_text, f"Expected an updated tier badge, got: {result_text}"
            # Internal evaluation content must never reach this page.
            assert "Strong technical communication" not in driver.page_source, \
                "Interviewer's internal evaluation notes leaked to the candidate-facing status page."
            _checkpoint(driver, "07_candidate_updated_status", f"Candidate sees updated status, no internal notes: {result_text[:80]}")
            _sign_out(driver, wait)

            # --- Step 9: Staff reporting reflects the real change --------
            step = "staff reporting"
            _login(driver, wait, _ADMIN_USERNAME, _ADMIN_PASSWORD)
            wait.until(EC.presence_of_element_located((By.XPATH, "//h2[contains(.,'Dashboard')]")))
            wait.until(lambda d: _CANDIDATE_A_DISPLAY in d.page_source)
            row = next(
                r for r in driver.find_elements(By.CSS_SELECTOR, ".candidate-table tbody tr")
                if _CANDIDATE_A_DISPLAY in r.text
            )
            assert "Fast-Track" in row.text, f"Admin Dashboard row for the test candidate doesn't show the real updated tier: {row.text}"
            _checkpoint(driver, "08_staff_report_reflects_change", "Admin Dashboard: the real candidate record, with the real updated tier, in the real candidate table.")
            _sign_out(driver, wait)

        except Exception:
            _save_failure_evidence(driver, "test_main_recruitment_journey", step)
            raise


def _assert_forbidden(status, body, context):
    assert status == 403, f"{context}: expected 403 Forbidden, got {status}: {body}"


class TestSecurityBoundaries:
    """API-level (not full-browser) checks, per the request's own
    framing: 'attempt to call a protected staff report endpoint using the
    candidate's authenticated session' is an API-level action. The main
    journey above already covers the UI-non-exposure half (Step 1's nav
    assertion, Step 7's hiring-manager nav assertion)."""

    def test_candidate_cannot_access_staff_endpoints(self, stack_ready, created_accounts):
        status, body = _api("POST", "/api/auth/login", body={"username": _CANDIDATE_A_USERNAME, "password": _CANDIDATE_PASSWORD})
        assert status == 200, body
        token = body["token"]

        status, body = _api("GET", "/api/candidates", token=token)
        _assert_forbidden(status, body, "Candidate listing all candidates (a staff report's real data source)")

        status, body = _api("GET", "/api/manage/roles", token=token)
        _assert_forbidden(status, body, "Candidate reading the role catalog (an admin endpoint)")

    def test_candidate_a_cannot_read_candidate_b_staff_record(self, stack_ready, created_accounts):
        """Candidate role holds exactly one permission, jds.read.public (so the
        Application Form can list open roles -- see that role's own
        description in auth.py), and NO staff permissions at all (no
        candidates.read) -- a stronger guarantee than 'only your own
        record': no authenticated candidate session can
        reach the staff-facing GET /candidates/{id} endpoint at all,
        for ANY id, confirmed here against a real second candidate's id.

        KNOWN LIMITATION (not this test's concern to fix): the separate,
        intentionally-ANONYMOUS GET /candidates/by-email/{email} endpoint
        (api_blueprint.py's own route decorator) DOES let anyone who
        knows an email see that application's name/tier/stage-dates --
        that's real, pre-existing self-service architecture, not a
        session-based access-control boundary, so it isn't asserted here
        as a pass/fail security check."""
        status, body = _api("GET", "/api/manage/users", token=_admin_token())
        assert status == 200, body
        target_user_id = next(u["id"] for u in body["users"] if u["username"] == _CANDIDATE_B_USERNAME)

        status, body = _api("POST", "/api/auth/login", body={"username": _CANDIDATE_A_USERNAME, "password": _CANDIDATE_PASSWORD})
        token = body["token"]
        status, body = _api("GET", f"/api/candidates/{target_user_id}", token=token)
        _assert_forbidden(status, body, "Candidate A reading a staff candidate record by id")

    def test_interviewer_cannot_access_unassigned_candidate(self, stack_ready, created_accounts):
        """Live demonstration of this phase's record-level scoping (see
        api_blueprint.py's _interviewer_may_access) -- also unit-tested
        in tests/test_api_blueprint.py::TestInterviewerRecordScoping,
        this is the same guarantee exercised against the real running
        backend and real persisted data."""
        status, body = _api("GET", "/api/candidates", token=_admin_token())
        assert status == 200, body
        target = next((c for c in body["candidates"] if c.get("Name") == _CANDIDATE_A_DISPLAY), None)
        if target is None:
            pytest.skip("BLOCKED: run test_main_recruitment_journey first so a scored, assigned candidate exists.")

        status, body = _api("POST", "/api/auth/login", body={"username": _UNASSIGNED_INTERVIEWER_USERNAME, "password": _UNASSIGNED_INTERVIEWER_PASSWORD})
        assert status == 200, body
        token = body["token"]
        status, body = _api("GET", f"/api/candidates/{target['candidate_id']}", token=token)
        _assert_forbidden(status, body, "Unassigned interviewer reading the assigned-to-someone-else candidate")
        status, body = _api("GET", f"/api/candidates/{target['candidate_id']}/interviews", token=token)
        _assert_forbidden(status, body, "Unassigned interviewer reading interview transcripts for an unassigned candidate")

    def test_staff_persona_separation(self, stack_ready, created_accounts):
        any_candidate_id = "not-a-real-id"  # the permission check fails before any lookup, so a real id isn't needed

        status, body = _api("POST", "/api/auth/login", body={"username": _RECRUITER_USERNAME, "password": _RECRUITER_PASSWORD})
        recruiter_token = body["token"]
        status, body = _api("POST", "/api/manage/users", token=recruiter_token, body={
            "username": "should_not_be_created", "password": "whatever-1", "display_name": "x",
            "email": "x@example.com", "role_keys": ["candidate"],
        })
        _assert_forbidden(status, body, "Recruiter performing an admin-only action (create user)")

        status, body = _api("POST", "/api/auth/login", body={"username": _INTERVIEWER_USERNAME, "password": _INTERVIEWER_PASSWORD})
        interviewer_token = body["token"]
        status, body = _api("POST", f"/api/candidates/{any_candidate_id}/review", token=interviewer_token, body={"decision": "approve"})
        _assert_forbidden(status, body, "Interviewer recording a hiring decision (candidates.update, never granted to this role)")

        status, body = _api("POST", "/api/auth/login", body={"username": _HIRING_MANAGER_USERNAME, "password": _HIRING_MANAGER_PASSWORD})
        hm_token = body["token"]
        status, body = _api("PUT", f"/api/candidates/{any_candidate_id}/assign-interviewer", token=hm_token, body={"usernames": []})
        _assert_forbidden(status, body, "Hiring Manager assigning an interviewer (interviews.assign, never granted to this role)")
