"""Student Attendance Checker API."""

import html
import json
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

def _scan_ts_to_et_minutes(ts):
    """Convert a scan_timestamp ('2026-02-03T19:11:32Z') to wall-clock ET
    minutes-past-midnight (float). Returns None on parse failure.
    Same convention as format_scan_time_et -- subtract 5h to get ET wall."""
    if not ts:
        return None
    s = ts.replace("Z", "+00:00").replace(".000+00:00", "+00:00")
    try:
        dt = datetime.fromisoformat(s).replace(tzinfo=None) - timedelta(hours=5)
    except ValueError:
        return None
    return dt.hour * 60 + dt.minute + dt.second / 60.0


app = Flask(__name__)
CORS(app)

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).parent / "data" / "checker.db"))
SYNC_API_KEY = os.environ.get("SYNC_API_KEY", "dev-key")

# Per-course metadata for the aggregate dashboard. Keep in sync with the
# frontend ENROLLED_OVERRIDE / CLASS_START_MINUTES constants in
# AttendanceView.tsx (source: memory project_attendance_checker.md).
COURSE_META = {
    "INFO-335-04": {"name": "POM", "enrolled": 39, "class_start_minutes": 12 * 60 + 40},
    "INFO-311-05": {"name": "QBA", "enrolled": 40, "class_start_minutes": 14 * 60 + 10},
}

# Dates to drop from aggregate charts and the individual arrival-times
# plot. These are bulk-enrollment class days -- scans were triggered by
# the virtual-barcode registration flow, not by arrival, so the timing
# signal is meaningless.
BULK_ENROLL_DATES = {
    "INFO-335-04": {"2026-04-21"},
    "INFO-311-05": {"2026-04-21"},
}


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


def normalize_barcode_variants(raw):
    """Return the set of plausible forms of a barcode for fuzzy-match at
    claim time. Strictly more permissive than normalize_barcode() -- used
    only against orphan scans in a specific course, so false positives are
    bounded by 'is this number actually in the attendance table for your
    class.' Variants:
      1. strip non-digits, strip leading zeros (canonical form)
      2. canonical minus last digit (check-digit drift)
      3. canonical minus first digit (symbology-prefix drift)
    Variants 2 and 3 are skipped when they'd shrink the barcode below 3
    chars. Returns a set (order doesn't matter, dedupe is free)."""
    if not raw:
        return set()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return set()
    canonical = digits.lstrip("0") or "0"
    variants = {canonical}
    if len(canonical) >= 4:
        variants.add(canonical[:-1])
        variants.add(canonical[1:])
    return variants


def _compute_attendance_delta(db, email, course_code_filter=None):
    """Return the total unexcused-absence count for this email's courses
    at the moment of call (int). Callers invoke this twice -- before and
    after writing student.physical_barcode_id -- and compute the delta
    themselves. Counts only sessions with >= 5 scans (the session-validity
    threshold), subtracts the student's own scans (under any of their
    barcodes), subtracts excused absences."""
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
    physical_barcode_skip_reason TEXT,
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

CREATE TABLE IF NOT EXISTS claim_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempted_at TEXT NOT NULL,
    email TEXT,
    course_code TEXT,
    submitted_barcode TEXT,
    variants_tried TEXT,
    matched_barcode TEXT,
    absent_before INTEGER,
    absent_after INTEGER
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
    try:
        conn.execute("ALTER TABLE student ADD COLUMN physical_barcode_skip_reason TEXT")
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
    skip_reason = (data.get("physical_barcode_skip_reason") or "").strip()

    errors = []
    if not EMAIL_RE.match(email):
        errors.append("Email must be a @bison.howard.edu address")
    if not HUID_RE.match(huid):
        errors.append("HUID must be @ followed by 8 digits (e.g. @03107801)")
    if not BARCODE_RE.match(barcode_id):
        errors.append("Barcode must be numeric")
    if physical_barcode_id and not BARCODE_RE.match(physical_barcode_id):
        errors.append("Physical card barcode must be numeric")
    if not physical_barcode_id and not skip_reason:
        errors.append(
            "Provide a physical card barcode or a skip reason "
            "(physical_barcode_skip_reason)"
        )
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
        "UPDATE student SET barcode_id = ?, physical_barcode_id = ?, "
        "physical_barcode_skip_reason = ?, huid = ? WHERE email = ?",
        (barcode_id, physical_barcode_id or None, skip_reason or None, huid, email),
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

    # Section orphan count: barcodes in attendance for this course that
    # aren't registered to any student (via barcode_id or physical_barcode_id).
    section_orphan_count = db.execute(
        "SELECT COUNT(DISTINCT a.student_id) FROM attendance a "
        "WHERE a.course_code = ? "
        "AND a.student_id NOT IN ("
        "  SELECT barcode_id FROM student "
        "    WHERE course_code = ? AND barcode_id IS NOT NULL AND barcode_id != '' "
        "  UNION "
        "  SELECT physical_barcode_id FROM student "
        "    WHERE course_code = ? AND physical_barcode_id IS NOT NULL AND physical_barcode_id != ''"
        ")",
        (course_code, course_code, course_code),
    ).fetchone()[0]

    has_physical_barcode = bool(physical_barcode_id)

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
        "section_orphan_count": section_orphan_count,
        "has_physical_barcode": has_physical_barcode,
    })


@app.route("/dashboard/<course_code>")
def dashboard(course_code):
    course_code = course_code.upper()
    meta = COURSE_META.get(course_code)
    if not meta:
        return jsonify({"error": f"unknown course {course_code}",
                        "known": sorted(COURSE_META.keys())}), 404

    db = get_db()
    excl = BULK_ENROLL_DATES.get(course_code, set())
    enrolled = meta["enrolled"]
    class_start = meta["class_start_minutes"]

    session_rows = db.execute(
        "SELECT scan_date, "
        "COUNT(DISTINCT student_id) AS scan_count, "
        "MIN(scan_timestamp) AS first_ts, "
        "MAX(scan_timestamp) AS last_ts "
        "FROM attendance WHERE course_code = ? "
        "GROUP BY scan_date HAVING scan_count >= 5 "
        "ORDER BY scan_date",
        (course_code,),
    ).fetchall()
    session_rows = [r for r in session_rows if r["scan_date"] not in excl]
    session_dates = [r["scan_date"] for r in session_rows]

    excused_rows = db.execute(
        "SELECT absence_date, COUNT(*) AS cnt FROM excused_absence "
        "WHERE course_code = ? GROUP BY absence_date",
        (course_code,),
    ).fetchall()
    excused_by_date = {r["absence_date"]: r["cnt"] for r in excused_rows}

    sessions = []
    for r in session_rows:
        d = r["scan_date"]
        scan_count = r["scan_count"]
        present = min(scan_count, enrolled)
        # Excused can exceed (enrolled - present) when an instructor-waiver
        # was applied to the whole roster -- the excused_absence table
        # includes drops and cross-enrolled students, so raw counts can go
        # above enrolled. Cap so the stacked-bar math stays consistent
        # with the enrolled denominator.
        raw_excused = excused_by_date.get(d, 0)
        excused = max(0, min(raw_excused, enrolled - present))
        absent = max(0, enrolled - present - excused)
        sessions.append({
            "date": d,
            "scan_count": scan_count,
            "present": present,
            "excused": excused,
            "absent": absent,
            "first_scan_time": format_scan_time_et(r["first_ts"]) if r["first_ts"] else None,
            "last_scan_time": format_scan_time_et(r["last_ts"]) if r["last_ts"] else None,
            "first_scan_minutes": _scan_ts_to_et_minutes(r["first_ts"]),
            "last_scan_minutes": _scan_ts_to_et_minutes(r["last_ts"]),
        })

    # Lateness histogram: per-(student, date) first-scan minutes vs class start,
    # bucketed 2 minutes wide, clamped [-20, +30]. Weight = one scan event.
    import collections
    hist = collections.Counter()
    if session_dates:
        placeholders = ",".join("?" * len(session_dates))
        per_student = db.execute(
            f"SELECT scan_date, student_id, MIN(scan_timestamp) AS first_ts "
            f"FROM attendance WHERE course_code = ? AND scan_date IN ({placeholders}) "
            f"GROUP BY scan_date, student_id",
            (course_code, *session_dates),
        ).fetchall()
        for r in per_student:
            m = _scan_ts_to_et_minutes(r["first_ts"])
            if m is None:
                continue
            delta = m - class_start
            delta = max(-20.0, min(30.0, delta))
            bucket = int(delta // 2) * 2
            hist[bucket] += 1
    buckets = sorted(hist.keys())
    lateness_histogram = [{"bucket_min": b, "count": hist[b]} for b in buckets]

    total_sessions = len(sessions)
    total_present = sum(s["present"] for s in sessions)
    total_excused = sum(s["excused"] for s in sessions)
    total_absent = sum(s["absent"] for s in sessions)
    denom = total_present + total_excused + total_absent
    overall_rate = (total_present + total_excused) / denom if denom else 0.0

    # Per-student attendance-rate distribution. Iterates over registered
    # students in this course -- i.e. students with at least one barcode
    # on file -- and buckets their effective rate. Unregistered students
    # are counted separately so the caller can show the registration gap.
    session_dates_set = set(session_dates)
    roster = db.execute(
        "SELECT email, barcode_id, physical_barcode_id FROM student WHERE course_code = ?",
        (course_code,),
    ).fetchall()
    all_excused = db.execute(
        "SELECT student_email, absence_date FROM excused_absence WHERE course_code = ?",
        (course_code,),
    ).fetchall()
    excused_by_email = {}
    for r in all_excused:
        excused_by_email.setdefault(r["student_email"], set()).add(r["absence_date"])

    per_student_rates = []
    unregistered = 0
    for r in roster:
        barcodes = [normalize_barcode(b) for b in (r["barcode_id"], r["physical_barcode_id"]) if b]
        if not barcodes:
            unregistered += 1
            continue
        placeholders = ",".join("?" * len(barcodes))
        attended_rows = db.execute(
            f"SELECT DISTINCT scan_date FROM attendance "
            f"WHERE student_id IN ({placeholders}) AND course_code = ?",
            barcodes + [course_code],
        ).fetchall()
        credited = ({a["scan_date"] for a in attended_rows} & session_dates_set) | \
                   (excused_by_email.get(r["email"], set()) & session_dates_set)
        rate = len(credited) / total_sessions if total_sessions else 0.0
        per_student_rates.append(rate)

    # Buckets. Left-inclusive, right-exclusive; top bucket catches 100%.
    bucket_defs = [
        (0.90, 1.01, "90-100%"),
        (0.80, 0.90, "80-90%"),
        (0.70, 0.80, "70-80%"),
        (0.60, 0.70, "60-70%"),
        (0.0,  0.60, "<60%"),
    ]
    bucket_counts = [0] * len(bucket_defs)
    for r in per_student_rates:
        for i, (lo, hi, _) in enumerate(bucket_defs):
            if lo <= r < hi:
                bucket_counts[i] += 1
                break

    attendance_distribution = [
        {"label": bd[2], "low": bd[0], "high": bd[1], "count": bucket_counts[i]}
        for i, bd in enumerate(bucket_defs)
    ]

    return jsonify({
        "course_code": course_code,
        "course_name": meta["name"],
        "enrolled": enrolled,
        "class_start_minutes": class_start,
        "total_sessions": total_sessions,
        "overall_attendance_rate": round(overall_rate, 4),
        "excluded_dates": sorted(excl),
        "sessions": sessions,
        "lateness_histogram": lateness_histogram,
        "attendance_distribution": attendance_distribution,
        "registered_students": len(per_student_rates),
        "unregistered_students": unregistered,
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
    # JS-safe literal for the inline <script> body below. json.dumps
    # handles quoting/escaping; the .replace closes the </script> vector
    # (Python 3.11 f-strings can't contain backslashes in expressions,
    # so we precompute here).
    email_js = json.dumps(email).replace("</", "<\\/")

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
        email: {email_js},
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


@app.route("/debug/claims")
def debug_claims():
    key = request.args.get("key", "")
    if key != SYNC_API_KEY:
        return ("<p style='font-family:system-ui;padding:2rem'>"
                "Add <code>?key=...</code></p>"), 401

    db = get_db()
    rows = db.execute(
        "SELECT attempted_at, email, course_code, submitted_barcode, "
        "variants_tried, matched_barcode, absent_before, absent_after "
        "FROM claim_log ORDER BY id DESC LIMIT 50"
    ).fetchall()

    html = [
        "<!doctype html><html><head><meta charset='utf-8'><title>Claim log</title>",
        "<style>body{font-family:system-ui;font-size:15px;max-width:1400px;margin:1rem auto;padding:0 1rem}",
        "table{border-collapse:collapse;width:100%} th,td{padding:0.4rem 0.6rem;border-bottom:1px solid #ddd;text-align:left;font-family:monospace;font-size:13px}",
        "th{background:#f3f3f3}.match{color:#0a7a0a}.nomatch{color:#b00020}.delta{font-weight:600}</style>",
        "</head><body><h1>Last 50 claim attempts</h1>",
        "<table><thead><tr><th>When</th><th>Email</th><th>Course</th><th>Submitted</th>",
        "<th>Variants</th><th>Matched</th><th>Delta</th></tr></thead><tbody>",
    ]
    for r in rows:
        matched_cls = "match" if r["matched_barcode"] else "nomatch"
        delta = (r["absent_before"] or 0) - (r["absent_after"] or 0)
        html.append(
            f"<tr><td>{r['attempted_at']}</td><td>{r['email']}</td>"
            f"<td>{r['course_code']}</td><td>{r['submitted_barcode']}</td>"
            f"<td>{r['variants_tried']}</td>"
            f"<td class='{matched_cls}'>{r['matched_barcode'] or '--'}</td>"
            f"<td class='delta'>{r['absent_before']} -> {r['absent_after']} "
            f"({'+' if delta > 0 else ''}{delta})</td></tr>"
        )
    html.append("</tbody></table></body></html>")
    return "".join(html)


@app.route("/admin/roster")
def admin_roster():
    key = request.args.get("key", "")
    if key != SYNC_API_KEY:
        return ("<p style='font-family:system-ui;padding:2rem'>"
                "Add <code>?key=...</code></p>"), 401

    db = get_db()
    rows = db.execute(
        "SELECT email, first_name, last_name, course_code, huid, "
        "barcode_id, physical_barcode_id, physical_barcode_skip_reason "
        "FROM student ORDER BY course_code, last_name, first_name"
    ).fetchall()

    # Pre-aggregate last scan per (barcode, course_code). Single query
    # instead of N+1. Attendance.student_id holds the scanned barcode.
    last_scans = {
        (r["student_id"], r["course_code"]): r["last_scan"]
        for r in db.execute(
            "SELECT student_id, course_code, MAX(scan_timestamp) AS last_scan "
            "FROM attendance GROUP BY student_id, course_code"
        ).fetchall()
    }

    # Compute status for each row and summary counts
    n_physical = 0
    n_skipped = 0
    n_virtual = 0
    n_unreg = 0

    table_rows = []
    for r in rows:
        phys = r["physical_barcode_id"]
        skip = r["physical_barcode_skip_reason"]
        virt = r["barcode_id"]
        # skip_reason is student-supplied free text (via /register "other"
        # option), so it must be HTML-escaped anywhere it's rendered.
        skip_safe = html.escape(skip) if skip else None
        if phys:
            status = "physical"
            status_cls = "status-physical"
            n_physical += 1
        elif skip:
            status = f"skipped: {skip_safe}"
            status_cls = "status-skipped"
            n_skipped += 1
        elif virt:
            status = "virtual only"
            status_cls = "status-virtual"
            n_virtual += 1
        else:
            status = "unregistered"
            status_cls = "status-unreg"
            n_unreg += 1

        # Most recent scan across either barcode for this student+course.
        bcs = [normalize_barcode(b) for b in (virt, phys) if b]
        stamps = [last_scans.get((b, r["course_code"])) for b in bcs]
        last_scan = max((s for s in stamps if s), default=None)
        last_scan_str = format_scan_time_et(last_scan) if last_scan else "--"

        name = f"{r['last_name']}, {r['first_name']}"
        table_rows.append(
            f"<tr>"
            f"<td>{r['email']}</td>"
            f"<td>{name}</td>"
            f"<td>{r['course_code']}</td>"
            f"<td>{r['huid'] or '--'}</td>"
            f"<td>{virt or '--'}</td>"
            f"<td>{phys or '--'}</td>"
            f"<td>{skip_safe or '--'}</td>"
            f"<td>{last_scan_str}</td>"
            f"<td class='{status_cls}'>{status}</td>"
            f"</tr>"
        )

    total = len(rows)
    page = [
        "<!doctype html><html><head><meta charset='utf-8'><title>Student Roster</title>",
        "<style>",
        "body{font-family:system-ui;font-size:15px;max-width:1500px;margin:1rem auto;padding:0 1rem}",
        "table{border-collapse:collapse;width:100%}",
        "th,td{padding:0.4rem 0.6rem;border-bottom:1px solid #ddd;text-align:left;"
        "font-family:monospace;font-size:13px}",
        "th{background:#f3f3f3}",
        ".summary{margin:1rem 0;font-size:15px}",
        ".status-physical{color:#0a7a0a;font-weight:600}",
        ".status-skipped{color:#b36b00;font-weight:600}",
        ".status-virtual{color:#555}",
        ".status-unreg{color:#b00020;font-weight:600}",
        "</style></head><body>",
        "<h1>Student Roster</h1>",
        f"<div class='summary'>Total enrolled: {total} &nbsp;|&nbsp; "
        f"Physical: {n_physical} &nbsp;|&nbsp; "
        f"Skipped: {n_skipped} &nbsp;|&nbsp; "
        f"Virtual only: {n_virtual} &nbsp;|&nbsp; "
        f"Unregistered: {n_unreg}</div>",
        "<table><thead><tr>",
        "<th>Email</th><th>Name</th><th>Course</th><th>HUID</th>",
        "<th>Virtual barcode</th><th>Physical barcode</th><th>Skip reason</th>",
        "<th>Last scan</th><th>Status</th>",
        "</tr></thead><tbody>",
    ]
    page.extend(table_rows)
    page.append("</tbody></table></body></html>")
    return "".join(page)


@app.route("/enroll")
def enroll_page():
    # Static file; admin key is read client-side from ?key=... and sent in
    # X-Sync-Key header on the AJAX POSTs, which is what actually enforces
    # auth. Serving the HTML itself is not sensitive.
    return app.send_static_file("enroll.html")


@app.route("/enroll-virtual")
def enroll_virtual_page():
    return app.send_static_file("enroll-virtual.html")


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
        "SELECT email, barcode_id, physical_barcode_id, physical_barcode_skip_reason, huid "
        "FROM student WHERE barcode_id IS NOT NULL AND barcode_id != ''"
    ).fetchall()

    return jsonify({
        "registrations": [
            {
                "email": r["email"],
                "barcode_id": r["barcode_id"],
                "physical_barcode_id": r["physical_barcode_id"],
                "physical_barcode_skip_reason": r["physical_barcode_skip_reason"],
                "huid": r["huid"],
            }
            for r in rows
        ]
    })


def _student_courses(db, email):
    """Return list of distinct course_codes the student is enrolled in."""
    return [r["course_code"] for r in db.execute(
        "SELECT DISTINCT course_code FROM student WHERE email = ?",
        (email,),
    ).fetchall()]


def _write_attendance_today(db, barcode, course_code):
    """Insert an attendance row for today with the given barcode and course.
    Called after a successful bulk-enroll link so the registration scan
    also serves as the attendance scan for the current session. INSERT OR
    IGNORE makes the call idempotent against the
    UNIQUE(student_id, course_code, scan_date) constraint."""
    now = datetime.utcnow()
    db.execute(
        "INSERT OR IGNORE INTO attendance "
        "(student_id, course_code, scan_date, scan_timestamp) "
        "VALUES (?, ?, ?, ?)",
        (barcode, course_code, now.date().isoformat(),
         now.strftime("%Y-%m-%dT%H:%M:%SZ")),
    )


@app.route("/admin/link-physical", methods=["POST"])
def admin_link_physical():
    auth_err = require_sync_key()
    if auth_err:
        return auth_err
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    physical = normalize_barcode((data.get("physical_barcode_id") or "").strip())
    course_code = (data.get("course_code") or "").strip() or None
    if not email or not physical:
        return jsonify({"error": "email and physical_barcode_id required"}), 400

    db = get_db()
    existing = db.execute(
        "SELECT id FROM student WHERE email = ?", (email,)
    ).fetchall()
    if not existing:
        return jsonify({"error": "email not found"}), 404

    if course_code and course_code not in _student_courses(db, email):
        return jsonify({
            "error": f"student is not enrolled in course {course_code}",
        }), 400

    absent_before = _compute_attendance_delta(db, email)
    db.execute(
        "UPDATE student SET physical_barcode_id = ? WHERE email = ?",
        (physical, email),
    )
    if course_code:
        _write_attendance_today(db, physical, course_code)
    db.commit()
    absent_after = _compute_attendance_delta(db, email)

    return jsonify({
        "success": True,
        "email": email,
        "physical_barcode_id": physical,
        "rows_updated": len(existing),
        "attendance_marked": bool(course_code),
        "attendance_delta": {
            "absent_before": absent_before,
            "absent_after": absent_after,
        },
    })


@app.route("/admin/link-virtual", methods=["POST"])
def admin_link_virtual():
    auth_err = require_sync_key()
    if auth_err:
        return auth_err
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    submitted = (data.get("barcode_id") or "").strip()
    course_code = (data.get("course_code") or "").strip() or None
    if not email or not submitted:
        return jsonify({"error": "email and barcode_id required"}), 400

    virtual = normalize_barcode(submitted)
    if not virtual or virtual == "0":
        return jsonify({
            "error": "barcode must contain digits and not be all zeros",
        }), 400

    db = get_db()
    existing = db.execute(
        "SELECT id FROM student WHERE email = ?", (email,)
    ).fetchall()
    if not existing:
        return jsonify({"error": "email not found"}), 404

    courses = _student_courses(db, email)
    if course_code and course_code not in courses:
        return jsonify({
            "error": f"student is not enrolled in course {course_code}",
        }), 400

    collision = db.execute(
        f"SELECT email FROM student "
        f"WHERE barcode_id = ? AND email != ? "
        f"AND course_code IN ({','.join('?' * len(courses))}) LIMIT 1",
        [virtual, email] + courses,
    ).fetchone()
    if collision:
        return jsonify({
            "error": "barcode already claimed by another student in this course",
        }), 409

    absent_before = _compute_attendance_delta(db, email)
    db.execute(
        "UPDATE student SET barcode_id = ? WHERE email = ?",
        (virtual, email),
    )
    if course_code:
        _write_attendance_today(db, virtual, course_code)
    db.commit()
    absent_after = _compute_attendance_delta(db, email)

    return jsonify({
        "success": True,
        "email": email,
        "barcode_id": virtual,
        "rows_updated": len(existing),
        "attendance_marked": bool(course_code),
        "attendance_delta": {
            "absent_before": absent_before,
            "absent_after": absent_after,
        },
    })


@app.route("/claim-physical-barcode", methods=["POST"])
def claim_physical_barcode():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    submitted = (data.get("physical_barcode_id") or "").strip()
    if not email or not submitted:
        return jsonify({"error": "email and physical_barcode_id required"}), 400

    db = get_db()
    rows = db.execute(
        "SELECT DISTINCT course_code FROM student WHERE email = ?",
        (email,),
    ).fetchall()
    if not rows:
        return jsonify({"error": "email not found"}), 404
    courses = [r["course_code"] for r in rows]

    # Snapshot the prior physical barcode so we can flag overwrites in the
    # response (UI can show "you replaced your earlier card").
    prev_row = db.execute(
        "SELECT DISTINCT physical_barcode_id FROM student "
        "WHERE email = ? AND physical_barcode_id IS NOT NULL "
        "AND physical_barcode_id != '' LIMIT 1",
        (email,),
    ).fetchone()
    previous_barcode = prev_row[0] if prev_row else None

    variants = normalize_barcode_variants(submitted)
    matched = None
    for variant in sorted(variants):
        hit = db.execute(
            f"SELECT 1 FROM attendance WHERE student_id = ? "
            f"AND course_code IN ({','.join('?' * len(courses))}) LIMIT 1",
            [variant] + courses,
        ).fetchone()
        if hit:
            matched = variant
            break

    canonical = normalize_barcode(submitted)
    # Reject submissions that normalize to empty, None, or a single "0".
    # These pass the non-empty submitted check but produce no useful link.
    if not canonical or canonical == "0":
        return jsonify({
            "error": "barcode must contain digits and not be all zeros",
        }), 400
    to_save = matched or canonical or submitted

    # Prevent barcode theft: the same physical barcode cannot be claimed
    # by two different students in the same course. Re-claim by the same
    # email is fine (handled by the UPDATE matching on email).
    collision = db.execute(
        f"SELECT email FROM student "
        f"WHERE physical_barcode_id = ? AND email != ? "
        f"AND course_code IN ({','.join('?' * len(courses))}) LIMIT 1",
        [to_save, email] + courses,
    ).fetchone()
    if collision:
        return jsonify({
            "error": "barcode already claimed by another student in this course",
        }), 409

    absent_before = _compute_attendance_delta(db, email)
    db.execute(
        "UPDATE student SET physical_barcode_id = ? WHERE email = ?",
        (to_save, email),
    )
    db.commit()
    absent_after = _compute_attendance_delta(db, email)

    db.execute(
        "INSERT INTO claim_log (attempted_at, email, course_code, "
        "submitted_barcode, variants_tried, matched_barcode, "
        "absent_before, absent_after) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (datetime.utcnow().isoformat() + "Z",
         email, ",".join(courses), submitted, json.dumps(sorted(variants)),
         matched, absent_before, absent_after),
    )
    db.commit()

    return jsonify({
        "linked": True,
        "physical_barcode_id": to_save,
        "matched": matched is not None,
        "replaced_previous": bool(previous_barcode and previous_barcode != to_save),
        "attendance_delta": {
            "absent_before": absent_before, "absent_after": absent_after,
        },
    })


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5001")), debug=True)
