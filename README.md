# Isomer — Compliance Tracking Platform

**Version:** Alpha

Isomer is a Dockerized, browser-based compliance tracking tool for **ISO 27001** and **SOC 2** audits. It provides a complete workflow for managing controls, uploading evidence, assigning ownership, and generating audit reports.

---

## Quick Start

```bash
# First-time setup: create a .env with a real secret
cp .env.example .env
python3 -c "import secrets; print('ISOMER_SECRET=' + secrets.token_urlsafe(48))" > .env

# Build and run
docker compose up -d --build

# Access the application
# User view:   http://localhost:27001/
# Admin view:  http://localhost:27001/admin   (admin role required)

# Default login
# Username: admin
# Password: admin
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
├── app.py              # Flask application (routes, DB, auth)
├── entrypoint.py       # Launches Flask on port 27001
├── requirements.txt    # Python dependencies
├── Dockerfile          # Container build
├── docker-compose.yml  # Compose deployment
├── .env.example        # Template for ISOMER_SECRET (real .env is gitignored)
├── data/
│   ├── iso27001_controls.json   # 93 Annex A controls with detailed metadata
│   └── soc2_controls.json       # 44 SOC 2 criteria with detailed metadata
└── templates/
    ├── base.html         # Layout, nav, CSS design system
    ├── login.html        # Authentication page
    ├── dashboard.html    # Company overview with stats
    ├── company_form.html # New company creation
    ├── company_view.html # Controls listing, filters, contacts
    ├── control_view.html # Control detail, evidence, notes, assignment
    ├── settings.html     # User management (admin only)
    └── report_view.html  # In-browser audit report
```

## Ports & Routing

The application listens on a single port. Admin tools are exposed via a path prefix and gated by role, not by network port.

| Port | Purpose |
|------|---------|
| **27001** | Entire application (user views and admin tools) |

| Path | Purpose | Access |
|------|---------|--------|
| `/` | Dashboard, companies, controls, evidence, reports | All authenticated users (capabilities filtered by role) |
| `/admin` | Admin portal — redirects to `/settings` | Admin role only |
| `/settings` | User management and system configuration | Admin role only |

Reverse proxies (e.g. nginx in front of the container) should forward everything to `127.0.0.1:27001`.

## Default Users

| Username | Password | Role |
|----------|----------|------|
| admin | admin | Admin (full access) |

Additional users can be created from the admin portal at `/admin` (admin role required). Change the default password immediately after first login.

## Configuration

| Variable | Where it's set | Description |
|----------|----------------|-------------|
| `ISOMER_SECRET` | `.env` (gitignored) | Flask session secret. Generate with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. Never commit a real value. |
| `ISOMER_DATA` | `docker-compose.yml` | Data directory inside the container. Defaults to `/data`. |

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
