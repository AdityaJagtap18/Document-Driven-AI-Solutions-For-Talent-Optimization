# CDPI deployment: current state and CI/CD setup

Adapted from a generic VPS deployment playbook for this project's actual
(unusual) shape: **two separate Hostinger products**, not one VPS serving
everything.

## 0. What's actually live right now (verified by curling it, not assumed)

| Piece | Host | Confirmed by |
|---|---|---|
| Frontend (`app.adityajagtap.com`) | Hostinger **shared hosting** (hPanel/hcdn), static files, account `u185188662` | `curl -sI` shows `platform: hostinger`, `Server: hcdn` |
| Backend (`api.adityajagtap.com`) | Hostinger **KVM VPS**, `187.127.108.141` port `22`, root, nginx + gunicorn running `hostinger-deploy/wsgi_app.py` at `/opt/cdpi-backend` | confirmed live via SSH (`systemctl status cdpi-backend`) |

The generic playbook's Docker/docker-compose shape doesn't apply here --
this repo has no `Dockerfile` and isn't containerized. The shape below is
"git pull + restart a systemd service" instead, matching what's already
running on the VPS.

`cdpi-backend.service` and `nginx-api.conf.example` in this folder are
now verbatim copies of the real files on the VPS (confirmed over SSH,
not guessed) -- `WorkingDirectory=/opt/cdpi-backend/hostinger-deploy`,
gunicorn bound to `127.0.0.1:8000`, running as root (a real
simplification worth revisiting, not a decision made here).

**As of this writing the VPS checkout is stale** -- `/opt/cdpi-backend`
is on `claude/azure-reports-local-download-4d2d85` at commit `13608be`,
well behind `main` (missing the demo-login feature, the CI fixes, and
this deploy scaffolding itself). Nothing has auto-deployed there yet;
see step 2.

## 1. Backend SSH access (confirmed working)

```bash
ssh -i ~/.ssh/hostinger_kvm -p 22 root@187.127.108.141 whoami
```

Note the **port is 22, not 65002** -- 65002 (what `known_hosts` seemed to
suggest) timed out; plain 22 works. `known_hosts` has a second, unrelated
entry for `[82.25.125.164]:65002` -- a different host (possibly stale),
not this VPS.

## 2. Backend CI/CD (`.github/workflows/deploy-backend.yml`)

Already added to this repo. Runs after `ci.yml`'s test job succeeds on
`main`, and is a safe no-op (explicitly checked, not just "it'll fail
quietly") until these repo secrets exist (**Settings -> Secrets and
variables -> Actions**):

| Secret | Value |
|---|---|
| `VPS_HOST` | `187.127.108.141` |
| `VPS_PORT` | `22` |
| `VPS_USER` | `root` |
| `VPS_SSH_KEY` | private half of `~/.ssh/hostinger_kvm` |
| `VPS_DEPLOY_PATH` | `/opt/cdpi-backend` |
| `VPS_VENV_DIR` | `/opt/cdpi-backend/venv` |

The deploy script itself (`git reset --hard origin/main`, `pip install`,
`systemctl restart cdpi-backend`) assumes the repo is already
`git clone`d at `VPS_DEPLOY_PATH` and `cdpi-backend.service` is
installed and enabled -- both already true on the real VPS. Since the
deploy user here is `root`, the `sudo systemctl restart` in the workflow
is a no-op prefix, not a privilege boundary -- worth narrowing to a
dedicated deploy user with a scoped `/etc/sudoers.d/` rule later rather
than deploying as root indefinitely.

**Do the first deploy manually once (confirm the app actually starts,
check logs) before trusting this workflow** -- same reasoning as any
first deploy: a schema or dependency gap that an in-memory test never
exercises can leave the process "running" but every real request
failing.

## 3. Frontend deploys (shared hosting, not the VPS)

The frontend is on the separate shared-hosting account `u185188662` at
`82.25.125.164:65002` (confirmed via SSH with `~/.ssh/hostinger_cdpi`,
comment `cdpi-deploy`) -- a *different* host from the backend VPS, even
though it's the same IP as what looked like the VPS's `known_hosts`
entry earlier (coincidence, not the same machine -- the VPS is
`187.127.108.141`).

`app.adityajagtap.com`'s real document root is
`domains/adityajagtap.com/public_html/app/` on that account (confirmed
via SSH, not guessed) -- a `.ftp-deploy-sync-state.json` file sitting
there is the telltale sign of a prior deploy via
`SamKirkland/FTP-Deploy-Action` (or the `ftp-deploy` npm package it
wraps, run some other way), even though no such workflow is currently
committed to this repo -- it was removed or never committed, and
whatever deployed it last did so manually or from a workflow that's
since gone.

`.github/workflows/deploy-frontend.yml` (added in this PR) recreates
that pipeline as a tracked, repeatable GitHub Actions job: uploads
`frontend/` to that path over SFTP on every push to `main`, gated the
same way as the backend workflow (no-ops until secrets exist).

| Secret | Value |
|---|---|
| `FRONTEND_SSH_HOST` | `82.25.125.164` |
| `FRONTEND_SSH_PORT` | `65002` |
| `FRONTEND_SSH_USER` | `u185188662` |
| `FRONTEND_SSH_KEY` | private half of `~/.ssh/hostinger_cdpi` |

Never use the account's real login password for this (not done here,
and not something to put in a GitHub secret either) -- the SSH key is
the right credential for CI.

## 4. CDN purge after deploy (`HOSTINGER_API_TOKEN`)

**Why this exists:** on 2026-11-06, `app.adityajagtap.com` went blank
(white screen, `TypeError: api.demoLoginOptions is not a function`) well
after a "successful" `deploy-frontend.yml` run. The uploaded
`apiClient.js` on the origin was actually correct -- some of Hostinger's
CDN (`hcdn`) edge nodes were just still serving a pre-deploy copy of
that file (`Cache-Control: public, max-age=604800`, i.e. a 7-day edge
TTL, with no purge-on-deploy). A newer `Login.jsx` calling a method the
stale `apiClient.js` didn't have yet crashed the whole app for any
visitor hitting one of those stale edges. It resolved on its own once
enough edges happened to revalidate, but that's hours of a
randomly-blank site, not a fix.

The deploy workflow now purges the CDN right after the SCP upload (see
`.github/workflows/deploy-frontend.yml`), via Hostinger's cache-clear
API, gated the same no-op-until-configured way as everything else here:

| Secret | Value |
|---|---|
| `HOSTINGER_API_TOKEN` | A Hostinger API token (hPanel -> API Tokens -> create one scoped to this account), used as a Bearer token against `developers.hostinger.com` |

The endpoint used (`DELETE /api/hosting/v1/accounts/{username}/websites/{domain}/cache/clear?directory={dir}`)
is documented in third-party mirrors of Hostinger's API docs, not
confirmed against `developers.hostinger.com` directly (no account
access to that portal from here) -- **verify it still matches Hostinger's
current API before assuming silence means it worked**, e.g. by checking
the workflow run's logs for the HTTP status this step prints, or by
deliberately deploying a version-bumped file and confirming all edges
serve it immediately rather than over the next 7 days. The purge step is
best-effort and never fails the deploy job on its own (see the
`::warning::` in the workflow rather than a hard failure) -- a missing
token or a failed purge just means edges fall back to self-expiring on
their own TTL, same as before this existed.

## 5. Verify end-to-end after any deploy

```bash
curl -s https://api.adityajagtap.com/api/auth/demo-login-options
```

Should return the 12-persona JSON list once the backend has the demo-login
feature deployed (currently 404 -- that endpoint isn't live yet, confirming
nothing has auto-deployed since it was merged). Then actually log in
through `https://app.adityajagtap.com` and hit one real data endpoint,
not just a 200 on `/`.
