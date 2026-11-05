# CDPI deployment: current state and CI/CD setup

Adapted from a generic VPS deployment playbook for this project's actual
(unusual) shape: **two separate Hostinger products**, not one VPS serving
everything.

## 0. What's actually live right now (verified by curling it, not assumed)

| Piece | Host | Confirmed by |
|---|---|---|
| Frontend (`app.adityajagtap.com`) | Hostinger **shared hosting** (hPanel/hcdn), static files | `curl -sI` shows `platform: hostinger`, `Server: hcdn` |
| Backend (`api.adityajagtap.com`) | Hostinger **KVM VPS**, `82.25.125.164:65002`, nginx + gunicorn running `hostinger-deploy/wsgi_app.py` | `curl -sI` shows `Server: nginx/1.28.3 (Ubuntu)`; see commit `196fdb8` |

The generic playbook's Docker/docker-compose shape doesn't apply here --
this repo has no `Dockerfile` and isn't containerized. The shape below is
"git pull + restart a systemd service" instead, matching what's already
running on the VPS.

**Known gap, not hidden:** the VPS's nginx vhost and the gunicorn process
were set up by hand over SSH in an earlier session -- neither is tracked
in this repo. `cdpi-backend.service` and `nginx-api.conf.example` in this
folder are reference copies of what's *presumably* configured; diff them
against the real files over SSH rather than assuming they match, and
install `cdpi-backend.service` for real (see its own header) if a
`systemctl status cdpi-backend` on the VPS comes back empty -- i.e. if
gunicorn is actually being kept alive some other way (a bare `nohup`, a
`screen`/`tmux` session, cron `@reboot`), which won't survive a crash or
reboot as cleanly as systemd would.

## 1. What's blocking automated backend deploys right now

A deploy key already exists for this (`~/.ssh/hostinger_kvm`, comment
`cdpi-backend-kvm`, matching `known_hosts` for `82.25.125.164:65002`),
but the **SSH username it's authorized for is unknown** -- `root` was
tried and refused (`Permission denied (publickey,password)`). Find this
out (check hPanel's VPS page, or however that key was originally
provisioned) before anything below can actually run. Don't guess
usernames over SSH -- that reads as credential brute-forcing regardless
of intent.

Once you have it, add it to:

```bash
ssh -i ~/.ssh/hostinger_kvm -p 65002 <username>@82.25.125.164 whoami
```

## 2. Backend CI/CD (`.github/workflows/deploy-backend.yml`)

Already added to this repo. Runs after `ci.yml`'s test job succeeds on
`main`, and is a safe no-op (explicitly checked, not just "it'll fail
quietly") until these repo secrets exist (**Settings -> Secrets and
variables -> Actions**):

| Secret | Value |
|---|---|
| `VPS_HOST` | `82.25.125.164` |
| `VPS_PORT` | `65002` |
| `VPS_USER` | *(the username from step 1)* |
| `VPS_SSH_KEY` | private half of `~/.ssh/hostinger_kvm` |
| `VPS_DEPLOY_PATH` | wherever this repo is checked out on the VPS, e.g. `/opt/cdpi` |
| `VPS_VENV_DIR` | that checkout's venv, e.g. `/opt/cdpi/venv` |

The deploy script itself (`git reset --hard origin/main`, `pip install`,
`systemctl restart cdpi-backend`) assumes:
- the repo is already `git clone`d on the VPS at `VPS_DEPLOY_PATH`
- `cdpi-backend.service` (this folder) is installed and enabled
- the deploy user can `sudo systemctl restart cdpi-backend` without a
  password prompt (add a narrow `/etc/sudoers.d/` rule for just that one
  command, don't give the deploy user full sudo)

None of this has been applied to the real VPS yet -- these are the repo
side artifacts only. **Do the first deploy manually once (SSH in,
confirm the service file, confirm the app actually starts) before
trusting this workflow**, same reasoning as any first deploy: a schema
or dependency gap that an in-memory test never exercises can leave the
container "started" but every real request failing.

## 3. Frontend deploys (shared hosting, not the VPS)

The frontend is on the separate shared-hosting plan, served via hPanel
-- SSH-based CI/CD doesn't apply the same way. Two realistic options,
neither set up yet:

- **hPanel's own Git integration** (if this plan has it under
  Websites -> your site -> Git): point it at this repo's `main` branch
  and `frontend/` as the document root; hPanel pulls on its own schedule
  or webhook, no GitHub Actions secret needed.
- **FTP/SFTP upload from CI** (e.g. `SamKirkland/FTP-Deploy-Action`) if
  Git integration isn't available on this plan -- needs FTP credentials
  as a repo secret, uploading `frontend/` on every push to `main`.

Check hPanel for Git integration before reaching for the FTP action --
it's zero extra CI surface if it's there.

## 4. Verify end-to-end after any deploy

```bash
curl -s https://api.adityajagtap.com/api/auth/demo-login-options
```

Should return the 12-persona JSON list once the backend has the demo-login
feature deployed (currently 404 -- that endpoint isn't live yet, confirming
nothing has auto-deployed since it was merged). Then actually log in
through `https://app.adityajagtap.com` and hit one real data endpoint,
not just a 200 on `/`.
