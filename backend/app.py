"""Student Attendance Checker API."""

import os
import re
import sqlite3
from pathlib import Path

from flask import Flask, g, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DB_PATH = Path(__file__).parent / "data" / "checker.db"
SYNC_API_KEY = os.environ.get("SYNC_API_KEY", "dev-key")

SCHEMA = """
CREATE TABLE IF NOT EXISTS student (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    first_name TEXT,
    last_name TEXT,
    course_code TEXT,
    course_name TEXT,
    barcode_id TEXT,
    huid TEXT
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
    conn.executescript(SCHEMA)
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

    errors = []
    if not EMAIL_RE.match(email):
        errors.append("Email must be a @bison.howard.edu address")
    if not HUID_RE.match(huid):
        errors.append("HUID must be @ followed by 8 digits (e.g. @03107801)")
    if not BARCODE_RE.match(barcode_id):
        errors.append("Barcode must be numeric")
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    db = get_db()
    student = db.execute(
        "SELECT id, first_name, last_name, course_code, course_name FROM student WHERE email = ?",
        (email,),
    ).fetchone()

    if not student:
        return jsonify({
            "error": "Email not found -- are you enrolled in Dr. B's class?"
        }), 404

    db.execute(
        "UPDATE student SET barcode_id = ?, huid = ? WHERE email = ?",
        (barcode_id, huid, email),
    )
    db.commit()

    return jsonify({
        "success": True,
        "student_name": f"{student['first_name']} {student['last_name']}",
        "course_code": student["course_code"],
        "course_name": student["course_name"],
    })


@app.route("/attendance")
def attendance():
    email = (request.args.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "email parameter required"}), 400

    db = get_db()
    student = db.execute(
        "SELECT first_name, last_name, course_code, course_name, barcode_id FROM student WHERE email = ?",
        (email,),
    ).fetchone()

    if not student:
        return jsonify({"error": "Email not found"}), 404

    if not student["barcode_id"]:
        return jsonify({
            "error": "not_registered",
            "message": "You need to register your barcode first.",
        }), 400

    course_code = student["course_code"]
    barcode_id = student["barcode_id"]

    all_sessions = db.execute(
        "SELECT DISTINCT scan_date FROM attendance WHERE course_code = ? ORDER BY scan_date",
        (course_code,),
    ).fetchall()
    all_dates = [row["scan_date"] for row in all_sessions]
    total_sessions = len(all_dates)

    attended_rows = db.execute(
        "SELECT DISTINCT scan_date FROM attendance WHERE student_id = ? AND course_code = ?",
        (barcode_id, course_code),
    ).fetchall()
    attended_dates = {row["scan_date"] for row in attended_rows}

    excused_rows = db.execute(
        "SELECT absence_date, absence_type, reason FROM excused_absence WHERE student_email = ? AND course_code = ?",
        (email, course_code),
    ).fetchall()
    excused_map = {row["absence_date"]: row for row in excused_rows}

    dates = []
    for d in all_dates:
        if d in attended_dates:
            status = "present"
        elif d in excused_map:
            status = "excused"
        else:
            status = "absent"
        dates.append({"date": d, "status": status})

    sessions_attended = len(attended_dates & set(all_dates))
    excused_count = len(set(excused_map.keys()) & set(all_dates))
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
        db.execute("""
            INSERT INTO student (email, first_name, last_name, course_code, course_name, barcode_id, huid)
            VALUES (:email, :first_name, :last_name, :course_code, :course_name, :barcode_id, :huid)
            ON CONFLICT(email) DO UPDATE SET
                first_name=excluded.first_name, last_name=excluded.last_name,
                course_code=excluded.course_code, course_name=excluded.course_name,
                barcode_id=COALESCE(student.barcode_id, excluded.barcode_id),
                huid=COALESCE(student.huid, excluded.huid)
        """, s)
        counts["students"] += 1

    for a in data.get("attendance", []):
        db.execute("""
            INSERT OR IGNORE INTO attendance (student_id, course_code, scan_date, scan_timestamp)
            VALUES (:student_id, :course_code, :scan_date, :scan_timestamp)
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
        "SELECT email, barcode_id, huid FROM student WHERE barcode_id IS NOT NULL AND barcode_id != ''"
    ).fetchall()

    return jsonify({
        "registrations": [
            {"email": r["email"], "barcode_id": r["barcode_id"], "huid": r["huid"]}
            for r in rows
        ]
    })


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
