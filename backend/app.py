"""Student Attendance Checker API."""

import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, g, jsonify, request
from flask_cors import CORS


def format_scan_time_et(ts: str) -> str:
    """Render a scan_timestamp as human-readable Eastern wall-clock time,
    to the second. The upstream Apps Script encodes ET-local times with a
    fixed EST (UTC-5) offset regardless of date -- so a scan at 12:40 PM
    EDT is stored as '17:40Z' (the EST equivalent). Subtract 5 hours and
    render naive so the wall-clock time is correct on both sides of DST.
    Input: '2026-02-03T19:11:32Z'. Output: 'Tue Feb 3, 2:11:32 PM ET'."""
    if not ts:
        return ""
    s = ts.replace("Z", "+00:00").replace(".000+00:00", "+00:00")
    try:
        dt = datetime.fromisoformat(s).replace(tzinfo=None)
        wall = dt - timedelta(hours=5)
        return wall.strftime("%a %b %-d, %-I:%M:%S %p ET")
    except ValueError:
        return ts

app = Flask(__name__)
CORS(app)

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).parent / "data" / "checker.db"))
SYNC_API_KEY = os.environ.get("SYNC_API_KEY", "dev-key")


def normalize_barcode(barcode):
    """Strip leading zeros so '07142851387095' and '7142851387095' match.

    USB/UPC scanners inconsistently include or drop leading zeros for the
    same physical card. Canonicalize to the stripped form everywhere so
    registered barcodes match scan rows. Idempotent. Preserves None/''
    as-is; '000' collapses to '0' so an all-zero code stays non-empty."""
    if not barcode:
        return barcode
    stripped = barcode.lstrip("0")
    return stripped or "0"

def _compute_attendance_delta(db, email, course_code_filter=None):
    """Return {absent_before, absent_after} for a given email, across all
    their courses or a single course if specified. 'before' reflects the
    state with the student's currently-stored barcodes; 'after' would be
    the same if the caller commits no changes, so this helper is meant to
    be called twice -- once before the write, once after -- with the diff
    computed by the caller. See usage below."""
    rows = db.execute(
        "SELECT course_code, barcode_id, physical_barcode_id FROM student "
        "WHERE email = ?" + (" AND course_code = ?" if course_code_filter else ""),
        (email, course_code_filter) if course_code_filter else (email,),
    ).fetchall()
    total = 0
    for row in rows:
        barcodes = list({
            normalize_barcode(b) for b in (row["barcode_id"], row["physical_barcode_id"])
            if b
        })
        sessions = db.execute(
            "SELECT scan_date, COUNT(DISTINCT student_id) as cnt FROM attendance "
            "WHERE course_code = ? GROUP BY scan_date HAVING cnt >= 5",
            (row["course_code"],),
        ).fetchall()
        session_dates = {r["scan_date"] for r in sessions}
        if not barcodes or not session_dates:
            total += len(session_dates)
            continue
        placeholders = ",".join("?" * len(barcodes))
        attended = db.execute(
            f"SELECT DISTINCT scan_date FROM attendance "
            f"WHERE student_id IN ({placeholders}) AND course_code = ? "
            f"AND scan_date IN ({','.join('?' * len(session_dates))})",
            barcodes + [row["course_code"]] + list(session_dates),
        ).fetchall()
        attended_dates = {r["scan_date"] for r in attended}
        excused = db.execute(
            "SELECT absence_date FROM excused_absence "
            "WHERE student_email = ? AND course_code = ?",
            (email, row["course_code"]),
        ).fetchall()
        excused_dates = {r[0] for r in excused}
        total += len(session_dates - attended_dates - excused_dates)
    return total


SCHEMA = """
CREATE TABLE IF NOT EXISTS student (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    course_code TEXT,
    course_name TEXT,
    barcode_id TEXT,
    physical_barcode_id TEXT,
    huid TEXT,
    UNIQUE(email, course_code)
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    course_code TEXT NOT NULL,
    scan_date TEXT NOT NULL,
    scan_timestamp TEXT NOT NULL,
    UNIQUE(student_id, course_code, scan_date)
);

CREATE TABLE IF NOT EXISTS excused_absence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_email TEXT NOT NULL,
    course_code TEXT NOT NULL,
    absence_date TEXT NOT NULL,
    absence_type TEXT,
    reason TEXT,
    source TEXT DEFAULT 'typeform',
    UNIQUE(student_email, course_code, absence_date)
);
"""


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(str(DB_PATH))
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    # Check if we need to migrate from UNIQUE(email) to UNIQUE(email, course_code)
    old_schema = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='student'"
    ).fetchone()
    if old_schema and "UNIQUE(email, course_code)" not in (old_schema[0] or ""):
        conn.execute("DROP TABLE IF EXISTS student")
    conn.executescript(SCHEMA)
    # Migration for existing DBs
    try:
        conn.execute("ALTER TABLE student ADD COLUMN physical_barcode_id TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    _migrate_normalize_barcodes(conn)
    conn.commit()
    conn.close()


def _migrate_normalize_barcodes(conn):
    """Strip leading zeros from stored barcodes so registrations match scans.

    Idempotent. Run on every init_db(); a no-op once all rows are already
    normalized. On attendance, a pair like ('07X', '7X') for the same
    (course, date) would violate UNIQUE on migrate -- drop the leading-zero
    duplicate first, then normalize the survivor."""
    conn.execute(
        "UPDATE student SET barcode_id = LTRIM(barcode_id, '0') "
        "WHERE barcode_id IS NOT NULL AND barcode_id LIKE '0%' "
        "AND LTRIM(barcode_id, '0') != ''"
    )
    conn.execute(
        "UPDATE student SET physical_barcode_id = LTRIM(physical_barcode_id, '0') "
        "WHERE physical_barcode_id IS NOT NULL AND physical_barcode_id LIKE '0%' "
        "AND LTRIM(physical_barcode_id, '0') != ''"
    )
    conn.execute("""
        DELETE FROM attendance
        WHERE student_id LIKE '0%'
          AND LTRIM(student_id, '0') != ''
          AND EXISTS (
              SELECT 1 FROM attendance a2
              WHERE a2.student_id = LTRIM(attendance.student_id, '0')
                AND a2.course_code = attendance.course_code
                AND a2.scan_date = attendance.scan_date
          )
    """)
    conn.execute(
        "UPDATE attendance SET student_id = LTRIM(student_id, '0') "
        "WHERE student_id LIKE '0%' AND LTRIM(student_id, '0') != ''"
    )


EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@bison\.howard\.edu$")
HUID_RE = re.compile(r"^@\d{8}$")
BARCODE_RE = re.compile(r"^\d+$")


def require_sync_key():
    key = request.headers.get("X-Sync-Key", "")
    if key != SYNC_API_KEY:
        return jsonify({"error": "unauthorized"}), 401
    return None


@app.route("/health")
def health():
    db = get_db()
    student_count = db.execute("SELECT COUNT(*) FROM student").fetchone()[0]
    last_attendance = db.execute(
        "SELECT MAX(scan_date) FROM attendance"
    ).fetchone()[0]
    return jsonify({
        "status": "ok",
        "student_count": student_count,
        "last_attendance_date": last_attendance,
    })


@app.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    email = (data.get("email") or "").strip().lower()
    huid = (data.get("huid") or "").strip()
    barcode_id = (data.get("barcode_id") or "").strip()
    physical_barcode_id = (data.get("physical_barcode_id") or "").strip()

    errors = []
    if not EMAIL_RE.match(email):
        errors.append("Email must be a @bison.howard.edu address")
    if not HUID_RE.match(huid):
        errors.append("HUID must be @ followed by 8 digits (e.g. @03107801)")
    if not BARCODE_RE.match(barcode_id):
        errors.append("Barcode must be numeric")
    if physical_barcode_id and not BARCODE_RE.match(physical_barcode_id):
        errors.append("Physical card barcode must be numeric")
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    barcode_id = normalize_barcode(barcode_id)
    physical_barcode_id = normalize_barcode(physical_barcode_id) if physical_barcode_id else ""

    db = get_db()
    students = db.execute(
        "SELECT id, first_name, last_name, course_code, course_name FROM student WHERE email = ?",
        (email,),
    ).fetchall()

    if not students:
        return jsonify({
            "error": "Email not found -- are you enrolled in Dr. B's class?"
        }), 404

    db.execute(
        "UPDATE student SET barcode_id = ?, physical_barcode_id = ?, huid = ? WHERE email = ?",
        (barcode_id, physical_barcode_id or None, huid, email),
    )
    db.commit()

    courses = [{"course_code": s["course_code"], "course_name": s["course_name"]} for s in students]
    return jsonify({
        "success": True,
        "student_name": f"{students[0]['first_name']} {students[0]['last_name']}",
        "courses": courses,
    })


@app.route("/attendance")
def attendance():
    email = (request.args.get("email") or "").strip().lower()
    course_code_param = (request.args.get("course_code") or "").strip()
    if not email:
        return jsonify({"error": "email parameter required"}), 400

    db = get_db()
    students = db.execute(
        "SELECT first_name, last_name, course_code, course_name, barcode_id, physical_barcode_id FROM student WHERE email = ?",
        (email,),
    ).fetchall()

    if not students:
        return jsonify({"error": "Email not found"}), 404

    if not students[0]["barcode_id"]:
        return jsonify({
            "error": "not_registered",
            "message": "You need to register your barcode first.",
        }), 400

    # If multiple courses and no course_code specified, return course list
    if len(students) > 1 and not course_code_param:
        return jsonify({
            "multiple_courses": True,
            "student_name": f"{students[0]['first_name']} {students[0]['last_name']}",
            "courses": [{"course_code": s["course_code"], "course_name": s["course_name"]} for s in students],
        })

    student = students[0]
    if course_code_param:
        match = [s for s in students if s["course_code"] == course_code_param]
        if match:
            student = match[0]

    course_code = student["course_code"]
    barcode_id = student["barcode_id"]
    physical_barcode_id = student["physical_barcode_id"]

    # Only count dates where 5+ students were scanned as real class sessions
    # (filters out test scans and scanner errors)
    all_sessions = db.execute(
        "SELECT scan_date, COUNT(DISTINCT student_id) as cnt FROM attendance WHERE course_code = ? GROUP BY scan_date HAVING cnt >= 5 ORDER BY scan_date",
        (course_code,),
    ).fetchall()
    all_dates = [row["scan_date"] for row in all_sessions]
    scan_count_by_date = {row["scan_date"]: row["cnt"] for row in all_sessions}
    total_sessions = len(all_dates)

    enrolled = db.execute(
        "SELECT COUNT(*) FROM student WHERE course_code = ?", (course_code,)
    ).fetchone()[0]

    # Check both virtual and physical card barcodes; normalize to match
    # stored scans (see normalize_barcode — scanners drop leading zeros).
    barcodes = list({normalize_barcode(b) for b in (barcode_id, physical_barcode_id) if b})
    placeholders = ",".join("?" * len(barcodes))
    attended_rows = db.execute(
        f"SELECT scan_date, MIN(scan_timestamp) AS first_ts FROM attendance "
        f"WHERE student_id IN ({placeholders}) AND course_code = ? GROUP BY scan_date",
        barcodes + [course_code],
    ).fetchall()
    first_scan_by_date = {row["scan_date"]: row["first_ts"] for row in attended_rows}
    attended_dates = set(first_scan_by_date.keys())

    excused_rows = db.execute(
        "SELECT absence_date, absence_type, reason FROM excused_absence WHERE student_email = ? AND course_code = ?",
        (email, course_code),
    ).fetchall()
    excused_map = {row["absence_date"]: row for row in excused_rows}

    dates = []
    for d in all_dates:
        if d in excused_map:
            status = "excused"
        elif d in attended_dates:
            status = "present"
        else:
            status = "absent"
        entry = {
            "date": d,
            "status": status,
            "class_scan_count": scan_count_by_date.get(d, 0),
            "first_scan_time": format_scan_time_et(first_scan_by_date.get(d))
                if d in first_scan_by_date else None,
        }
        if d in excused_map:
            entry["absence_type"] = excused_map[d]["absence_type"]
            entry["reason"] = excused_map[d]["reason"]
        dates.append(entry)

    excused_count = len(set(excused_map.keys()) & set(all_dates))
    sessions_attended = len((attended_dates & set(all_dates)) - set(excused_map.keys()))
    unexcused_count = total_sessions - sessions_attended - excused_count

    effective_rate = (
        (sessions_attended + excused_count) / total_sessions
        if total_sessions > 0
        else 1.0
    )

    barcodes_registered = [b for b in (barcode_id, physical_barcode_id) if b]

    return jsonify({
        "student_name": f"{student['first_name']} {student['last_name']}",
        "course_code": course_code,
        "course_name": student["course_name"],
        "enrolled": enrolled,
        "barcodes_registered": barcodes_registered,
        "total_sessions": total_sessions,
        "sessions_attended": sessions_attended,
        "excused_count": excused_count,
        "unexcused_count": max(0, unexcused_count),
        "effective_rate": round(effective_rate, 4),
        "dates": dates,
    })


@app.route("/debug")
def debug_view():
    email = (request.args.get("email") or "").strip().lower()
    if not email:
        return (
            "<p style='font-family:system-ui;font-size:18px;padding:2rem'>"
            "Add <code>?email=you@bison.howard.edu</code> to the URL.</p>"
        ), 400

    db = get_db()
    students = db.execute(
        "SELECT first_name, last_name, course_code, course_name, barcode_id, "
        "physical_barcode_id, huid FROM student WHERE email = ? ORDER BY course_code",
        (email,),
    ).fetchall()
    if not students:
        return f"<p style='font-family:system-ui;font-size:18px;padding:2rem'>Email <b>{email}</b> not found.</p>", 404

    name = f"{students[0]['first_name']} {students[0]['last_name']}"

    out = [
        "<!doctype html><html><head><meta charset='utf-8'><title>Debug -- ", name, "</title>",
        "<style>",
        "body{font-family:system-ui,sans-serif;font-size:18px;max-width:1100px;margin:2rem auto;padding:0 1rem;line-height:1.5}",
        "h1{font-size:28px;margin-bottom:0.25rem} h2{font-size:22px;margin-top:2rem;border-bottom:2px solid #ccc;padding-bottom:0.25rem}",
        "table{border-collapse:collapse;width:100%;margin-top:0.5rem;font-size:16px}",
        "th,td{padding:0.4rem 0.6rem;border-bottom:1px solid #ddd;text-align:left}",
        "th{background:#f3f3f3;font-weight:600}",
        ".present{color:#0a7a0a;font-weight:600} .excused{color:#0066cc;font-weight:600} .absent{color:#b00020;font-weight:600}",
        ".meta{color:#555;font-size:16px} code{background:#f3f3f3;padding:0.1rem 0.3rem;border-radius:3px}",
        ".summary{background:#fafafa;padding:0.6rem 1rem;border-left:4px solid #333;margin-top:0.5rem}",
        "</style></head><body>",
        f"<h1>{name}</h1>",
        "<form id='link-form' style='background:#fffbe6;border:2px solid #e7c66e;padding:1rem;border-radius:8px;margin:1rem 0'>",
        "<b>Link physical barcode</b> (admin only)<br>",
        "<label>Physical barcode: <input type='text' name='barcode' required style='font-family:monospace;padding:0.3rem'></label> ",
        "<label>Admin key: <input type='password' name='key' required style='padding:0.3rem'></label> ",
        "<button type='submit' style='padding:0.3rem 0.8rem'>Link</button>",
        "<div id='link-result' style='margin-top:0.5rem'></div>",
        "</form>",
        f"""<script>
document.getElementById('link-form').addEventListener('submit', async (e) => {{
  e.preventDefault();
  const f = e.target;
  const res = document.getElementById('link-result');
  res.textContent = 'Linking...';
  try {{
    const r = await fetch('/admin/link-physical', {{
      method: 'POST',
      headers: {{
        'Content-Type': 'application/json',
        'X-Sync-Key': f.key.value,
      }},
      body: JSON.stringify({{
        email: {email!r},
        physical_barcode_id: f.barcode.value.trim(),
      }}),
    }});
    const j = await r.json();
    if (!r.ok) {{ res.style.color = '#b00020'; res.textContent = 'Error: ' + (j.error || r.status); return; }}
    const d = j.attendance_delta;
    res.style.color = '#0a7a0a';
    res.textContent = 'Linked. Unexcused absences: ' + d.absent_before + ' -> ' + d.absent_after + '. Reloading...';
    setTimeout(() => location.reload(), 1200);
  }} catch (err) {{ res.style.color = '#b00020'; res.textContent = 'Request failed: ' + err; }}
}});
</script>""",
        f"<p class='meta'>{email} &middot; HUID: <code>{students[0]['huid'] or '(none)'}</code> &middot; "
        f"Virtual barcode: <code>{students[0]['barcode_id'] or '(none)'}</code>"
        + (f" &middot; Physical barcode: <code>{students[0]['physical_barcode_id']}</code>" if students[0]['physical_barcode_id'] else "")
        + "</p>",
    ]

    for s in students:
        course = s["course_code"]
        bcs = list({normalize_barcode(b) for b in (s["barcode_id"], s["physical_barcode_id"]) if b})

        sessions = db.execute(
            "SELECT scan_date, COUNT(DISTINCT student_id) AS n FROM attendance "
            "WHERE course_code = ? GROUP BY scan_date HAVING n >= 5 ORDER BY scan_date",
            (course,),
        ).fetchall()
        enrolled = db.execute(
            "SELECT COUNT(*) FROM student WHERE course_code = ?", (course,)
        ).fetchone()[0]
        excused = {
            r["absence_date"]: (r["absence_type"] or "", r["reason"] or "")
            for r in db.execute(
                "SELECT absence_date, absence_type, reason FROM excused_absence "
                "WHERE student_email = ? AND course_code = ?",
                (email, course),
            ).fetchall()
        }

        out.append(f"<h2>{course} &mdash; {s['course_name']}</h2>")
        out.append(
            f"<p class='meta'>Enrolled: <b>{enrolled}</b>"
            f" &middot; Sessions recorded: <b>{len(sessions)}</b>"
            f" &middot; Your barcodes: <code>{', '.join(bcs) if bcs else 'NOT REGISTERED'}</code></p>"
        )
        out.append(
            "<table><thead><tr><th>Date</th><th>Status</th><th>Class size</th>"
            "<th>% of class</th><th>Your scan times</th><th>Excuse</th></tr></thead><tbody>"
        )

        present = excused_n = absent = 0
        for row in sessions:
            d = row["scan_date"]
            n = row["n"]
            pct = f"{100*n/enrolled:.0f}%" if enrolled else "-"
            stamps = []
            if bcs:
                placeholders = ",".join("?" * len(bcs))
                stamps = [
                    r[0]
                    for r in db.execute(
                        f"SELECT scan_timestamp FROM attendance WHERE student_id IN ({placeholders}) "
                        f"AND course_code = ? AND scan_date = ? ORDER BY scan_timestamp",
                        bcs + [course, d],
                    ).fetchall()
                ]
            if d in excused:
                status, cls = "excused", "excused"
                excused_n += 1
            elif stamps:
                status, cls = "present", "present"
                present += 1
            else:
                status, cls = "absent", "absent"
                absent += 1
            stamp_str = "<br>".join(format_scan_time_et(t) for t in stamps) if stamps else "&mdash;"
            excuse_str = ""
            if d in excused:
                t, r = excused[d]
                excuse_str = f"{t}: {r}" if t else r
            out.append(
                f"<tr><td>{d}</td><td class='{cls}'>{status}</td>"
                f"<td>{n}/{enrolled}</td><td>{pct}</td>"
                f"<td>{stamp_str}</td><td>{excuse_str}</td></tr>"
            )

        total = len(sessions)
        rate = (present + excused_n) / total * 100 if total else 100.0
        out.append("</tbody></table>")
        out.append(
            f"<div class='summary'>Present: <b>{present}</b> &middot; "
            f"Excused: <b>{excused_n}</b> &middot; Absent: <b>{absent}</b> / {total} sessions "
            f"&middot; Effective rate: <b>{rate:.1f}%</b></div>"
        )

    out.append("</body></html>")
    return "".join(out)


@app.route("/enroll")
def enroll_page():
    # Static file; admin key is read client-side from ?key=... and sent in
    # X-Sync-Key header on the AJAX POSTs, which is what actually enforces
    # auth. Serving the HTML itself is not sensitive.
    return app.send_static_file("enroll.html")


@app.route("/sync/push", methods=["POST"])
def sync_push():
    auth_err = require_sync_key()
    if auth_err:
        return auth_err

    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    db = get_db()
    counts = {"students": 0, "attendance": 0, "excused": 0}

    for s in data.get("students", []):
        s.setdefault("physical_barcode_id", None)
        s["barcode_id"] = normalize_barcode(s.get("barcode_id"))
        s["physical_barcode_id"] = normalize_barcode(s.get("physical_barcode_id"))
        db.execute("""
            INSERT INTO student (email, first_name, last_name, course_code, course_name, barcode_id, physical_barcode_id, huid)
            VALUES (:email, :first_name, :last_name, :course_code, :course_name, :barcode_id, :physical_barcode_id, :huid)
            ON CONFLICT(email, course_code) DO UPDATE SET
                first_name=excluded.first_name, last_name=excluded.last_name,
                course_name=excluded.course_name,
                barcode_id=COALESCE(student.barcode_id, excluded.barcode_id),
                physical_barcode_id=COALESCE(student.physical_barcode_id, excluded.physical_barcode_id),
                huid=COALESCE(student.huid, excluded.huid)
        """, s)
        counts["students"] += 1

    for a in data.get("attendance", []):
        a["student_id"] = normalize_barcode(a.get("student_id"))
        db.execute("""
            INSERT INTO attendance (student_id, course_code, scan_date, scan_timestamp)
            VALUES (:student_id, :course_code, :scan_date, :scan_timestamp)
            ON CONFLICT(student_id, course_code, scan_date) DO UPDATE SET
                scan_timestamp=excluded.scan_timestamp
        """, a)
        counts["attendance"] += 1

    for e in data.get("excused_absences", []):
        db.execute("""
            INSERT INTO excused_absence (student_email, course_code, absence_date, absence_type, reason, source)
            VALUES (:student_email, :course_code, :absence_date, :absence_type, :reason, :source)
            ON CONFLICT(student_email, course_code, absence_date) DO UPDATE SET
                absence_type=excluded.absence_type, reason=excluded.reason, source=excluded.source
        """, e)
        counts["excused"] += 1

    db.commit()
    return jsonify({"success": True, "counts": counts})


@app.route("/sync/pull")
def sync_pull():
    auth_err = require_sync_key()
    if auth_err:
        return auth_err

    db = get_db()
    rows = db.execute(
        "SELECT email, barcode_id, physical_barcode_id, huid FROM student WHERE barcode_id IS NOT NULL AND barcode_id != ''"
    ).fetchall()

    return jsonify({
        "registrations": [
            {"email": r["email"], "barcode_id": r["barcode_id"], "physical_barcode_id": r["physical_barcode_id"], "huid": r["huid"]}
            for r in rows
        ]
    })


@app.route("/admin/link-physical", methods=["POST"])
def admin_link_physical():
    auth_err = require_sync_key()
    if auth_err:
        return auth_err
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    physical = normalize_barcode((data.get("physical_barcode_id") or "").strip())
    if not email or not physical:
        return jsonify({"error": "email and physical_barcode_id required"}), 400

    db = get_db()
    existing = db.execute(
        "SELECT id FROM student WHERE email = ?", (email,)
    ).fetchall()
    if not existing:
        return jsonify({"error": "email not found"}), 404

    absent_before = _compute_attendance_delta(db, email)
    db.execute(
        "UPDATE student SET physical_barcode_id = ? WHERE email = ?",
        (physical, email),
    )
    db.commit()
    absent_after = _compute_attendance_delta(db, email)

    return jsonify({
        "success": True,
        "email": email,
        "physical_barcode_id": physical,
        "rows_updated": len(existing),
        "attendance_delta": {
            "absent_before": absent_before,
            "absent_after": absent_after,
        },
    })


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
