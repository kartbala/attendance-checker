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


class EnrollRouteTest(unittest.TestCase):
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

    def test_enroll_serves_static_html(self):
        r = self.client.get("/enroll")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"<h1>Bulk Enroll", r.data)
        self.assertIn(b"enroll-form", r.data)
        r.close()


class ClaimLogSchemaTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)
        self.app_mod = _fresh_app(self.db_path)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.db_path + suffix)
            except FileNotFoundError:
                pass

    def test_claim_log_table_exists(self):
        with sqlite3.connect(self.db_path) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(claim_log)")]
        self.assertIn("email", cols)
        self.assertIn("submitted_barcode", cols)
        self.assertIn("variants_tried", cols)
        self.assertIn("matched_barcode", cols)
        self.assertIn("absent_before", cols)
        self.assertIn("absent_after", cols)


class NormalizeVariantsTest(unittest.TestCase):
    def setUp(self):
        import app
        self.variants = app.normalize_barcode_variants

    def test_strips_non_digits(self):
        v = self.variants("71-42851*387095")
        self.assertIn("7142851387095", v)

    def test_strips_leading_zeros(self):
        v = self.variants("007142851387095")
        self.assertIn("7142851387095", v)

    def test_includes_check_digit_variant(self):
        v = self.variants("7142851387095")
        self.assertIn("714285138709", v)  # trailing digit stripped

    def test_includes_symbology_prefix_variant(self):
        v = self.variants("7142851387095")
        self.assertIn("142851387095", v)  # leading digit stripped

    def test_handles_none_and_empty(self):
        self.assertEqual(self.variants(None), set())
        self.assertEqual(self.variants(""), set())
        self.assertEqual(self.variants("---"), set())

    def test_short_barcode_skips_trim_variants(self):
        """For very short inputs, trim variants would reduce to <3 chars --
        too ambiguous to match safely. Skip them."""
        v = self.variants("12")
        self.assertEqual(v, {"12"})

    def test_returns_set(self):
        self.assertIsInstance(self.variants("7142851387095"), set)


class ClaimPhysicalBarcodeTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)
        self.app_mod = _fresh_app(self.db_path)
        self.client = self.app_mod.app.test_client()
        self._seed()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.db_path + suffix)
            except FileNotFoundError:
                pass

    def _seed(self):
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

    def test_matches_and_links(self):
        r = self.client.post("/claim-physical-barcode", json={
            "email": "charrikka@bison.howard.edu",
            "physical_barcode_id": "9988776655",
        })
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["linked"])
        self.assertEqual(body["attendance_delta"]["absent_before"], 3)
        self.assertEqual(body["attendance_delta"]["absent_after"], 0)

    def test_check_digit_variant_matches(self):
        """Student's phone camera reads the physical card and produces a
        15-digit value (has an extra trailing digit compared to what the
        classroom scanner produces). Variant matching should still find
        the orphan."""
        r = self.client.post("/claim-physical-barcode", json={
            "email": "charrikka@bison.howard.edu",
            "physical_barcode_id": "99887766550",  # one extra trailing digit
        })
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["linked"])
        self.assertEqual(body["attendance_delta"]["absent_after"], 0)

    def test_no_match_still_saves_and_returns_zero_delta(self):
        # A non-zero digit string with no attendance match still saves and
        # returns a zero delta. (All-zero strings are rejected with 400 --
        # see test_400_on_empty_after_normalize.)
        r = self.client.post("/claim-physical-barcode", json={
            "email": "charrikka@bison.howard.edu",
            "physical_barcode_id": "5555555555",
        })
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["linked"])
        self.assertEqual(
            body["attendance_delta"]["absent_before"]
            - body["attendance_delta"]["absent_after"], 0)

    def test_claim_log_populated(self):
        self.client.post("/claim-physical-barcode", json={
            "email": "charrikka@bison.howard.edu",
            "physical_barcode_id": "9988776655",
        })
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT email, submitted_barcode, matched_barcode, "
                "absent_before, absent_after FROM claim_log"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "charrikka@bison.howard.edu")
        self.assertEqual(rows[0][1], "9988776655")
        self.assertEqual(rows[0][2], "9988776655")

    def test_404_on_unknown_email(self):
        r = self.client.post("/claim-physical-barcode", json={
            "email": "nobody@bison.howard.edu",
            "physical_barcode_id": "9988776655",
        })
        self.assertEqual(r.status_code, 404)

    def test_409_on_collision_with_another_student(self):
        # First student claims the barcode successfully
        r = self.client.post("/claim-physical-barcode", json={
            "email": "s0@bison.howard.edu",
            "physical_barcode_id": "9988776655",
        })
        self.assertEqual(r.status_code, 200)
        # Second student tries to claim the SAME barcode -> 409
        r = self.client.post("/claim-physical-barcode", json={
            "email": "charrikka@bison.howard.edu",
            "physical_barcode_id": "9988776655",
        })
        self.assertEqual(r.status_code, 409)

    def test_same_student_can_reclaim(self):
        # Charrikka claims once, then claims again with the same card -- OK
        r = self.client.post("/claim-physical-barcode", json={
            "email": "charrikka@bison.howard.edu",
            "physical_barcode_id": "9988776655",
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json().get("replaced_previous", False))
        r = self.client.post("/claim-physical-barcode", json={
            "email": "charrikka@bison.howard.edu",
            "physical_barcode_id": "9988776655",
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json().get("replaced_previous", False))

    def test_replaced_previous_true_when_changing_card(self):
        # Claim first card
        self.client.post("/claim-physical-barcode", json={
            "email": "charrikka@bison.howard.edu",
            "physical_barcode_id": "9988776655",
        })
        # Claim a different card -> replaced_previous True
        r = self.client.post("/claim-physical-barcode", json={
            "email": "charrikka@bison.howard.edu",
            "physical_barcode_id": "1234567890",
        })
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["replaced_previous"])

    def test_400_on_empty_after_normalize(self):
        # All-zeros and empty-string submissions are rejected
        r = self.client.post("/claim-physical-barcode", json={
            "email": "charrikka@bison.howard.edu",
            "physical_barcode_id": "000000",
        })
        self.assertEqual(r.status_code, 400)


class AttendanceResponseFieldsTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)
        self.app_mod = _fresh_app(self.db_path)
        self.client = self.app_mod.app.test_client()
        # Reuse seed from ClaimPhysicalBarcodeTest -- copy-paste here
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

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.db_path + suffix)
            except FileNotFoundError:
                pass

    def test_response_includes_has_physical_barcode_false(self):
        r = self.client.get("/attendance",
                            query_string={"email": "charrikka@bison.howard.edu"})
        body = r.get_json()
        self.assertIn("has_physical_barcode", body)
        self.assertFalse(body["has_physical_barcode"])

    def test_response_includes_section_orphan_count(self):
        r = self.client.get("/attendance",
                            query_string={"email": "charrikka@bison.howard.edu"})
        body = r.get_json()
        self.assertIn("section_orphan_count", body)
        # 9988776655 is the only orphan (5 registered students + Charrikka's
        # virtual 7142851387095 have no scans under that student_id)
        self.assertEqual(body["section_orphan_count"], 1)

    def test_has_physical_barcode_true_after_claim(self):
        self.client.post("/claim-physical-barcode", json={
            "email": "charrikka@bison.howard.edu",
            "physical_barcode_id": "9988776655",
        })
        r = self.client.get("/attendance",
                            query_string={"email": "charrikka@bison.howard.edu"})
        body = r.get_json()
        self.assertTrue(body["has_physical_barcode"])
        # Once linked, orphan count for this section drops to 0
        self.assertEqual(body["section_orphan_count"], 0)


class DebugClaimsRouteTest(unittest.TestCase):
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

    def test_requires_key(self):
        r = self.client.get("/debug/claims")
        self.assertEqual(r.status_code, 401)

    def test_renders_table_with_key(self):
        r = self.client.get("/debug/claims", query_string={"key": "testkey"})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Last 50 claim attempts", r.data)


class SkipReasonColumnTest(unittest.TestCase):
    def test_column_exists(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(db_path)
        _fresh_app(db_path)
        with sqlite3.connect(db_path) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(student)")]
        self.assertIn("physical_barcode_skip_reason", cols)
        for suffix in ("", "-wal", "-shm"):
            try: os.unlink(db_path + suffix)
            except FileNotFoundError: pass


class SyncPullSkipReasonTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)
        self.app_mod = _fresh_app(self.db_path)
        self.client = self.app_mod.app.test_client()
        # Seed directly: sync/push silently drops physical_barcode_skip_reason,
        # so we insert rows directly into the DB after init_db() has run.
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO student (email, first_name, last_name, course_code, course_name, "
                "barcode_id, physical_barcode_id, physical_barcode_skip_reason, huid) VALUES "
                "('skipper@bison.howard.edu','S','Kip','INFO-335-04','POM',"
                "'100001',NULL,'privacy-screen','@00000001')"
            )
            conn.execute(
                "INSERT INTO student (email, first_name, last_name, course_code, course_name, "
                "barcode_id, physical_barcode_id, physical_barcode_skip_reason, huid) VALUES "
                "('physical@bison.howard.edu','P','Hys','INFO-335-04','POM',"
                "'100002','200002',NULL,'@00000002')"
            )
            conn.commit()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.db_path + suffix)
            except FileNotFoundError:
                pass

    def test_sync_pull_includes_skip_reason(self):
        r = self.client.get("/sync/pull", headers={"X-Sync-Key": "testkey"})
        self.assertEqual(r.status_code, 200)
        by_email = {x["email"]: x for x in r.get_json()["registrations"]}
        self.assertIn("skipper@bison.howard.edu", by_email)
        self.assertIn("physical@bison.howard.edu", by_email)
        self.assertEqual(
            by_email["skipper@bison.howard.edu"]["physical_barcode_skip_reason"],
            "privacy-screen",
        )
        self.assertIsNone(
            by_email["physical@bison.howard.edu"]["physical_barcode_skip_reason"]
        )


class RegisterWithSkipReasonTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)
        self.app_mod = _fresh_app(self.db_path)
        self.client = self.app_mod.app.test_client()
        # Seed one enrolled student
        self.client.post("/sync/push", json={
            "students": [{
                "email": "s1@bison.howard.edu", "first_name": "S", "last_name": "One",
                "course_code": "INFO-335-04", "course_name": "POM",
                "barcode_id": None, "physical_barcode_id": None, "huid": None,
            }], "attendance": [],
        }, headers={"X-Sync-Key": "testkey"})

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try: os.unlink(self.db_path + suffix)
            except FileNotFoundError: pass

    def test_rejects_without_physical_or_skip(self):
        r = self.client.post("/register", json={
            "email": "s1@bison.howard.edu", "huid": "@01234567",
            "barcode_id": "123456",
        })
        body = r.get_json()
        self.assertEqual(r.status_code, 400)
        self.assertTrue(any("physical_barcode_skip_reason" in d for d in body.get("details", [])))

    def test_accepts_with_physical(self):
        r = self.client.post("/register", json={
            "email": "s1@bison.howard.edu", "huid": "@01234567",
            "barcode_id": "123456", "physical_barcode_id": "987654",
        })
        self.assertEqual(r.status_code, 200)

    def test_accepts_with_skip_reason(self):
        r = self.client.post("/register", json={
            "email": "s1@bison.howard.edu", "huid": "@01234567",
            "barcode_id": "123456",
            "physical_barcode_skip_reason": "privacy-screen",
        })
        self.assertEqual(r.status_code, 200)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT physical_barcode_skip_reason, physical_barcode_id "
                "FROM student WHERE email = ?", ("s1@bison.howard.edu",)
            ).fetchone()
        self.assertEqual(row[0], "privacy-screen")
        self.assertIsNone(row[1])


class RosterTest(unittest.TestCase):
    """GET /admin/roster returns an HTML table of all students with status."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)
        self.app_mod = _fresh_app(self.db_path)
        self.client = self.app_mod.app.test_client()
        # Seed students covering all four status cases via direct INSERT so we
        # can control physical_barcode_skip_reason (sync/push drops that field).
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO student (email, first_name, last_name, course_code, course_name, "
                "barcode_id, physical_barcode_id, physical_barcode_skip_reason, huid) VALUES "
                "(?,?,?,?,?,?,?,?,?)",
                [
                    # physical: has physical_barcode_id
                    ("physical@bison.howard.edu", "P", "Hys", "INFO-335-04", "POM",
                     "100001", "200001", None, "@00000001"),
                    # skipped: has skip_reason, no physical
                    ("skipper@bison.howard.edu", "S", "Kip", "INFO-335-04", "POM",
                     "100002", None, "privacy-screen", "@00000002"),
                    # virtual only: has virtual barcode, no physical, no skip
                    ("virtual@bison.howard.edu", "V", "Irt", "INFO-335-04", "POM",
                     "100003", None, None, "@00000003"),
                    # unregistered: no virtual barcode
                    ("unreg@bison.howard.edu", "U", "Nreg", "INFO-335-04", "POM",
                     None, None, None, None),
                ],
            )
            conn.commit()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.db_path + suffix)
            except FileNotFoundError:
                pass

    def test_requires_key(self):
        r = self.client.get("/admin/roster")
        self.assertEqual(r.status_code, 401)

    def test_wrong_key_rejected(self):
        r = self.client.get("/admin/roster", query_string={"key": "wrongkey"})
        self.assertEqual(r.status_code, 401)

    def test_200_with_correct_key(self):
        r = self.client.get("/admin/roster", query_string={"key": "testkey"})
        self.assertEqual(r.status_code, 200)

    def test_contains_all_emails(self):
        r = self.client.get("/admin/roster", query_string={"key": "testkey"})
        body = r.data
        self.assertIn(b"physical@bison.howard.edu", body)
        self.assertIn(b"skipper@bison.howard.edu", body)
        self.assertIn(b"virtual@bison.howard.edu", body)
        self.assertIn(b"unreg@bison.howard.edu", body)

    def test_status_strings_present(self):
        r = self.client.get("/admin/roster", query_string={"key": "testkey"})
        body = r.data
        self.assertIn(b"physical", body)
        self.assertIn(b"skipped: privacy-screen", body)
        self.assertIn(b"virtual only", body)
        self.assertIn(b"unregistered", body)

    def test_summary_counts(self):
        r = self.client.get("/admin/roster", query_string={"key": "testkey"})
        body = r.data.decode()
        # Total enrolled: 4, Physical: 1, Skipped: 1, Virtual only: 1, Unregistered: 1
        self.assertIn("Total enrolled: 4", body)
        self.assertIn("Physical: 1", body)
        self.assertIn("Skipped: 1", body)
        self.assertIn("Virtual only: 1", body)
        self.assertIn("Unregistered: 1", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
