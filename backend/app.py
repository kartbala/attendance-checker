"""Student Attendance Checker API."""

import os
import re
import sqlite3
from pathlib import Path

from flask import Flask, g, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).parent / "data" / "checker.db"))
SYNC_API_KEY = os.environ.get("SYNC_API_KEY", "dev-key")

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
    conn.commit()
    conn.close()


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
    total_sessions = len(all_dates)

    # Check both virtual and physical card barcodes
    barcodes = [barcode_id]
    if physical_barcode_id:
        barcodes.append(physical_barcode_id)
    placeholders = ",".join("?" * len(barcodes))
    attended_rows = db.execute(
        f"SELECT DISTINCT scan_date FROM attendance WHERE student_id IN ({placeholders}) AND course_code = ?",
        barcodes + [course_code],
    ).fetchall()
    attended_dates = {row["scan_date"] for row in attended_rows}

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
        dates.append({"date": d, "status": status})

    excused_count = len(set(excused_map.keys()) & set(all_dates))
    sessions_attended = len(attended_dates & set(all_dates)) - excused_count
    unexcused_count = total_sessions - sessions_attended - excused_count

    effective_rate = (
        (sessions_attended + excused_count) / total_sessions
        if total_sessions > 0
        else 1.0
    )

    return jsonify({
        "student_name": f"{student['first_name']} {student['last_name']}",
        "course_code": course_code,
        "course_name": student["course_name"],
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
        f"<p class='meta'>{email} &middot; HUID: <code>{students[0]['huid'] or '(none)'}</code> &middot; "
        f"Virtual barcode: <code>{students[0]['barcode_id'] or '(none)'}</code>"
        + (f" &middot; Physical barcode: <code>{students[0]['physical_barcode_id']}</code>" if students[0]['physical_barcode_id'] else "")
        + "</p>",
    ]

    for s in students:
        course = s["course_code"]
        bcs = [b for b in (s["barcode_id"], s["physical_barcode_id"]) if b]

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
            stamp_str = "<br>".join(t[:19] for t in stamps) if stamps else "&mdash;"
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

    out.append(
        "<p class='meta' style='margin-top:2rem'>Note: scan timestamps from the scanner "
        "are currently recorded as midnight UTC for the session date &mdash; individual scan "
        "instants are not captured by the upstream Google Sheets API.</p>"
    )
    out.append("</body></html>")
    return "".join(out)


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


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
