"""Tests for the leading-zero barcode fix.

Run:
    cd backend && python -m unittest test_normalize.py -v
"""

import importlib
import os
import sqlite3
import tempfile
import unittest


class NormalizeBarcodeUnitTests(unittest.TestCase):
    """Pure-function behavior of normalize_barcode."""

    def setUp(self):
        import app
        self.normalize = app.normalize_barcode

    def test_strips_leading_zero(self):
        self.assertEqual(self.normalize("07142851387095"), "7142851387095")

    def test_idempotent(self):
        once = self.normalize("07142851387095")
        self.assertEqual(self.normalize(once), once)

    def test_multiple_leading_zeros(self):
        self.assertEqual(self.normalize("0007142851387095"), "7142851387095")

    def test_no_leading_zero_unchanged(self):
        self.assertEqual(self.normalize("7142851387095"), "7142851387095")

    def test_none_preserved(self):
        self.assertIsNone(self.normalize(None))

    def test_empty_preserved(self):
        self.assertEqual(self.normalize(""), "")

    def test_all_zeros_collapses_to_single_zero(self):
        self.assertEqual(self.normalize("0000"), "0")


def _fresh_app(db_path):
    """Reimport app module with a fresh DB_PATH so module-level init_db()
    runs against an isolated temp database."""
    os.environ["DB_PATH"] = db_path
    os.environ["SYNC_API_KEY"] = "testkey"
    if "app" in _imported_modules():
        import app
        importlib.reload(app)
    else:
        import app  # noqa: F401
    import app as app_mod
    return app_mod


def _imported_modules():
    import sys
    return sys.modules


class LeadingZeroEndToEndTest(unittest.TestCase):
    """Nia's scenario: registered with 14-digit leading-zero barcode, scans
    landed as 13-digit. Before the fix, /attendance returned 0 present.
    After the fix, all scans count."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)
        self.app_mod = _fresh_app(self.db_path)
        self.client = self.app_mod.app.test_client()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.db_path + suffix)
            except FileNotFoundError:
                pass

    def _push(self, payload):
        return self.client.post(
            "/sync/push", json=payload, headers={"X-Sync-Key": "testkey"}
        )

    def test_nia_leading_zero_barcode_matches_stripped_scans(self):
        course = "INFO-335-04"
        # 6 enrollees so the >=5 session-threshold in /attendance passes
        students = [
            {
                "email": f"s{i}@bison.howard.edu",
                "first_name": f"S{i}", "last_name": "Test",
                "course_code": course, "course_name": "POM",
                "barcode_id": f"99999999999{i:02d}",
                "physical_barcode_id": None, "huid": f"@0000000{i}",
            }
            for i in range(5)
        ] + [
            # Nia: registered with leading zero (14-digit)
            {
                "email": "nia.peake@bison.howard.edu",
                "first_name": "Nia", "last_name": "Peake",
                "course_code": course, "course_name": "POM",
                "barcode_id": "07142851387095",
                "physical_barcode_id": None, "huid": "@03109035",
            },
        ]

        dates = ["2026-02-03", "2026-02-05", "2026-02-10"]
        attendance = []
        for d in dates:
            for i in range(5):
                attendance.append({
                    "student_id": f"99999999999{i:02d}",
                    "course_code": course, "scan_date": d,
                    "scan_timestamp": f"{d}T19:11:32Z",
                })
            # Nia's scans come in stripped (13 digits) from the classroom scanner
            attendance.append({
                "student_id": "7142851387095",
                "course_code": course, "scan_date": d,
                "scan_timestamp": f"{d}T19:15:00Z",
            })

        r = self._push({"students": students, "attendance": attendance})
        self.assertEqual(r.status_code, 200)

        r = self.client.get(
            "/attendance",
            query_string={
                "email": "nia.peake@bison.howard.edu",
                "course_code": course,
            },
        )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["total_sessions"], 3)
        self.assertEqual(body["sessions_attended"], 3,
                         f"Nia should be present on all 3 sessions, got: {body}")
        # Her registered barcode should be stored in canonical (stripped) form
        self.assertEqual(body["barcodes_registered"], ["7142851387095"])


class MigrationBackfillTest(unittest.TestCase):
    """Legacy rows written before the fix should be normalized in place."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.db_path + suffix)
            except FileNotFoundError:
                pass

    def test_backfill_collapses_leading_zeros_and_dedupes(self):
        # Seed pre-fix shape by running app once, then mutating directly.
        os.environ["DB_PATH"] = self.db_path
        os.environ["SYNC_API_KEY"] = "testkey"
        import app
        importlib.reload(app)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO student (email, first_name, last_name, course_code, "
                "course_name, barcode_id, physical_barcode_id, huid) VALUES "
                "('nia@bison.howard.edu','Nia','Peake','INFO-335-04','POM',"
                "'07142851387095', NULL, '@03109035')"
            )
            conn.execute(
                "INSERT INTO attendance (student_id, course_code, scan_date, scan_timestamp) "
                "VALUES ('07142851387095','INFO-335-04','2026-01-10','2026-01-10T19:00:00Z')"
            )
            # Day 2: BOTH forms exist (scanner hiccup); migration must keep one
            conn.execute(
                "INSERT INTO attendance (student_id, course_code, scan_date, scan_timestamp) "
                "VALUES ('07142851387095','INFO-335-04','2026-01-12','2026-01-12T19:00:00Z')"
            )
            conn.execute(
                "INSERT INTO attendance (student_id, course_code, scan_date, scan_timestamp) "
                "VALUES ('7142851387095','INFO-335-04','2026-01-12','2026-01-12T19:05:00Z')"
            )
            conn.commit()

        # Re-init triggers migration
        app.init_db()

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT barcode_id FROM student WHERE email='nia@bison.howard.edu'"
            ).fetchone()
            self.assertEqual(row[0], "7142851387095",
                             "student.barcode_id should be stripped")

            rows = conn.execute(
                "SELECT student_id, scan_date FROM attendance ORDER BY scan_date"
            ).fetchall()
            self.assertEqual(
                rows, [("7142851387095", "2026-01-10"), ("7142851387095", "2026-01-12")],
                "attendance rows should be normalized, with the day-2 duplicate collapsed",
            )

            # Idempotency: running migration a second time is a no-op
            app._migrate_normalize_barcodes(conn)
            conn.commit()
            rows2 = conn.execute(
                "SELECT student_id, scan_date FROM attendance ORDER BY scan_date"
            ).fetchall()
            self.assertEqual(rows, rows2)


class AdminLinkPhysicalTest(unittest.TestCase):
    """POST /admin/link-physical sets physical_barcode_id for all student rows
    with the given email, returns attendance delta."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)
        self.app_mod = _fresh_app(self.db_path)
        self.client = self.app_mod.app.test_client()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.db_path + suffix)
            except FileNotFoundError:
                pass

    def _seed_course_with_orphan_scans(self):
        """5 registered + 1 target student. The target has 3 physical-card
        scans in the attendance table under barcode '9988776655' that are
        currently orphaned (not linked to her student row)."""
        course = "INFO-335-04"
        students = [
            {"email": f"s{i}@bison.howard.edu", "first_name": f"S{i}",
             "last_name": "T", "course_code": course, "course_name": "POM",
             "barcode_id": f"11111111111{i:02d}", "physical_barcode_id": None,
             "huid": f"@0000000{i}"}
            for i in range(5)
        ] + [
            {"email": "charrikka@bison.howard.edu", "first_name": "Charrikka",
             "last_name": "Gordon", "course_code": course, "course_name": "POM",
             "barcode_id": "7142851387095", "physical_barcode_id": None,
             "huid": "@03109999"},
        ]
        attendance = []
        for d in ("2026-02-03", "2026-02-05", "2026-02-10"):
            for i in range(5):
                attendance.append({
                    "student_id": f"11111111111{i:02d}", "course_code": course,
                    "scan_date": d, "scan_timestamp": f"{d}T19:11:32Z",
                })
            attendance.append({
                "student_id": "9988776655", "course_code": course,
                "scan_date": d, "scan_timestamp": f"{d}T19:15:00Z",
            })
        self.client.post("/sync/push", json={
            "students": students, "attendance": attendance,
        }, headers={"X-Sync-Key": "testkey"})

    def test_links_physical_barcode_and_returns_delta(self):
        self._seed_course_with_orphan_scans()
        r = self.client.post(
            "/admin/link-physical",
            json={"email": "charrikka@bison.howard.edu",
                  "physical_barcode_id": "9988776655"},
            headers={"X-Sync-Key": "testkey"},
        )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["attendance_delta"]["absent_before"], 3)
        self.assertEqual(body["attendance_delta"]["absent_after"], 0)

        # /attendance now reflects the link
        r = self.client.get(
            "/attendance",
            query_string={"email": "charrikka@bison.howard.edu"},
        )
        body = r.get_json()
        self.assertEqual(body["sessions_attended"], 3)

    def test_rejects_missing_auth(self):
        self._seed_course_with_orphan_scans()
        r = self.client.post(
            "/admin/link-physical",
            json={"email": "charrikka@bison.howard.edu",
                  "physical_barcode_id": "9988776655"},
        )
        self.assertEqual(r.status_code, 401)

    def test_rejects_bad_auth(self):
        self._seed_course_with_orphan_scans()
        r = self.client.post(
            "/admin/link-physical",
            json={"email": "charrikka@bison.howard.edu",
                  "physical_barcode_id": "9988776655"},
            headers={"X-Sync-Key": "wrong"},
        )
        self.assertEqual(r.status_code, 401)

    def test_idempotent(self):
        self._seed_course_with_orphan_scans()
        for _ in range(2):
            r = self.client.post(
                "/admin/link-physical",
                json={"email": "charrikka@bison.howard.edu",
                      "physical_barcode_id": "9988776655"},
                headers={"X-Sync-Key": "testkey"},
            )
            self.assertEqual(r.status_code, 200)
        r = self.client.get(
            "/attendance",
            query_string={"email": "charrikka@bison.howard.edu"},
        )
        self.assertEqual(r.get_json()["sessions_attended"], 3)

    def test_404_on_unknown_email(self):
        self._seed_course_with_orphan_scans()
        r = self.client.post(
            "/admin/link-physical",
            json={"email": "nobody@bison.howard.edu",
                  "physical_barcode_id": "9988776655"},
            headers={"X-Sync-Key": "testkey"},
        )
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
