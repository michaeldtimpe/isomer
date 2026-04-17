# Isomer — Architecture Document

**Version:** Alpha
**Last Updated:** April 2026 (security hardening pass)

---

## 1. System Overview

Isomer is a self-contained, Dockerized compliance tracking platform designed to manage ISO 27001 and SOC 2 audit engagements. The entire system runs inside a single Docker container serving HTTP on `127.0.0.1:27001`; a reverse proxy (nginx in the reference deployment) sits in front of it to terminate TLS and apply security headers. Both the user dashboard and the admin portal are served from the same process — admin tools are exposed under the `/admin` path prefix and gated by role, not by network port. All state is persisted to a mounted volume, making the system portable and trivially backed up.

The system follows a traditional server-rendered architecture: a Python/Flask backend serves HTML pages directly to the browser with no frontend build step, no JavaScript framework, and no external service dependencies. This was a deliberate choice to minimize operational complexity for a tool that will typically be run on a single machine or internal server.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Docker Container                      │
│                                                         │
│  ┌──────────────────────────────────────────┐           │
│  │          Flask App (Port 27001)          │           │
│  │                                          │           │
│  │  /            → Dashboard (user view)    │           │
│  │  /company/*   → Controls, evidence, …    │           │
│  │  /admin       → redirects to /settings   │           │
│  │  /settings/*  → User mgmt (admin only)   │           │
│  └────────────────┬─────────────────────────┘           │
│                   │                                     │
│             ┌─────▼──────┐                              │
│             │  SQLite DB │                              │
│             │  (WAL mode)│                              │
│             └─────┬──────┘                              │
│                   │                                     │
│             ┌─────▼──────────┐                          │
│             │  /data volume  │  ← Persistent storage    │
│             │  ├── isomer.db │                          │
│             │  └── uploads/  │                          │
│             │      └── {id}/ │  ← Evidence files        │
│             └────────────────┘                          │
│                                                         │
│  ┌────────────────────────┐                             │
│  │  Bundled JSON Data     │  ← Read-only reference      │
│  │  ├── iso27001_controls │     93 Annex A controls     │
│  │  └── soc2_controls     │     44 TSC criteria         │
│  └────────────────────────┘                             │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Component Breakdown

### 3.1 Entrypoint (`entrypoint.py` + gunicorn)

The container's CMD runs `entrypoint.py` once to create `/data/uploads` and then execs `gunicorn --workers 2 --threads 4 --bind 0.0.0.0:27001 app:app`. The app itself binds to `0.0.0.0` inside the container, but Docker publishes it at `127.0.0.1:27001` on the host, so the only reachable path to the app from outside the container is via the reverse proxy. `app.py` no longer calls `app.run()` — Flask's built-in server is a developer convenience, not a production WSGI server. The admin portal is not a separate service — it's a path (`/admin`) on the same Flask app, gated by the existing `@role_required("admin")` decorators.

An earlier revision of Isomer ran two Flask processes on ports 27001 and 27000 to separate the "user" and "settings" UIs. That design was removed because the two processes shared the same codebase, database, and auth — the only behavioral difference was a template flag. The flag is now derived from `session.role == "admin"`, so admin-only UI hides consistently for non-admins whether they land on `/` or `/admin`.

### 3.2 Flask Application (`app.py`)

A single ~910-line Python file containing all backend logic. The application is organized into clearly delimited sections:

**App Setup** — Flask initialization, a required (no-fallback) `ISOMER_SECRET` check that raises at import time if the env var is missing, explicit session-cookie hardening (`Secure`, `HttpOnly`, `SameSite=Lax`, 8h lifetime), `MAX_CONTENT_LENGTH=50 MiB` for uploads, and `CSRFProtect(app)` from Flask-WTF so every state-changing request is token-verified. Also wires the custom `from_json` Jinja2 filter for deserializing JSON strings stored in SQLite.

**Database Layer** — Connection factory (`get_db()`) returning `sqlite3.Row`-based connections with WAL journaling and foreign key enforcement. Schema initialization (`init_db()`) creates six tables on first run. If the `users` table is empty it seeds a single `admin` account: password comes from `ISOMER_BOOTSTRAP_PASSWORD` if set, otherwise from `secrets.token_urlsafe(18)`. The chosen credential is written to stderr once and never logged again. There is no built-in `admin / admin` default.

**Authentication** — Two decorators: `login_required` checks for a session cookie, `role_required(min_role)` enforces a hierarchical permission model where admin > auditor > reporter (mapped to integers 3 > 2 > 1). Session data stores user ID, username, display name, and role. The `update_session_activity` before-request hook re-reads the user's row on every request: if the row is gone, or the DB role no longer matches what's in the session cookie, the server-side session and client cookie are both cleared and the request is bounced to `/login`. This closes the window where a deleted or demoted user keeps working until their cookie expires.

**Login rate limit** — An in-memory sliding-window limiter (`_register_login_attempt`) rejects more than 6 attempts per 5 minutes per `(username_lower, client_ip)`. `_client_ip` honors `X-Forwarded-For` / `X-Real-IP` from the proxy so the key is the real visitor, not loopback. nginx does a coarser `limit_req zone=general burst=5 nodelay` on `/login` on top.

**Control Data Loader** (lines 192–204) — `load_framework_controls(framework)` reads the bundled JSON files from `/app/data/` and returns a list of control dictionaries. Called once per framework when a new company is created.

**Route Groups:**

| Group | Routes | Auth Level | Purpose |
|-------|--------|------------|---------|
| Auth | `/login`, `/logout` | Public | Session-based login with Werkzeug password hashing |
| Dashboard | `/` | Any user | Company listing with aggregate status counts |
| Company CRUD | `/company/new`, `/company/<id>`, `/company/<id>/delete` | Admin (create/delete), Any (view) | Company creation populates controls from JSON reference data |
| Import/Export | `/company/<id>/export`, `/company/import` | Any (export), Admin (import) | ZIP archive containing `company_data.json` + evidence files with ID remapping on import |
| Control Detail | `/control/<id>`, `/control/<id>/edit` | Any (view), Auditor+ (edit) | View/edit status, notes, assignment, tags, prior-evidence flag |
| Evidence | `/control/<id>/upload`, `/evidence/<id>/view`, `/evidence/<id>/delete` | Auditor+ (upload/delete), Any (view) | Multi-file upload with extension validation, inline browser viewing |
| Contacts | `/company/<id>/contact/add`, `/contact/<id>/delete` | Auditor+ | Company contact management |
| Reports | `/company/<id>/report`, `/company/<id>/report/view` | Any | ZIP export (Markdown report + evidence organized by framework/section/control) and in-browser HTML report with print CSS |
| Settings | `/settings`, `/settings/user/*` | Admin | User CRUD (add, edit, delete) |
| API | `/api/search/<id>`, `/api/control/<id>/tags` | Varies | JSON endpoints for search and tag updates |

### 3.3 Database Schema

SQLite with WAL mode for concurrent read performance. Six tables:

```
users
├── id              TEXT PK (UUID)
├── username        TEXT UNIQUE
├── password_hash   TEXT (Werkzeug scrypt)
├── display_name    TEXT
├── email           TEXT
└── role            TEXT (admin|auditor|reporter)

companies
├── id              TEXT PK (UUID)
├── name            TEXT
├── description     TEXT
├── frameworks      TEXT (JSON array: ["iso27001","soc2"])
├── engagement_type TEXT (first_time|renewal)
├── created_at      TEXT (ISO 8601)
└── updated_at      TEXT (ISO 8601)

controls
├── id                    TEXT PK (UUID)
├── company_id            TEXT FK → companies.id CASCADE
├── framework             TEXT (iso27001|soc2)
├── control_id            TEXT (e.g. "A.5.1", "CC1.1")
├── section               TEXT (e.g. "A5 - Organizational Controls")
├── title                 TEXT
├── description           TEXT
├── detailed_explanation  TEXT
├── real_world_application TEXT
├── what_it_aids          TEXT
├── challenge_level       TEXT (low|medium|high)
├── affected_teams        TEXT (JSON array)
├── likely_stakeholders   TEXT (JSON array)
├── status                TEXT (new|in_progress|stalled|closed)
├── notes                 TEXT
├── assigned_name         TEXT
├── assigned_email        TEXT
├── tags                  TEXT (JSON array)
├── prior_evidence_valid  INTEGER (0|1)
├── created_at            TEXT (ISO 8601)
└── updated_at            TEXT (ISO 8601)

evidence
├── id                TEXT PK (UUID)
├── control_id        TEXT FK → controls.id CASCADE
├── company_id        TEXT FK → companies.id CASCADE
├── filename          TEXT (UUID-based stored filename)
├── original_filename TEXT (user-facing name)
├── file_type         TEXT (extension)
├── file_size         INTEGER (bytes)
├── description       TEXT
├── uploaded_by       TEXT
└── uploaded_at       TEXT (ISO 8601)

contacts
├── id          TEXT PK (UUID)
├── company_id  TEXT FK → companies.id CASCADE
├── name        TEXT
├── email       TEXT
├── phone       TEXT
└── department  TEXT

settings
├── key    TEXT PK
└── value  TEXT
```

Foreign keys use `ON DELETE CASCADE` so deleting a company removes all its controls, evidence metadata, and contacts. Evidence files on disk are cleaned up separately in application code.

JSON arrays (frameworks, affected_teams, likely_stakeholders, tags) are stored as serialized JSON strings in TEXT columns and deserialized at the template layer using the custom `from_json` Jinja2 filter.

### 3.4 File Storage

Evidence files are stored on disk under `/data/uploads/{company_id}/{uuid_hex}.{ext}`. The original filename is preserved in the database but not used on disk, avoiding collisions and path traversal issues. `werkzeug.utils.secure_filename` sanitizes the original name. The allowed extension whitelist rejects unexpected file types.

`evidence_view` forces `Content-Disposition: attachment` for any original filename ending in `svg`, `html`, `htm`, `xhtml`, or `xml` — those formats execute inline in the origin's security context when served with their default Content-Type, and serving them inline would let a hostile auditor plant `<svg onload=...>` as stored XSS. All other types are still served inline for the normal preview experience.

`company_import` extracts zip entries into `/data/uploads/{new_company_id}/`. Before writing, each entry is validated: the relative path cannot contain `..` components or start with `/`, and the resolved destination must live under the target directory. Entries that fail either check are silently skipped. This closes the classic zip-slip path where a crafted archive (`evidence/../../etc/cron.d/...`) could write outside the intended upload area.

### 3.5 Templates

Eight Jinja2 templates with a shared base layout:

- `base.html` — Full CSS design system (dark theme, DM Sans + JetBrains Mono typography, CSS custom properties), sticky top navigation bar with role badge, flash message rendering, accordion JavaScript, and all shared UI components (cards, buttons, forms, tables, status badges, tags, progress bars, stats grid).
- `login.html` — Standalone page (does not extend base) with centered login form.
- `dashboard.html` — Company cards with progress bars, aggregate statistics, and import button.
- `company_form.html` — New company creation with framework checkboxes and engagement type radios.
- `company_view.html` — Control listing with five-dimension filter bar (search, section, status, framework, tag), stats grid, contacts table with inline add form.
- `control_view.html` — Two-column layout. Left: description, expandable detail accordions, evidence grid with thumbnails and upload. Right: edit form for status, assignment, tags, notes, prior-evidence flag.
- `settings.html` — User table with inline edit/delete, add user form, system info panel.
- `report_view.html` — Printable audit report with print-specific CSS overrides, controls grouped by section with evidence thumbnails.

### 3.6 Bundled Control Data

Two JSON files in `/app/data/` provide the reference control definitions:

`iso27001_controls.json` — 93 controls from ISO 27001:2022 Annex A, organized into four themes: Organizational (A.5, 37 controls), People (A.6, 8 controls), Physical (A.7, 14 controls), and Technological (A.8, 34 controls). Includes all 11 controls new to the 2022 revision (A.5.7, A.5.23, A.5.30, A.7.4, A.8.9, A.8.10, A.8.11, A.8.12, A.8.16, A.8.23, A.8.28).

`soc2_controls.json` — 44 criteria covering the mandatory Common Criteria (CC1–CC9) and optional Trust Services Categories: Availability (A1), Processing Integrity (PI1), Confidentiality (C1), and Privacy (P1).

Each control record includes: `control_id`, `section`, `title`, `description`, `detailed_explanation`, `real_world_application`, `what_it_aids`, `challenge_level`, `affected_teams`, `likely_stakeholders`, and `tags`.

---

## 4. Data Flow

### 4.1 Company Creation Flow

```
User selects frameworks (ISO 27001, SOC 2, or both)
    → POST /company/new
    → Insert company row
    → For each framework:
        → load_framework_controls() reads JSON file
        → Insert one controls row per control (93 + 44 = 137 max)
    → Redirect to /company/{id}
```

### 4.2 Evidence Upload Flow

```
User selects files on control detail page
    → POST /control/{id}/upload (multipart/form-data)
    → For each file:
        → Validate extension against whitelist
        → Generate UUID-based filename
        → Save to /data/uploads/{company_id}/{uuid}.{ext}
        → Insert evidence metadata row
    → Redirect to /control/{id}
```

### 4.3 Export/Import Flow

```
Export:
    → GET /company/{id}/export
    → Query all company data (company, controls, evidence, contacts)
    → Build ZIP containing:
        ├── company_data.json (full JSON dump)
        └── evidence/ (all uploaded files)
    → Stream ZIP as download

Import:
    → POST /company/import (ZIP file)
    → Parse company_data.json
    → Generate new UUIDs for company, all controls, all evidence
    → Build old_id → new_id mapping for control references
    → Insert all rows with remapped IDs
    → Extract evidence files to /data/uploads/{new_company_id}/
```

### 4.4 Report Generation Flow

```
ZIP Report:
    → GET /company/{id}/report (optional ?section= or ?control_id= filters)
    → Query controls (filtered or all)
    → Build Markdown report with status summary, per-control detail, evidence references
    → Copy evidence files into ZIP under evidence/{framework}/{section}/{control_id}/
    → Stream ZIP as download

In-Browser Report:
    → GET /company/{id}/report/view
    → Query all controls + evidence
    → Render report_view.html with print CSS
    → User can Print → Save as PDF from browser
```

---

## 5. Security Model

Isomer has no network-level auth (no Cloudflare Access, no mTLS) — authentication is entirely in-process via Flask session cookies. That pushes weight onto four things: the cookie signing key, the cookie flags, the CSRF tokens, and the reverse proxy in front of the app. Any of those breaking degrades the whole system.

### 5.1 Authentication

Session-based authentication using Flask's signed cookie sessions. Passwords are hashed using Werkzeug's `generate_password_hash` (scrypt by default). `ISOMER_SECRET` is **required** — the app raises at import time if it's unset, so a missing env var can't silently downgrade to a known default key. The secret signs both the session cookie and Flask-WTF CSRF tokens.

Cookies are set with:

| Flag | Value | Why |
|------|-------|-----|
| `HttpOnly` | `True` | JavaScript (including uploaded SVG/HTML) can't read the cookie |
| `Secure` | `True` | Browser refuses to send the cookie over plaintext HTTP |
| `SameSite` | `Lax` | Blocks cross-site POST delivery; still allows top-level navigation |
| `PERMANENT_SESSION_LIFETIME` | 8h | Bounds a stolen-cookie window |

### 5.2 CSRF protection

Every POST route goes through Flask-WTF's `CSRFProtect`. Templates render `{{ csrf_token() }}` as a hidden input in each form. A `CSRFError` handler flashes a friendly message and bounces back rather than returning an opaque 400. `/logout` is a POST form, not a GET link, so an `<img src="/logout">` on a hostile page can't terminate the admin's session.

### 5.3 Authorization

Three-tier role hierarchy enforced at the route level by decorators:

| Role | Level | Capabilities |
|------|-------|-------------|
| **Reporter** | 1 | View dashboard, companies, controls, evidence, reports. Read-only access to all data. |
| **Auditor** | 2 | Everything Reporter can do, plus: edit control status/notes/assignment/tags, upload/delete evidence, add/delete contacts. |
| **Admin** | 3 | Everything Auditor can do, plus: create/delete companies, import companies, manage users (add/edit/delete), access settings page. |

The `role_required(min_role)` decorator compares the session role against the required minimum using integer levels. Routes serving data to any authenticated user use `@login_required`. Routes requiring write access use `@role_required("auditor")`. Administrative routes use `@role_required("admin")`. The per-request hook re-reads the DB, so admin deletions or role demotions take effect immediately.

### 5.4 Login rate limiting

An in-memory sliding window keyed on `(username_lower, client_ip)` caps attempts at 6 per 5 minutes. Rejections return HTTP 429 and a flash message. The limiter is process-local; with two gunicorn workers the effective ceiling is 12 per 5 minutes per target, which is still well below what a meaningful brute force would need. nginx also has a `limit_req` in front of `/login` as coarse per-IP defense in depth.

### 5.5 File upload security

- Extension whitelist (26 allowed types) rejects unexpected file types.
- `werkzeug.utils.secure_filename` strips path separators and special characters from filenames.
- Files are stored with UUID-based names, eliminating path traversal and collision risks.
- Original filenames preserved only in the database for display.
- `MAX_CONTENT_LENGTH = 50 MiB` at the app, matched by `client_max_body_size 50M` at nginx.
- Evidence files in inline-executable formats (`svg`, `html`, `htm`, `xhtml`, `xml`) are served as downloads, not inline.
- Company-import zip entries are path-validated before extraction (zip-slip defense).

### 5.6 Reverse proxy responsibilities

The provided `deploy/isomer.zoleb.com.conf` vhost handles everything the app doesn't: TLS termination, HTTP/2, `client_max_body_size`, a per-IP rate limit, and response headers:

| Header | Value |
|--------|-------|
| `Content-Security-Policy` | `default-src 'self'` base with `'unsafe-inline'` for style/script (templates use inline `<style>`/`<script>`; tighten when externalized) |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` |
| `X-Frame-Options` | `DENY` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `no-referrer` |
| `Permissions-Policy` | sensors/cameras/microphones/payments all `()` |

### 5.7 Threat model summary

Assumes an attacker without valid credentials can reach `/login` over TLS, but cannot reach the loopback-bound Flask socket directly. Under those assumptions, the remaining attack surface is: the login form (rate-limited), bootstrap password visibility (printed once to stderr, rotate after first login), and code-level issues (CSRF, zip-slip, inline XSS) all covered above. A compromised auditor account can still vandalize data within the tool, but cannot escalate to admin, forge another user's session, or plant stored XSS in the evidence viewer.

---

## 6. Deployment

### 6.1 Docker

The Dockerfile uses `python:3.11-slim` as the base image with SQLite3 installed. The application code is copied to `/app`. A persistent volume is mounted at `/data` for the database and evidence files. The container CMD is a shell wrapper that first runs `python entrypoint.py` (to ensure `/data/uploads` exists) and then execs gunicorn:

```
python entrypoint.py && exec gunicorn --workers 2 --threads 4 \
    --bind 0.0.0.0:27001 --access-logfile - --error-logfile - app:app
```

### 6.2 Docker Compose

```yaml
services:
  isomer:
    build: .
    ports:
      - "127.0.0.1:27001:27001"  # loopback-only — nginx is the only ingress
    volumes:
      - isomer_data:/data
    env_file:
      - .env
    environment:
      - ISOMER_DATA=/data
```

### 6.3 nginx reverse proxy

`deploy/isomer.zoleb.com.conf` is the vhost used in the reference deployment. It terminates TLS (Cloudflare origin cert), enables HTTP/2, applies `client_max_body_size 50M`, a stricter rate limit on `/login`, and the response headers listed in §5.6. Install:

```bash
sudo install -o root -g root -m 0644 deploy/isomer.zoleb.com.conf \
  /etc/nginx/sites-available/isomer.zoleb.com.conf
sudo ln -sf /etc/nginx/sites-available/isomer.zoleb.com.conf \
  /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 6.4 Environment Variables

| Variable | Source | Purpose |
|----------|--------|---------|
| `ISOMER_SECRET` | `.env` (gitignored, `chmod 600`) | Flask session + CSRF signing key. **Required** — app raises at import if unset. Generate with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `ISOMER_BOOTSTRAP_PASSWORD` | `.env` (optional) | Picks the first-boot `admin` password when the `users` table is empty. If unset, the app generates one and prints it once to stderr. |
| `ISOMER_DATA` | `docker-compose.yml` | Path to persistent data directory. Defaults to `/data`. |

---

## 7. Technology Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Language | Python 3.11 | Wide ecosystem, rapid development, team familiarity |
| Web framework | Flask | Lightweight, no ORM overhead, suitable for a single-file application |
| Database | SQLite (WAL) | Zero-configuration, file-based, portable within Docker volume, sufficient for single-user/small-team workloads |
| Templating | Jinja2 (server-rendered) | No build step, no JavaScript framework, instant page loads, print-friendly |
| Auth | Session cookies + scrypt hashing | Simple, stateless from server perspective, secure password storage |
| File storage | Filesystem (UUID naming) | Avoids database bloat, simple to back up via volume mount |
| Containerization | Docker + Compose | Portability, reproducibility, simple deployment |
| ID generation | UUID v4 | Globally unique, no auto-increment coordination needed |

---

## 8. Limitations and Future Considerations

**Current limitations:**
- Single Docker host, single gunicorn instance — adequate for small teams but not horizontally scaled.
- TLS is not terminated inside the container; requires a reverse proxy (nginx vhost provided).
- No audit logging of user actions (who changed what, when).
- No email notifications for assignments or status changes.
- SQLite does not support concurrent writes well — fine for small teams, would need PostgreSQL for larger deployments.
- Login rate limit is in-memory per worker, so a restart or scale-out resets it. Acceptable given the `(username, client_ip)` key and nginx's per-IP backstop, but not a substitute for a shared store at higher traffic.
- No pagination on controls listing — works fine for 137 controls but would need pagination if custom controls are added.

**Potential future enhancements:**
- Externalize template `<style>` and `<script>` blocks so CSP can drop `'unsafe-inline'`.
- PostgreSQL option for multi-user concurrency.
- Audit trail table recording all mutations.
- Email integration for assignment notifications.
- LDAP/SSO integration for enterprise authentication.
- API tokens for programmatic access.
- Custom control definitions beyond ISO 27001 and SOC 2.
- Evidence versioning and approval workflows.
