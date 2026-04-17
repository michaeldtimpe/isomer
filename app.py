"""
Isomer — Compliance Tracking Platform
Version: Alpha
"""

import os
import sys
import json
import secrets
import time
import uuid
import shutil
import zipfile
import io
import datetime
from collections import defaultdict, deque
from functools import wraps
from pathlib import Path
from threading import Lock

from flask import (
    Flask, render_template, request, redirect, url_for, session,
    jsonify, send_file, flash, abort, Response
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3

# ---------------------------------------------------------------------------
# App Setup
# ---------------------------------------------------------------------------

_secret = os.environ.get("ISOMER_SECRET")
if not _secret:
    raise RuntimeError(
        "ISOMER_SECRET is not set. Refusing to start with a default/empty key — "
        "anyone who knew the fallback could forge session cookies. Set ISOMER_SECRET "
        "(e.g. `openssl rand -base64 48`) in the container environment and retry."
    )

app = Flask(__name__, template_folder="templates")
app.secret_key = _secret
app.config.update(
    # Reject multipart bodies larger than 50 MiB (evidence uploads, imports).
    MAX_CONTENT_LENGTH=50 * 1024 * 1024,
    # Cookie hardening.
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=8),
)

# Custom Jinja2 filter to parse JSON strings
@app.template_filter('from_json')
def from_json_filter(value):
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []

DATA_DIR = Path(os.environ.get("ISOMER_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")))
DB_PATH = DATA_DIR / "isomer.db"
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "bmp", "webp", "svg",
    "pdf", "doc", "docx", "xls", "xlsx", "csv", "txt",
    "log", "json", "xml", "yaml", "yml", "zip", "gz", "tar",
    "msg", "eml", "html", "md"
}

ROLES = {"admin": 3, "auditor": 2, "reporter": 1}

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        display_name TEXT,
        email TEXT,
        role TEXT NOT NULL DEFAULT 'reporter'
    );

    CREATE TABLE IF NOT EXISTS companies (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        frameworks TEXT NOT NULL DEFAULT '[]',
        engagement_type TEXT NOT NULL DEFAULT 'first_time',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS contacts (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        name TEXT NOT NULL,
        email TEXT,
        phone TEXT,
        department TEXT,
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS controls (
        id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        framework TEXT NOT NULL,
        control_id TEXT NOT NULL,
        section TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        detailed_explanation TEXT,
        real_world_application TEXT,
        what_it_aids TEXT,
        challenge_level TEXT DEFAULT 'medium',
        affected_teams TEXT DEFAULT '[]',
        likely_stakeholders TEXT DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'new',
        notes TEXT DEFAULT '',
        assigned_name TEXT,
        assigned_email TEXT,
        tags TEXT DEFAULT '[]',
        prior_evidence_valid INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS evidence (
        id TEXT PRIMARY KEY,
        control_id TEXT NOT NULL,
        company_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        original_filename TEXT NOT NULL,
        file_type TEXT,
        file_size INTEGER,
        description TEXT,
        uploaded_by TEXT,
        uploaded_at TEXT NOT NULL,
        FOREIGN KEY (control_id) REFERENCES controls(id) ON DELETE CASCADE,
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        username TEXT NOT NULL,
        display_name TEXT,
        logged_in_at TEXT NOT NULL,
        last_active_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """)
    # First-boot bootstrap: seed a single admin user with a random password
    # (or the operator-supplied ISOMER_BOOTSTRAP_PASSWORD) and print it once
    # to stderr. We never ship a fixed `admin/admin` credential — anyone
    # who knew that default could walk straight in.
    cur = conn.execute("SELECT COUNT(*) as c FROM users")
    if cur.fetchone()["c"] == 0:
        bootstrap_pw = os.environ.get("ISOMER_BOOTSTRAP_PASSWORD") or secrets.token_urlsafe(18)
        conn.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), "admin", generate_password_hash(bootstrap_pw), "Administrator", "admin")
        )
        sys.stderr.write(
            "\n" + "=" * 62 + "\n"
            "  ISOMER — bootstrap admin credentials (printed once):\n"
            "    username: admin\n"
            f"    password: {bootstrap_pw}\n"
            "  Log in and change this password from Settings → Users.\n"
            + "=" * 62 + "\n\n"
        )
        sys.stderr.flush()
    conn.commit()
    conn.close()


init_db()

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def role_required(min_role):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            user_role = session.get("role", "reporter")
            if ROLES.get(user_role, 0) < ROLES.get(min_role, 0):
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator


def now_iso():
    return datetime.datetime.utcnow().isoformat() + "Z"


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# Session activity tracking — throttle stale cleanup to once per minute
_last_session_cleanup = [0.0]

@app.before_request
def update_session_activity():
    sid = session.get("session_id")
    if not sid:
        return
    import time
    now = time.time()
    conn = get_db()
    conn.execute("UPDATE sessions SET last_active_at=? WHERE id=?", (now_iso(), sid))
    # Clean up stale sessions (>30 min) at most once per minute
    if now - _last_session_cleanup[0] > 60:
        _last_session_cleanup[0] = now
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(minutes=30)).isoformat() + "Z"
        conn.execute("DELETE FROM sessions WHERE last_active_at < ?", (cutoff,))
    conn.commit()
    conn.close()

# ---------------------------------------------------------------------------
# Control data loader (from JSON reference files)
# ---------------------------------------------------------------------------

def load_framework_controls(framework):
    """Load controls from bundled JSON reference files."""
    base = Path(__file__).parent / "data"
    if framework == "iso27001":
        path = base / "iso27001_controls.json"
    elif framework == "soc2":
        path = base / "soc2_controls.json"
    else:
        return []
    with open(path) as f:
        return json.load(f)


def load_cross_framework_map():
    """Load cross-framework control mapping (bidirectional lookup)."""
    path = Path(__file__).parent / "data" / "cross_framework_map.json"
    if not path.exists():
        return {}
    with open(path) as f:
        raw = json.load(f)
    mapping = {}
    for entry in raw:
        s, i, rel = entry["soc2"], entry["iso27001"], entry.get("relationship", "related")
        mapping.setdefault(s, []).append({"control_id": i, "framework": "iso27001", "relationship": rel})
        mapping.setdefault(i, []).append({"control_id": s, "framework": "soc2", "relationship": rel})
    return mapping


CROSS_MAP = load_cross_framework_map()

# ---------------------------------------------------------------------------
# Routes — Auth
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["display_name"] = user["display_name"]
            session["role"] = user["role"]
            # Track server-side session
            session_id = str(uuid.uuid4())
            session["session_id"] = session_id
            ts = now_iso()
            conn.execute(
                "INSERT INTO sessions (id, user_id, username, display_name, logged_in_at, last_active_at) VALUES (?,?,?,?,?,?)",
                (session_id, user["id"], user["username"], user["display_name"], ts, ts)
            )
            conn.commit()
            conn.close()
            return redirect(url_for("dashboard"))
        conn.close()
        flash("Invalid credentials", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    sid = session.get("session_id")
    if sid:
        conn = get_db()
        conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
        conn.commit()
        conn.close()
    session.clear()
    return redirect(url_for("login"))

# ---------------------------------------------------------------------------
# Routes — Dashboard
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def dashboard():
    conn = get_db()
    companies = conn.execute("SELECT * FROM companies ORDER BY name").fetchall()
    stats = {}
    for co in companies:
        total = conn.execute("SELECT COUNT(*) as c FROM controls WHERE company_id=?", (co["id"],)).fetchone()["c"]
        closed = conn.execute("SELECT COUNT(*) as c FROM controls WHERE company_id=? AND status='closed'", (co["id"],)).fetchone()["c"]
        in_progress = conn.execute("SELECT COUNT(*) as c FROM controls WHERE company_id=? AND status='in_progress'", (co["id"],)).fetchone()["c"]
        stalled = conn.execute("SELECT COUNT(*) as c FROM controls WHERE company_id=? AND status='stalled'", (co["id"],)).fetchone()["c"]
        new = conn.execute("SELECT COUNT(*) as c FROM controls WHERE company_id=? AND status='new'", (co["id"],)).fetchone()["c"]
        stats[co["id"]] = {"total": total, "closed": closed, "in_progress": in_progress, "stalled": stalled, "new": new}
    conn.close()
    return render_template("dashboard.html", companies=companies, stats=stats)

# ---------------------------------------------------------------------------
# Routes — Company CRUD
# ---------------------------------------------------------------------------

@app.route("/company/new", methods=["GET", "POST"])
@role_required("admin")
def company_new():
    if request.method == "POST":
        cid = str(uuid.uuid4())
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        frameworks = request.form.getlist("frameworks")
        engagement_type = request.form.get("engagement_type", "first_time")
        if not name:
            flash("Company name is required", "error")
            return render_template("company_form.html", company=None)
        if not frameworks:
            flash("At least one framework must be selected", "error")
            return render_template("company_form.html", company=None)
        ts = now_iso()
        conn = get_db()
        conn.execute(
            "INSERT INTO companies (id, name, description, frameworks, engagement_type, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (cid, name, description, json.dumps(frameworks), engagement_type, ts, ts)
        )
        # Populate controls for chosen frameworks
        for fw in frameworks:
            controls = load_framework_controls(fw)
            for ctrl in controls:
                conn.execute(
                    """INSERT INTO controls
                    (id, company_id, framework, control_id, section, title, description,
                     detailed_explanation, real_world_application, what_it_aids,
                     challenge_level, affected_teams, likely_stakeholders,
                     status, notes, tags, prior_evidence_valid, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), cid, fw, ctrl["control_id"], ctrl["section"],
                     ctrl["title"], ctrl["description"],
                     ctrl.get("detailed_explanation", ""),
                     ctrl.get("real_world_application", ""),
                     ctrl.get("what_it_aids", ""),
                     ctrl.get("challenge_level", "medium"),
                     json.dumps(ctrl.get("affected_teams", [])),
                     json.dumps(ctrl.get("likely_stakeholders", [])),
                     "new", "", json.dumps(ctrl.get("tags", [])),
                     0, ts, ts)
                )
        conn.commit()
        conn.close()
        return redirect(url_for("company_view", company_id=cid))
    return render_template("company_form.html", company=None)


@app.route("/company/<company_id>")
@login_required
def company_view(company_id):
    conn = get_db()
    company = conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    if not company:
        abort(404)
    section = request.args.get("section", "")
    search = request.args.get("search", "")
    status_filter = request.args.get("status", "")
    framework_filter = request.args.get("framework", "")
    tag_filter = request.args.get("tag", "")

    query = "SELECT * FROM controls WHERE company_id=?"
    params = [company_id]

    if section:
        query += " AND section=?"
        params.append(section)
    if framework_filter:
        query += " AND framework=?"
        params.append(framework_filter)
    if status_filter:
        query += " AND status=?"
        params.append(status_filter)
    if search:
        query += " AND (title LIKE ? OR description LIKE ? OR notes LIKE ? OR control_id LIKE ? OR tags LIKE ?)"
        s = f"%{search}%"
        params.extend([s, s, s, s, s])
    if tag_filter:
        query += " AND tags LIKE ?"
        params.append(f'%"{tag_filter}"%')

    query += " ORDER BY framework, section, control_id"
    controls = conn.execute(query, params).fetchall()

    # Get unique sections and tags for filters
    all_controls = conn.execute(
        "SELECT DISTINCT section, framework FROM controls WHERE company_id=? ORDER BY framework, section",
        (company_id,)
    ).fetchall()
    sections = list({(c["section"], c["framework"]) for c in all_controls})
    sections.sort()

    all_tags_raw = conn.execute("SELECT tags FROM controls WHERE company_id=?", (company_id,)).fetchall()
    all_tags = set()
    for row in all_tags_raw:
        for t in json.loads(row["tags"] or "[]"):
            all_tags.add(t)
    all_tags = sorted(all_tags)

    # Stats
    total = conn.execute("SELECT COUNT(*) as c FROM controls WHERE company_id=?", (company_id,)).fetchone()["c"]
    closed = conn.execute("SELECT COUNT(*) as c FROM controls WHERE company_id=? AND status='closed'", (company_id,)).fetchone()["c"]
    in_progress = conn.execute("SELECT COUNT(*) as c FROM controls WHERE company_id=? AND status='in_progress'", (company_id,)).fetchone()["c"]
    stalled = conn.execute("SELECT COUNT(*) as c FROM controls WHERE company_id=? AND status='stalled'", (company_id,)).fetchone()["c"]

    contacts = conn.execute("SELECT * FROM contacts WHERE company_id=? ORDER BY name", (company_id,)).fetchall()
    conn.close()

    return render_template("company_view.html",
        company=company, controls=controls, sections=sections,
        all_tags=all_tags, contacts=contacts,
        total=total, closed=closed, in_progress=in_progress, stalled=stalled,
        current_section=section, current_search=search,
        current_status=status_filter, current_framework=framework_filter,
        current_tag=tag_filter,
        mapped_control_ids=set(CROSS_MAP.keys())
    )


@app.route("/company/<company_id>/delete", methods=["POST"])
@role_required("admin")
def company_delete(company_id):
    conn = get_db()
    conn.execute("DELETE FROM evidence WHERE company_id=?", (company_id,))
    conn.execute("DELETE FROM controls WHERE company_id=?", (company_id,))
    conn.execute("DELETE FROM contacts WHERE company_id=?", (company_id,))
    conn.execute("DELETE FROM companies WHERE id=?", (company_id,))
    conn.commit()
    conn.close()
    # Clean up upload dir
    co_upload = UPLOAD_DIR / company_id
    if co_upload.exists():
        shutil.rmtree(co_upload)
    return redirect(url_for("dashboard"))

# ---------------------------------------------------------------------------
# Routes — Export / Import Company
# ---------------------------------------------------------------------------

@app.route("/company/<company_id>/export")
@login_required
def company_export(company_id):
    conn = get_db()
    company = conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    if not company:
        abort(404)

    controls = conn.execute("SELECT * FROM controls WHERE company_id=?", (company_id,)).fetchall()
    evidence = conn.execute("SELECT * FROM evidence WHERE company_id=?", (company_id,)).fetchall()
    contacts = conn.execute("SELECT * FROM contacts WHERE company_id=?", (company_id,)).fetchall()
    conn.close()

    export_data = {
        "isomer_version": "alpha",
        "exported_at": now_iso(),
        "company": dict(company),
        "controls": [dict(c) for c in controls],
        "evidence": [dict(e) for e in evidence],
        "contacts": [dict(c) for c in contacts],
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("company_data.json", json.dumps(export_data, indent=2))
        # Include evidence files
        co_upload = UPLOAD_DIR / company_id
        if co_upload.exists():
            for fpath in co_upload.rglob("*"):
                if fpath.is_file():
                    arcname = f"evidence/{fpath.relative_to(co_upload)}"
                    zf.write(fpath, arcname)
    buf.seek(0)
    safe_name = company["name"].replace(" ", "_")
    return send_file(buf, mimetype="application/zip",
                     as_attachment=True,
                     download_name=f"isomer_export_{safe_name}_{datetime.date.today()}.zip")


@app.route("/company/import", methods=["POST"])
@role_required("admin")
def company_import():
    f = request.files.get("file")
    if not f:
        flash("No file provided", "error")
        return redirect(url_for("dashboard"))
    try:
        with zipfile.ZipFile(io.BytesIO(f.read())) as zf:
            data = json.loads(zf.read("company_data.json"))
            conn = get_db()
            co = data["company"]
            new_id = str(uuid.uuid4())
            ts = now_iso()
            conn.execute(
                "INSERT INTO companies (id, name, description, frameworks, engagement_type, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (new_id, co["name"] + " (imported)", co.get("description", ""),
                 co["frameworks"], co["engagement_type"], ts, ts)
            )
            id_map = {}
            for ctrl in data.get("controls", []):
                old_id = ctrl["id"]
                ctrl_new_id = str(uuid.uuid4())
                id_map[old_id] = ctrl_new_id
                conn.execute(
                    """INSERT INTO controls
                    (id, company_id, framework, control_id, section, title, description,
                     detailed_explanation, real_world_application, what_it_aids,
                     challenge_level, affected_teams, likely_stakeholders,
                     status, notes, tags, assigned_name, assigned_email,
                     prior_evidence_valid, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (ctrl_new_id, new_id, ctrl["framework"], ctrl["control_id"],
                     ctrl["section"], ctrl["title"], ctrl.get("description", ""),
                     ctrl.get("detailed_explanation", ""),
                     ctrl.get("real_world_application", ""),
                     ctrl.get("what_it_aids", ""),
                     ctrl.get("challenge_level", "medium"),
                     ctrl.get("affected_teams", "[]"),
                     ctrl.get("likely_stakeholders", "[]"),
                     ctrl.get("status", "new"),
                     ctrl.get("notes", ""),
                     ctrl.get("tags", "[]"),
                     ctrl.get("assigned_name"),
                     ctrl.get("assigned_email"),
                     ctrl.get("prior_evidence_valid", 0),
                     ts, ts)
                )
            for ev in data.get("evidence", []):
                ev_new_id = str(uuid.uuid4())
                ctrl_new_id = id_map.get(ev["control_id"], ev["control_id"])
                conn.execute(
                    """INSERT INTO evidence (id, control_id, company_id, filename, original_filename,
                       file_type, file_size, description, uploaded_by, uploaded_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (ev_new_id, ctrl_new_id, new_id, ev["filename"], ev["original_filename"],
                     ev.get("file_type"), ev.get("file_size"), ev.get("description"),
                     ev.get("uploaded_by"), ts)
                )
            for ct in data.get("contacts", []):
                conn.execute(
                    "INSERT INTO contacts (id, company_id, name, email, phone, department) VALUES (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), new_id, ct["name"], ct.get("email"),
                     ct.get("phone"), ct.get("department"))
                )
            # Extract evidence files
            co_upload = UPLOAD_DIR / new_id
            co_upload.mkdir(parents=True, exist_ok=True)
            for name in zf.namelist():
                if name.startswith("evidence/") and not name.endswith("/"):
                    rel = name[len("evidence/"):]
                    dest = co_upload / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with open(dest, "wb") as out:
                        out.write(zf.read(name))
            conn.commit()
            conn.close()
            flash(f"Company '{co['name']}' imported successfully", "success")
    except Exception as e:
        flash(f"Import failed: {e}", "error")
    return redirect(url_for("dashboard"))

# ---------------------------------------------------------------------------
# Routes — Control Detail & Edit
# ---------------------------------------------------------------------------

@app.route("/control/<control_id>")
@login_required
def control_view(control_id):
    conn = get_db()
    ctrl = conn.execute("SELECT * FROM controls WHERE id=?", (control_id,)).fetchone()
    if not ctrl:
        abort(404)
    company = conn.execute("SELECT * FROM companies WHERE id=?", (ctrl["company_id"],)).fetchone()
    evidence_list = conn.execute(
        "SELECT * FROM evidence WHERE control_id=? ORDER BY uploaded_at DESC", (control_id,)
    ).fetchall()
    # Cross-framework mappings
    cross_links = []
    if ctrl["control_id"] in CROSS_MAP:
        for link in CROSS_MAP[ctrl["control_id"]]:
            linked = conn.execute(
                "SELECT id, control_id, title, status, framework FROM controls WHERE company_id=? AND control_id=?",
                (ctrl["company_id"], link["control_id"])
            ).fetchone()
            if linked:
                cross_links.append({**dict(linked), "relationship": link["relationship"]})
    conn.close()
    return render_template("control_view.html", ctrl=ctrl, company=company, evidence=evidence_list, cross_links=cross_links)


@app.route("/control/<control_id>/edit", methods=["POST"])
@role_required("auditor")
def control_edit(control_id):
    conn = get_db()
    ctrl = conn.execute("SELECT * FROM controls WHERE id=?", (control_id,)).fetchone()
    if not ctrl:
        abort(404)

    status = request.form.get("status", ctrl["status"])
    notes = request.form.get("notes", ctrl["notes"])
    assigned_name = request.form.get("assigned_name", ctrl["assigned_name"])
    assigned_email = request.form.get("assigned_email", ctrl["assigned_email"])
    tags_raw = request.form.get("tags", "")
    tags = json.dumps([t.strip() for t in tags_raw.split(",") if t.strip()])
    prior_evidence_valid = 1 if request.form.get("prior_evidence_valid") else 0

    conn.execute(
        """UPDATE controls SET status=?, notes=?, assigned_name=?, assigned_email=?,
           tags=?, prior_evidence_valid=?, updated_at=? WHERE id=?""",
        (status, notes, assigned_name, assigned_email, tags, prior_evidence_valid, now_iso(), control_id)
    )
    conn.commit()
    conn.close()

    return redirect(url_for("control_view", control_id=control_id))

# ---------------------------------------------------------------------------
# Routes — Evidence Upload / View / Delete
# ---------------------------------------------------------------------------

@app.route("/control/<control_id>/upload", methods=["POST"])
@role_required("auditor")
def evidence_upload(control_id):
    conn = get_db()
    ctrl = conn.execute("SELECT * FROM controls WHERE id=?", (control_id,)).fetchone()
    if not ctrl:
        abort(404)

    files = request.files.getlist("files")
    desc = request.form.get("description", "")

    for f in files:
        if f and allowed_file(f.filename):
            original = secure_filename(f.filename)
            ext = original.rsplit(".", 1)[1].lower() if "." in original else ""
            stored_name = f"{uuid.uuid4().hex}.{ext}"
            co_dir = UPLOAD_DIR / ctrl["company_id"]
            co_dir.mkdir(parents=True, exist_ok=True)
            f.save(str(co_dir / stored_name))

            conn.execute(
                """INSERT INTO evidence (id, control_id, company_id, filename, original_filename,
                   file_type, file_size, description, uploaded_by, uploaded_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), control_id, ctrl["company_id"], stored_name, original,
                 ext, os.path.getsize(str(co_dir / stored_name)), desc,
                 session.get("display_name", ""), now_iso())
            )
    conn.commit()
    conn.close()
    return redirect(url_for("control_view", control_id=control_id))


@app.route("/evidence/<evidence_id>/view")
@login_required
def evidence_view(evidence_id):
    conn = get_db()
    ev = conn.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
    conn.close()
    if not ev:
        abort(404)
    fpath = UPLOAD_DIR / ev["company_id"] / ev["filename"]
    if not fpath.exists():
        abort(404)
    return send_file(str(fpath), download_name=ev["original_filename"])


@app.route("/evidence/<evidence_id>/delete", methods=["POST"])
@role_required("auditor")
def evidence_delete(evidence_id):
    conn = get_db()
    ev = conn.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
    if not ev:
        abort(404)
    control_id = ev["control_id"]
    fpath = UPLOAD_DIR / ev["company_id"] / ev["filename"]
    if fpath.exists():
        fpath.unlink()
    conn.execute("DELETE FROM evidence WHERE id=?", (evidence_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("control_view", control_id=control_id))

# ---------------------------------------------------------------------------
# Routes — Contacts
# ---------------------------------------------------------------------------

@app.route("/contacts")
@login_required
def contacts_list():
    conn = get_db()
    contacts = conn.execute("""
        SELECT contacts.*, companies.name as company_name, companies.id as company_id
        FROM contacts
        JOIN companies ON contacts.company_id = companies.id
        ORDER BY companies.name, contacts.name
    """).fetchall()
    companies = conn.execute("SELECT id, name FROM companies ORDER BY name").fetchall()
    conn.close()
    return render_template("contacts.html", contacts=contacts, companies=companies)


@app.route("/company/<company_id>/contact/add", methods=["POST"])
@role_required("auditor")
def contact_add(company_id):
    conn = get_db()
    conn.execute(
        "INSERT INTO contacts (id, company_id, name, email, phone, department) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), company_id,
         request.form.get("name", ""),
         request.form.get("email", ""),
         request.form.get("phone", ""),
         request.form.get("department", ""))
    )
    conn.commit()
    conn.close()
    if request.form.get("next") == "contacts":
        return redirect(url_for("contacts_list"))
    return redirect(url_for("company_view", company_id=company_id))


@app.route("/contact/<contact_id>/delete", methods=["POST"])
@role_required("auditor")
def contact_delete(contact_id):
    conn = get_db()
    ct = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    if not ct:
        abort(404)
    conn.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
    conn.commit()
    conn.close()
    if request.form.get("next") == "contacts":
        return redirect(url_for("contacts_list"))
    return redirect(url_for("company_view", company_id=ct["company_id"]))

# ---------------------------------------------------------------------------
# Routes — Report Generation
# ---------------------------------------------------------------------------

@app.route("/company/<company_id>/report")
@login_required
def company_report(company_id):
    """Generate a full audit report as downloadable ZIP."""
    conn = get_db()
    company = conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    if not company:
        abort(404)

    section_filter = request.args.get("section", "")
    control_filter = request.args.get("control_id", "")

    query = "SELECT * FROM controls WHERE company_id=?"
    params = [company_id]
    if section_filter:
        query += " AND section=?"
        params.append(section_filter)
    if control_filter:
        query += " AND id=?"
        params.append(control_filter)
    query += " ORDER BY framework, section, control_id"

    controls = conn.execute(query, params).fetchall()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        report_lines = []
        report_lines.append(f"# Isomer Audit Report")
        report_lines.append(f"## Company: {company['name']}")
        report_lines.append(f"**Frameworks:** {company['frameworks']}")
        report_lines.append(f"**Engagement:** {company['engagement_type']}")
        report_lines.append(f"**Generated:** {now_iso()}")
        report_lines.append(f"**Total Controls in Report:** {len(controls)}")
        report_lines.append("")

        # Stats
        statuses = {}
        for c in controls:
            statuses[c["status"]] = statuses.get(c["status"], 0) + 1
        report_lines.append("## Status Summary")
        for s, count in sorted(statuses.items()):
            report_lines.append(f"- **{s}**: {count}")
        report_lines.append("")

        current_section = None
        for ctrl in controls:
            sec_key = f"{ctrl['framework']}/{ctrl['section']}"
            if sec_key != current_section:
                current_section = sec_key
                report_lines.append(f"---")
                report_lines.append(f"## Section: {ctrl['section']} ({ctrl['framework'].upper()})")
                report_lines.append("")

            report_lines.append(f"### {ctrl['control_id']} — {ctrl['title']}")
            report_lines.append(f"**Status:** {ctrl['status']}")
            if ctrl["assigned_name"]:
                report_lines.append(f"**Assigned:** {ctrl['assigned_name']} ({ctrl['assigned_email'] or 'N/A'})")
            report_lines.append(f"**Challenge Level:** {ctrl['challenge_level']}")
            if ctrl["description"]:
                report_lines.append(f"\n**Description:** {ctrl['description']}")
            if ctrl["notes"]:
                report_lines.append(f"\n**Notes:** {ctrl['notes']}")
            tags = json.loads(ctrl["tags"] or "[]")
            if tags:
                report_lines.append(f"**Tags:** {', '.join(tags)}")

            # Evidence
            ev_list = conn.execute(
                "SELECT * FROM evidence WHERE control_id=? ORDER BY uploaded_at",
                (ctrl["id"],)
            ).fetchall()
            if ev_list:
                report_lines.append(f"\n**Evidence ({len(ev_list)} files):**")
                for ev in ev_list:
                    report_lines.append(f"- {ev['original_filename']} (uploaded {ev['uploaded_at']})")
                    src = UPLOAD_DIR / company_id / ev["filename"]
                    if src.exists():
                        arc_path = f"evidence/{ctrl['framework']}/{ctrl['section']}/{ctrl['control_id']}/{ev['original_filename']}"
                        zf.write(str(src), arc_path)
                        # Embed images in report
                        ext = ev["original_filename"].rsplit(".", 1)[-1].lower() if "." in ev["original_filename"] else ""
                        if ext in ("png", "jpg", "jpeg", "gif", "webp", "svg"):
                            report_lines.append(f"  ![{ev['original_filename']}]({arc_path})")
            report_lines.append("")

        zf.writestr("audit_report.md", "\n".join(report_lines))

    buf.seek(0)
    conn.close()
    safe_name = company["name"].replace(" ", "_")
    return send_file(buf, mimetype="application/zip",
                     as_attachment=True,
                     download_name=f"isomer_report_{safe_name}_{datetime.date.today()}.zip")


@app.route("/company/<company_id>/report/view")
@login_required
def company_report_view(company_id):
    """View an HTML report in-browser."""
    conn = get_db()
    company = conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    if not company:
        abort(404)

    controls = conn.execute(
        "SELECT * FROM controls WHERE company_id=? ORDER BY framework, section, control_id",
        (company_id,)
    ).fetchall()

    evidence_map = {}
    evidence_all = conn.execute("SELECT * FROM evidence WHERE company_id=?", (company_id,)).fetchall()
    for ev in evidence_all:
        evidence_map.setdefault(ev["control_id"], []).append(ev)

    conn.close()
    return render_template("report_view.html", company=company, controls=controls, evidence_map=evidence_map)

# ---------------------------------------------------------------------------
# Routes — Settings (port 27000 — served from same app, separated by blueprint or prefix)
# ---------------------------------------------------------------------------

@app.route("/admin")
@login_required
def admin_portal():
    return redirect(url_for("settings_page"))


@app.route("/settings")
@role_required("admin")
def settings_page():
    conn = get_db()
    users = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
    admin_count = conn.execute("SELECT COUNT(*) as c FROM users WHERE role='admin'").fetchone()["c"]
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(minutes=30)).isoformat() + "Z"
    active_sessions = conn.execute(
        "SELECT * FROM sessions WHERE last_active_at >= ? ORDER BY last_active_at DESC", (cutoff,)
    ).fetchall()
    conn.close()
    return render_template("settings.html", users=users, admin_count=admin_count, active_sessions=active_sessions)


@app.route("/settings/user/add", methods=["POST"])
@role_required("admin")
def user_add():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    display_name = request.form.get("display_name", "").strip()
    email = request.form.get("email", "").strip()
    role = request.form.get("role", "reporter")
    if not username or not password:
        flash("Username and password required", "error")
        return redirect(url_for("settings_page"))
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, display_name, email, role) VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), username, generate_password_hash(password), display_name, email, role)
        )
        conn.commit()
        flash(f"User '{username}' created", "success")
    except sqlite3.IntegrityError:
        flash("Username already exists", "error")
    conn.close()
    return redirect(url_for("settings_page"))


@app.route("/settings/user/<user_id>/delete", methods=["POST"])
@role_required("admin")
def user_delete(user_id):
    conn = get_db()
    # Prevent deleting the last admin account
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if user and user["role"] == "admin":
        admin_count = conn.execute("SELECT COUNT(*) as c FROM users WHERE role='admin'").fetchone()["c"]
        if admin_count <= 1:
            conn.close()
            flash("Cannot delete the last admin account", "error")
            return redirect(url_for("settings_page"))
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("settings_page"))


@app.route("/settings/user/<user_id>/edit", methods=["POST"])
@role_required("admin")
def user_edit(user_id):
    conn = get_db()
    display_name = request.form.get("display_name", "").strip()
    email = request.form.get("email", "").strip()
    role = request.form.get("role", "reporter")
    password = request.form.get("password", "").strip()

    if password:
        conn.execute(
            "UPDATE users SET display_name=?, email=?, role=?, password_hash=? WHERE id=?",
            (display_name, email, role, generate_password_hash(password), user_id)
        )
    else:
        conn.execute(
            "UPDATE users SET display_name=?, email=?, role=? WHERE id=?",
            (display_name, email, role, user_id)
        )
    conn.commit()
    conn.close()
    return redirect(url_for("settings_page"))


# ---------------------------------------------------------------------------
# API endpoints for AJAX
# ---------------------------------------------------------------------------

@app.route("/api/control/<control_id>/tags", methods=["POST"])
@role_required("auditor")
def api_control_tags(control_id):
    conn = get_db()
    data = request.get_json()
    tags = json.dumps(data.get("tags", []))
    conn.execute("UPDATE controls SET tags=?, updated_at=? WHERE id=?", (tags, now_iso(), control_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/search/<company_id>")
@login_required
def api_search(company_id):
    q = request.args.get("q", "")
    conn = get_db()
    s = f"%{q}%"
    controls = conn.execute(
        """SELECT id, control_id, title, section, framework, status, tags
           FROM controls WHERE company_id=?
           AND (title LIKE ? OR description LIKE ? OR notes LIKE ? OR control_id LIKE ? OR tags LIKE ?)
           ORDER BY framework, section, control_id LIMIT 50""",
        (company_id, s, s, s, s, s)
    ).fetchall()
    conn.close()
    return jsonify([dict(c) for c in controls])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

@app.context_processor
def inject_portal_flag():
    return {"is_admin_portal": session.get("role") == "admin"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=27001, debug=False)
