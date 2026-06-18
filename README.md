# Isomer — Compliance Tracking Platform

**Version:** Alpha

Isomer is a Dockerized, browser-based compliance tracking tool for **ISO 27001** and **SOC 2** audits. It provides a complete workflow for managing controls, uploading evidence, assigning ownership, and generating audit reports.

---

## Quick Start

### Running the app with docker-compose

To run the app using docker-compose, execute the following command:

```bash
docker compose up -d --build
```

This command will build the Docker images and start the containers in detached mode. The app will be accessible on port `27001`.

```bash
# First-time setup: create a .env with a real secret.
# The app refuses to start without ISOMER_SECRET set.
cp .env.example .env
python3 -c "import secrets; print('ISOMER_SECRET=' + secrets.token_urlsafe(48))" > .env
chmod 600 .env

# Build and run
docker compose up -d --build

# On the first boot the container generates a one-time bootstrap
# password for the `admin` user and prints it to stderr. Read it once
# and change it from Settings → Users on first login:
docker logs isomer 2>&1 | grep -A1 "bootstrap admin"

# Alternatively, pass ISOMER_BOOTSTRAP_PASSWORD in .env on first boot
# to pick the initial password yourself (env is read only when the
# users table is empty).

# Local access (loopback-only; put nginx in front for remote):
# http://127.0.0.1:27001/
```

## Features

| Feature | Description |
|---------|-------------|
| **Multi-framework** | ISO 27001 (93 Annex A controls) and SOC 2 (44 Trust Services Criteria) |
| **Role-based access** | Admin (full), Auditor (write), Reporter (read-only) |
| **Company management** | Create companies with one or both frameworks, first-time or renewal engagements |
| **Control tracking** | Status (new/in progress/stalled/closed), assignment, notes, tags |
| **Evidence upload** | Screenshots, logs, documents — viewable in browser |
| **Detailed control info** | Expandable panels: explanation, real-world application, challenge level, affected teams, stakeholders |
| **Filtering & search** | By section, status, framework, tag, or free-text search |
| **Audit reports** | In-browser printable report or downloadable ZIP with evidence organized by section/control |
| **Import/Export** | Back up or migrate companies between containers as ZIP files |
| **Renewal support** | Flag controls where prior evidence remains valid |
| **Dashboard** | Aggregate progress stats across all companies |

## Architecture

```
isomer/
├── app.py              # Flask application (routes, DB, auth, CSRF, rate limit)
├── entrypoint.py       # /data/uploads preflight; gunicorn runs the app
├── requirements.txt    # Python dependencies (includes gunicorn, Flask-WTF)
├── Dockerfile          # Container build (gunicorn CMD, 2 workers/4 threads)
├── docker-compose.yml  # Compose deployment (binds 127.0.0.1:27001)
├── .env.example        # Template for ISOMER_SECRET (real .env is gitignored)
├── deploy/
│   └── isomer.zoleb.com.conf  # nginx vhost: HTTP/2, CSP/HSTS/XFO/Permissions
├── data/
│   ├── iso27001_controls.json   # 93 Annex A controls with detailed metadata
│   └── soc2_controls.json       # 44 SOC 2 criteria with detailed metadata
├── static/
│   └── InterVariable.ttf  # locally-served font referenced by templates
└── templates/
    ├── base.html         # Layout, nav, CSS design system, logout POST form
    ├── login.html        # Authentication page (rate-limited in app)
    ├── dashboard.html    # Company overview with stats
    ├── company_form.html # New company creation
    ├── company_view.html # Controls listing, filters, contacts
    ├── control_view.html # Control detail, evidence, notes, assignment
    ├── settings.html     # User management (admin only)
    └── report_view.html  # In-browser audit report
```

## Theming

Isomer follows the [zoleb.com style guide](https://github.com/michaeldtimpe/zoleb/tree/main/style-guide) — Monokai dark (default) with a Monokai light toggle, the shared `zoleb-theme` localStorage key, and locally-served `InterVariable.ttf`. The topbar "I" mark uses `currentColor` so it tracks `--accent` in both themes. Role badges (admin/auditor/reporter) stay distinct with palette-adjacent hues rather than the single accent color.

## Ports & Routing

The application listens on `127.0.0.1:27001` inside the container and is published by Docker on the host at `127.0.0.1:27001` only. Admin tools are exposed via a path prefix and gated by role, not by network port.

| Port | Host binding | Purpose |
|------|--------------|---------|
| **27001** | 127.0.0.1 only | Entire application (user views and admin tools) |

| Path | Purpose | Access |
|------|---------|--------|
| `/` | Dashboard, companies, controls, evidence, reports | All authenticated users (capabilities filtered by role) |
| `/admin` | Admin portal — redirects to `/settings` | Admin role only |
| `/settings` | User management and system configuration | Admin role only |

The bundled [`deploy/isomer.zoleb.com.conf`](deploy/isomer.zoleb.com.conf) nginx vhost terminates TLS, applies a strict rate limit to `/login`, and stamps the usual response headers (CSP, HSTS, XFO, X-Content-Type-Options, Referrer-Policy, Permissions-Policy). For any other reverse proxy, forward everything to `127.0.0.1:27001` and set `X-Forwarded-For` / `X-Real-IP` so the app's login rate limiter keys off real client addresses.

## Production deployment

```bash
sudo install -o root -g root -m 0644 deploy/isomer.zoleb.com.conf \
  /etc/nginx/sites-available/isomer.zoleb.com.conf
sudo ln -sf /etc/nginx/sites-available/isomer.zoleb.com.conf \
  /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

The vhost expects `/etc/nginx/conf.d/hardening.conf` to define a `limit_req_zone zone=general:10m rate=10r/s` keyed on `$http_cf_connecting_ip`; adjust the zone name in the vhost if your setup differs.

## Users

The bootstrap `admin` user is seeded on the first boot against an empty users table with a random password printed to stderr once. After that, users are managed from the admin portal (`/admin` → Settings → Users). Delete the bootstrap account once you have your own admin accounts created.

| Role | Capabilities |
|------|--------------|
| Reporter | Read-only: dashboard, companies, controls, evidence, reports |
| Auditor | Reporter + edit control status/notes/assignment/tags, upload/delete evidence, contact CRUD |
| Admin | Auditor + company create/delete/import, user CRUD, settings |

## Configuration

| Variable | Where it's set | Description |
|----------|----------------|-------------|
| `ISOMER_SECRET` | `.env` (gitignored) | Flask session secret. Required — the app refuses to start without it. Generate with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. Also used as the CSRF signing key. |
| `ISOMER_BOOTSTRAP_PASSWORD` | `.env` (optional) | Picks the password for the initial `admin` user on the very first boot (when the users table is empty). Omit to have the app generate and log a random one. Ignored after first boot. |
| `ISOMER_DATA` | `docker-compose.yml` | Data directory inside the container. Defaults to `/data`. |

## Security notes

- The app binds only to `127.0.0.1:27001`; TLS and response-header hardening live on the nginx reverse proxy in front of it.
- All state-changing POST routes are CSRF-protected via Flask-WTF. The logout action is also a POST (a hidden form in the top bar) so it can't be triggered by an `<img src="/logout">` on a hostile page.
- `/login` has an in-memory rate limit of 6 attempts per 5 minutes per `(username, client IP)`. nginx does a coarser per-IP limit on top of that.
- Evidence uploads in `svg`, `html`, `htm`, `xhtml`, and `xml` formats are served as attachments rather than inline, so a hostile `<svg onload>` can't turn into a stored XSS for anyone else who opens the file.
- Company import extracts zip entries into `/data/uploads/<company_id>/` and refuses any entry whose resolved path escapes that directory (zip-slip defense).
- Session cookies are `HttpOnly`, `Secure`, `SameSite=Lax`; requests whose session no longer matches a live user row get their cookie cleared and are bounced to `/login` on the next request, so admin deletions and role demotions take effect immediately.
- Multipart bodies are capped at 50 MiB in both the app (`MAX_CONTENT_LENGTH`) and nginx (`client_max_body_size`).

## Data Persistence

All data is stored in the `/data` volume inside the container:
- `/data/isomer.db` — SQLite database
- `/data/uploads/` — Evidence files organized by company ID

The `docker-compose.yml` maps this to a named volume `isomer_data` for persistence across container restarts.

## Control Data

Each control includes:
- **Control ID & Title** — Standard identifier and name
- **Description** — Official control requirement
- **Detailed Explanation** — What the control means in practice
- **Real-World Application** — Concrete implementation examples
- **What It Aids** — Why this control matters
- **Challenge Level** — Low / Medium / High implementation difficulty
- **Affected Teams** — Which departments are involved
- **Likely Stakeholders** — Key people responsible
- **Tags** — Searchable categorization
