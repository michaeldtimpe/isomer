# Isomer — Architecture Document

**Version:** Alpha
**Last Updated:** April 2026

---

## 1. System Overview

Isomer is a self-contained, Dockerized compliance tracking platform designed to manage ISO 27001 and SOC 2 audit engagements. The entire system runs inside a single Docker container, exposing a single HTTP port (27001) that serves both the user dashboard and the admin portal. Admin tools are exposed under the `/admin` path prefix and gated by role, not by network port. All state is persisted to a mounted volume, making the system portable and trivially backed up.

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

### 3.1 Entrypoint (`entrypoint.py`)

The container's CMD runs `entrypoint.py`, which imports `app.py` and calls `app.run(host="0.0.0.0", port=27001)`. Single process, single port. The admin portal is not a separate service — it's a path (`/admin`) on the same Flask app, gated by the existing `@role_required("admin")` decorators.

An earlier revision of Isomer ran two Flask processes on ports 27001 and 27000 to separate the "user" and "settings" UIs. That design was removed because the two processes shared the same codebase, database, and auth — the only behavioral difference was a template flag. The flag is now derived from `session.role == "admin"`, so admin-only UI hides consistently for non-admins whether they land on `/` or `/admin`.

### 3.2 Flask Application (`app.py`)

A single ~910-line Python file containing all backend logic. The application is organized into clearly delimited sections:

**App Setup** (lines 28–50) — Flask initialization, secret key configuration, data directory paths, allowed file extension whitelist, and the custom `from_json` Jinja2 template filter for deserializing JSON strings stored in SQLite.

**Database Layer** (lines 53–154) — Connection factory (`get_db()`) returning `sqlite3.Row`-based connections with WAL journaling and foreign key enforcement. Schema initialization (`init_db()`) creates six tables on first run and seeds a default admin user. Called at module import time so the database is always ready.

**Authentication** (lines 156–188) — Two decorators: `login_required` checks for a session cookie, `role_required(min_role)` enforces a hierarchical permission model where admin > auditor > reporter (mapped to integers 3 > 2 > 1). Session data stores user ID, username, display name, and role.

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

### 5.1 Authentication

Session-based authentication using Flask's signed cookie sessions. Passwords are hashed using Werkzeug's `generate_password_hash` (scrypt by default). The session secret key is configurable via `ISOMER_SECRET` environment variable.

### 5.2 Authorization

Three-tier role hierarchy enforced at the route level by decorators:

| Role | Level | Capabilities |
|------|-------|-------------|
| **Reporter** | 1 | View dashboard, companies, controls, evidence, reports. Read-only access to all data. |
| **Auditor** | 2 | Everything Reporter can do, plus: edit control status/notes/assignment/tags, upload/delete evidence, add/delete contacts. |
| **Admin** | 3 | Everything Auditor can do, plus: create/delete companies, import companies, manage users (add/edit/delete), access settings page. |

The `role_required(min_role)` decorator compares the session role against the required minimum using integer levels. Routes serving data to any authenticated user use `@login_required`. Routes requiring write access use `@role_required("auditor")`. Administrative routes use `@role_required("admin")`.

### 5.3 File Upload Security

- Extension whitelist (26 allowed types) rejects unexpected file types
- `werkzeug.utils.secure_filename` strips path separators and special characters from filenames
- Files are stored with UUID-based names, eliminating path traversal and collision risks
- Original filenames preserved only in the database for display

---

## 6. Deployment

### 6.1 Docker

The Dockerfile uses `python:3.11-slim` as the base image with SQLite3 installed. The application code is copied to `/app`. A persistent volume is mounted at `/data` for the database and evidence files.

### 6.2 Docker Compose

```yaml
services:
  isomer:
    build: .
    ports:
      - "27001:27001"
    volumes:
      - isomer_data:/data
    env_file:
      - .env
    environment:
      - ISOMER_DATA=/data
```

### 6.3 Environment Variables

| Variable | Source | Purpose |
|----------|--------|---------|
| `ISOMER_SECRET` | `.env` (gitignored) | Flask session signing key. Never committed. Generate with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` and store in `.env` for compose to read via `env_file`. |
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
- Single-process Flask server (not production WSGI like Gunicorn) — adequate for small teams but would need a proper WSGI server for heavier load
- No HTTPS termination (assumes a reverse proxy or VPN in front)
- No audit logging of user actions (who changed what, when)
- No email notifications for assignments or status changes
- SQLite does not support concurrent writes well — fine for small teams, would need PostgreSQL for larger deployments
- No pagination on controls listing — works fine for 137 controls but would need pagination if custom controls are added
- Session secret should be rotated and stored securely in production

**Potential future enhancements:**
- Gunicorn/uWSGI for production serving
- PostgreSQL option for multi-user concurrency
- Audit trail table recording all mutations
- Email integration for assignment notifications
- LDAP/SSO integration for enterprise authentication
- API tokens for programmatic access
- Custom control definitions beyond ISO 27001 and SOC 2
- Evidence versioning and approval workflows
