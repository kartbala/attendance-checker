# Bulk Enroll -- Virtual Barcodes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a virtual-barcode-only bulk-enrollment page (`/enroll-virtual`) paralleling the shipped physical flow, with a new admin endpoint that writes `barcode_id`, checks collisions, and returns retroactive attendance delta.

**Architecture:** Mirror the shipped physical path. New Flask route serves a near-clone HTML page; new admin endpoint updates `student.barcode_id` for all rows with the given email after a collision check, using the existing `_compute_attendance_delta()` helper so retroactive scans count automatically. No changes to the existing physical flow.

**Tech Stack:** Flask, SQLite, Python `unittest`, vanilla JS (no build step), Render auto-deploy.

**Spec:** `Sandbox/attendance-checker/docs/2026-04-21-bulk-enroll-virtual-design.md`

---

## File Structure

**New files:**
- `backend/static/enroll-virtual.html` -- standalone HTML, no JS build. Single form, one barcode field.

**Modified files:**
- `backend/app.py` -- add two routes: `@app.route("/admin/link-virtual", methods=["POST"])` and `@app.route("/enroll-virtual")`.
- `backend/test_normalize.py` -- add `AdminLinkVirtualTest` class (~7 tests) and one `/enroll-virtual` route test in the existing `EnrollRouteTest` class (or new `EnrollVirtualRouteTest`).

**Unchanged:** `/enroll`, `/admin/link-physical`, `enroll.html`, `/register`, `/claim-physical-barcode`, the React frontend.

---

## Task 1: Happy-path test and stub endpoint for `/admin/link-virtual`

**Files:**
- Modify: `backend/test_normalize.py` -- add new class `AdminLinkVirtualTest` after line 331 (end of `AdminLinkPhysicalTest`).
- Modify: `backend/app.py` -- add new route after the existing `/admin/link-physical` block (around line 893).

- [ ] **Step 1: Write the failing happy-path test.**

Append to `backend/test_normalize.py`, after `class AdminLinkPhysicalTest` ends:

```python
class AdminLinkVirtualTest(unittest.TestCase):
    """POST /admin/link-virtual sets barcode_id (virtual) for all student rows
    with the given email, collision-checks, returns attendance delta."""

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

    def _seed_course_with_orphan_virtual_scans(self):
        """5 registered + 1 target student. The target ('anya') has no
        virtual barcode yet; 3 scans exist under virtual barcode
        '5551234567890' that are currently orphaned."""
        course = "INFO-335-04"
        students = [
            {"email": f"s{i}@bison.howard.edu", "first_name": f"S{i}",
             "last_name": "T", "course_code": course, "course_name": "POM",
             "barcode_id": f"22222222222{i:02d}", "physical_barcode_id": None,
             "huid": f"@1111111{i}"}
            for i in range(5)
        ] + [
            {"email": "anya@bison.howard.edu", "first_name": "Anya",
             "last_name": "Test", "course_code": course, "course_name": "POM",
             "barcode_id": None, "physical_barcode_id": None,
             "huid": "@03108888"},
        ]
        attendance = []
        for d in ("2026-02-03", "2026-02-05", "2026-02-10"):
            for i in range(5):
                attendance.append({
                    "student_id": f"22222222222{i:02d}", "course_code": course,
                    "scan_date": d, "scan_timestamp": f"{d}T19:11:32Z",
                })
            attendance.append({
                "student_id": "5551234567890", "course_code": course,
                "scan_date": d, "scan_timestamp": f"{d}T19:15:00Z",
            })
        self.client.post("/sync/push", json={
            "students": students, "attendance": attendance,
        }, headers={"X-Sync-Key": "testkey"})

    def test_links_virtual_barcode_and_returns_delta(self):
        self._seed_course_with_orphan_virtual_scans()
        r = self.client.post(
            "/admin/link-virtual",
            json={"email": "anya@bison.howard.edu",
                  "barcode_id": "5551234567890"},
            headers={"X-Sync-Key": "testkey"},
        )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["email"], "anya@bison.howard.edu")
        self.assertEqual(body["barcode_id"], "5551234567890")
        self.assertEqual(body["attendance_delta"]["absent_before"], 3)
        self.assertEqual(body["attendance_delta"]["absent_after"], 0)

        # /attendance now reflects the link
        r = self.client.get(
            "/attendance",
            query_string={"email": "anya@bison.howard.edu"},
        )
        body = r.get_json()
        self.assertEqual(body["sessions_attended"], 3)
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `cd "/Users/karthikbalasubramanian/Library/CloudStorage/GoogleDrive-karthik@balasubramanian.us/My Drive/Sandbox/attendance-checker/backend" && python -m unittest test_normalize.AdminLinkVirtualTest.test_links_virtual_barcode_and_returns_delta -v`

Expected: FAIL -- `404 NOT FOUND` on `/admin/link-virtual` (route doesn't exist).

- [ ] **Step 3: Add the minimal endpoint.**

In `backend/app.py`, add this route immediately after the existing `/admin/link-physical` block ends (after the closing `})` around line 892, before `@app.route("/claim-physical-barcode", ...)`):

```python
@app.route("/admin/link-virtual", methods=["POST"])
def admin_link_virtual():
    auth_err = require_sync_key()
    if auth_err:
        return auth_err
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    virtual = normalize_barcode((data.get("barcode_id") or "").strip())
    if not email or not virtual:
        return jsonify({"error": "email and barcode_id required"}), 400

    db = get_db()
    existing = db.execute(
        "SELECT id FROM student WHERE email = ?", (email,)
    ).fetchall()
    if not existing:
        return jsonify({"error": "email not found"}), 404

    absent_before = _compute_attendance_delta(db, email)
    db.execute(
        "UPDATE student SET barcode_id = ? WHERE email = ?",
        (virtual, email),
    )
    db.commit()
    absent_after = _compute_attendance_delta(db, email)

    return jsonify({
        "success": True,
        "email": email,
        "barcode_id": virtual,
        "rows_updated": len(existing),
        "attendance_delta": {
            "absent_before": absent_before,
            "absent_after": absent_after,
        },
    })
```

- [ ] **Step 4: Run the test to verify it passes.**

Run: `cd backend && python -m unittest test_normalize.AdminLinkVirtualTest.test_links_virtual_barcode_and_returns_delta -v`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
cd "/Users/karthikbalasubramanian/Library/CloudStorage/GoogleDrive-karthik@balasubramanian.us/My Drive/Sandbox/attendance-checker"
git add backend/app.py backend/test_normalize.py
git commit -m "feat(backend): /admin/link-virtual happy path

Add admin endpoint paralleling /admin/link-physical. Sets barcode_id
(virtual) for all student rows with the given email, returns
retroactive attendance delta via _compute_attendance_delta()."
```

---

## Task 2: Validation and auth error paths

**Files:**
- Modify: `backend/test_normalize.py` -- add 4 tests inside `AdminLinkVirtualTest`.

- [ ] **Step 1: Write the failing tests.**

Append these test methods inside `AdminLinkVirtualTest` (same class as Task 1):

```python
    def test_rejects_missing_auth(self):
        self._seed_course_with_orphan_virtual_scans()
        r = self.client.post(
            "/admin/link-virtual",
            json={"email": "anya@bison.howard.edu",
                  "barcode_id": "5551234567890"},
        )
        self.assertEqual(r.status_code, 401)

    def test_rejects_bad_auth(self):
        self._seed_course_with_orphan_virtual_scans()
        r = self.client.post(
            "/admin/link-virtual",
            json={"email": "anya@bison.howard.edu",
                  "barcode_id": "5551234567890"},
            headers={"X-Sync-Key": "wrong"},
        )
        self.assertEqual(r.status_code, 401)

    def test_rejects_missing_email(self):
        self._seed_course_with_orphan_virtual_scans()
        r = self.client.post(
            "/admin/link-virtual",
            json={"barcode_id": "5551234567890"},
            headers={"X-Sync-Key": "testkey"},
        )
        self.assertEqual(r.status_code, 400)

    def test_rejects_missing_barcode(self):
        self._seed_course_with_orphan_virtual_scans()
        r = self.client.post(
            "/admin/link-virtual",
            json={"email": "anya@bison.howard.edu"},
            headers={"X-Sync-Key": "testkey"},
        )
        self.assertEqual(r.status_code, 400)

    def test_404_on_unknown_email(self):
        self._seed_course_with_orphan_virtual_scans()
        r = self.client.post(
            "/admin/link-virtual",
            json={"email": "nobody@bison.howard.edu",
                  "barcode_id": "5551234567890"},
            headers={"X-Sync-Key": "testkey"},
        )
        self.assertEqual(r.status_code, 404)
```

- [ ] **Step 2: Run the tests.**

Run: `cd backend && python -m unittest test_normalize.AdminLinkVirtualTest -v`

Expected: PASS on all 6 (5 new + the happy path from Task 1). The endpoint already handles auth, missing fields, and 404 because it was written cleanly in Task 1.

If any of the 5 new tests fails, fix the endpoint -- but based on the Task 1 implementation (which mirrors `/admin/link-physical`), they should all pass without code changes.

- [ ] **Step 3: Commit.**

```bash
git add backend/test_normalize.py
git commit -m "test(backend): /admin/link-virtual validation and auth paths"
```

---

## Task 3: Collision check -- block typos landing on another student's barcode

**Files:**
- Modify: `backend/test_normalize.py` -- add 1 test to `AdminLinkVirtualTest`.
- Modify: `backend/app.py` -- add collision check inside `admin_link_virtual()`.

- [ ] **Step 1: Write the failing collision test.**

Append inside `AdminLinkVirtualTest`:

```python
    def test_rejects_collision_in_same_course(self):
        """If another student in the same course already has this
        barcode_id as their virtual, block with 409 (likely a typo)."""
        self._seed_course_with_orphan_virtual_scans()
        # s0 already has barcode_id "2222222222200" (from the seed).
        # Anya tries to register that same barcode -- reject.
        r = self.client.post(
            "/admin/link-virtual",
            json={"email": "anya@bison.howard.edu",
                  "barcode_id": "2222222222200"},
            headers={"X-Sync-Key": "testkey"},
        )
        self.assertEqual(r.status_code, 409)
        body = r.get_json()
        self.assertIn("already claimed", body["error"])

        # Anya's barcode_id was NOT updated.
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT barcode_id FROM student WHERE email = ?",
                ("anya@bison.howard.edu",),
            ).fetchone()
        self.assertIsNone(row["barcode_id"])
```

- [ ] **Step 2: Run the test to verify it fails.**

Run: `cd backend && python -m unittest test_normalize.AdminLinkVirtualTest.test_rejects_collision_in_same_course -v`

Expected: FAIL -- the endpoint currently returns 200 and overwrites, because there's no collision check yet.

- [ ] **Step 3: Add the collision check.**

In `backend/app.py`, modify `admin_link_virtual()` to collect courses and check for collisions BEFORE the UPDATE. Replace the body of the function (starting after the `existing` lookup) with:

```python
    db = get_db()
    existing = db.execute(
        "SELECT id FROM student WHERE email = ?", (email,)
    ).fetchall()
    if not existing:
        return jsonify({"error": "email not found"}), 404

    courses = [r["course_code"] for r in db.execute(
        "SELECT DISTINCT course_code FROM student WHERE email = ?",
        (email,),
    ).fetchall()]

    # Block typos that collide with another student's virtual barcode
    # in any of this student's courses. Re-linking the same email is
    # fine (email != ? clause).
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
    db.commit()
    absent_after = _compute_attendance_delta(db, email)

    return jsonify({
        "success": True,
        "email": email,
        "barcode_id": virtual,
        "rows_updated": len(existing),
        "attendance_delta": {
            "absent_before": absent_before,
            "absent_after": absent_after,
        },
    })
```

(Remove the earlier duplicate block from Task 1 -- the above is the full body of the function from `db = get_db()` onward.)

- [ ] **Step 4: Run the test.**

Run: `cd backend && python -m unittest test_normalize.AdminLinkVirtualTest.test_rejects_collision_in_same_course -v`

Expected: PASS.

- [ ] **Step 5: Run the full class to confirm nothing regressed.**

Run: `cd backend && python -m unittest test_normalize.AdminLinkVirtualTest -v`

Expected: PASS on all 7 tests.

- [ ] **Step 6: Commit.**

```bash
git add backend/app.py backend/test_normalize.py
git commit -m "feat(backend): collision check on /admin/link-virtual

Block 409 if another student in any of this student's courses already
has the submitted virtual barcode. Catches scan typos before they
silently steal another student's registration."
```

---

## Task 4: Re-link idempotency and barcode normalization tests

**Files:**
- Modify: `backend/test_normalize.py` -- add 2 tests to `AdminLinkVirtualTest`.

- [ ] **Step 1: Write the tests.**

Append inside `AdminLinkVirtualTest`:

```python
    def test_idempotent_same_email_same_barcode(self):
        """Re-linking the same email to the same barcode is allowed
        (the collision-check's email != ? clause handles this)."""
        self._seed_course_with_orphan_virtual_scans()
        for _ in range(2):
            r = self.client.post(
                "/admin/link-virtual",
                json={"email": "anya@bison.howard.edu",
                      "barcode_id": "5551234567890"},
                headers={"X-Sync-Key": "testkey"},
            )
            self.assertEqual(r.status_code, 200)
        # Attendance still correct after the second (no-op) call.
        r = self.client.get(
            "/attendance",
            query_string={"email": "anya@bison.howard.edu"},
        )
        self.assertEqual(r.get_json()["sessions_attended"], 3)

    def test_normalizes_leading_zeros(self):
        """Submitting "00005551234567890" stores and returns "5551234567890"."""
        self._seed_course_with_orphan_virtual_scans()
        r = self.client.post(
            "/admin/link-virtual",
            json={"email": "anya@bison.howard.edu",
                  "barcode_id": "00005551234567890"},
            headers={"X-Sync-Key": "testkey"},
        )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["barcode_id"], "5551234567890")
        # Retroactive attendance still matches because the seed scans
        # are under the canonical form.
        self.assertEqual(body["attendance_delta"]["absent_after"], 0)
```

- [ ] **Step 2: Run the tests.**

Run: `cd backend && python -m unittest test_normalize.AdminLinkVirtualTest -v`

Expected: PASS on all 9 tests (both new tests should pass without code changes, since normalization and the email-match idempotency are already in the endpoint).

- [ ] **Step 3: Commit.**

```bash
git add backend/test_normalize.py
git commit -m "test(backend): /admin/link-virtual idempotency and normalization"
```

---

## Task 5: `/enroll-virtual` route and static HTML page

**Files:**
- Create: `backend/static/enroll-virtual.html`.
- Modify: `backend/app.py` -- add `/enroll-virtual` route after the existing `/enroll` route (around line 776).
- Modify: `backend/test_normalize.py` -- add route test.

- [ ] **Step 1: Write the failing route test.**

Append to `backend/test_normalize.py` (inside the existing `EnrollRouteTest` class if you can find a clean spot, or add a new class after it):

```python
class EnrollVirtualRouteTest(unittest.TestCase):
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

    def test_serves_enroll_virtual_html(self):
        r = self.client.get("/enroll-virtual")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertIn("Virtual Barcodes", body)
        self.assertIn("/admin/link-virtual", body)
        self.assertIn('id="email"', body)
        self.assertIn('id="barcode"', body)
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `cd backend && python -m unittest test_normalize.EnrollVirtualRouteTest -v`

Expected: FAIL -- `404 NOT FOUND` (route doesn't exist and file doesn't exist).

- [ ] **Step 3: Create the HTML page.**

Create `backend/static/enroll-virtual.html` with the following content (near-mirror of `enroll.html` with the labels, endpoint, and JSON field swapped):

```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Bulk Enroll -- Virtual Barcodes</title>
<style>
  body { font-family: system-ui, sans-serif; font-size: 20px; max-width: 800px;
         margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }
  h1 { font-size: 32px; }
  form { background: #f8f8f8; padding: 1.5rem; border-radius: 12px;
         border: 2px solid #ddd; }
  label { display: block; margin: 0.8rem 0 0.3rem; font-weight: 600; }
  input { width: 100%; padding: 0.6rem; font-size: 20px;
          border: 2px solid #ccc; border-radius: 6px; font-family: monospace; }
  input:focus { border-color: #2563eb; outline: none; }
  button { margin-top: 1rem; padding: 0.7rem 1.5rem; font-size: 18px;
           background: #2563eb; color: white; border: 0; border-radius: 6px; cursor: pointer; }
  #log { margin-top: 2rem; }
  .entry { padding: 0.5rem 0.8rem; margin: 0.3rem 0; border-radius: 6px;
           font-family: monospace; font-size: 16px; }
  .ok { background: #e8f5e9; color: #1b5e20; }
  .err { background: #ffebee; color: #b71c1c; }
  #auth-badge { padding: 0.3rem 0.6rem; border-radius: 4px; font-size: 14px;
                display: inline-block; margin-bottom: 1rem; }
  .auth-ok { background: #c8e6c9; color: #1b5e20; }
  .auth-no { background: #ffcdd2; color: #b71c1c; }
</style>
</head>
<body>
<h1>Bulk Enroll -- Virtual Barcodes</h1>
<div id="auth-badge" class="auth-no">No admin key in URL. Add <code>?key=YOUR_SYNC_KEY</code>.</div>
<form id="enroll-form">
  <label for="email">Bison email</label>
  <input type="email" id="email" name="email" autofocus autocomplete="off"
         placeholder="student@bison.howard.edu" required>
  <label for="barcode">Virtual card barcode (scan from phone)</label>
  <input type="text" id="barcode" name="barcode" autocomplete="off"
         placeholder="(scan from Bison app)" required>
  <button type="submit">Save and next</button>
</form>
<div id="log"></div>

<script>
(function() {
  const params = new URLSearchParams(location.search);
  const key = params.get('key');
  const badge = document.getElementById('auth-badge');
  if (key) {
    badge.className = 'auth-ok';
    badge.textContent = 'Admin key loaded. Ready to enroll.';
  }

  const form = document.getElementById('enroll-form');
  const emailInput = document.getElementById('email');
  const barcodeInput = document.getElementById('barcode');
  const log = document.getElementById('log');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!key) {
      addEntry('err', 'No admin key -- add ?key=... to URL');
      return;
    }
    const email = emailInput.value.trim().toLowerCase();
    const barcode = barcodeInput.value.trim();
    if (!email || !barcode) return;

    try {
      const r = await fetch('/admin/link-virtual', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Sync-Key': key },
        body: JSON.stringify({ email: email, barcode_id: barcode }),
      });
      const j = await r.json();
      if (!r.ok) {
        addEntry('err', `${email} -- ${j.error || r.status}`);
      } else {
        const d = j.attendance_delta;
        const delta = d.absent_before - d.absent_after;
        addEntry('ok',
          `${email} -- virtual barcode ${barcode} linked. ` +
          (delta > 0 ? `+${delta} scans now counted (absences ${d.absent_before} -> ${d.absent_after})`
                     : `no retroactive change`));
        emailInput.value = '';
        barcodeInput.value = '';
        emailInput.focus();
      }
    } catch (err) {
      addEntry('err', `${email} -- request failed: ${err}`);
    }
  });

  function addEntry(cls, msg) {
    const div = document.createElement('div');
    div.className = 'entry ' + cls;
    const ts = new Date().toLocaleTimeString();
    div.textContent = `[${ts}] ${msg}`;
    log.insertBefore(div, log.firstChild);
    while (log.children.length > 30) log.removeChild(log.lastChild);
  }

  emailInput.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === 'Tab') && emailInput.value.trim()) {
      e.preventDefault();
      barcodeInput.focus();
    }
  });
})();
</script>
</body>
</html>
```

- [ ] **Step 4: Add the route.**

In `backend/app.py`, immediately after the `/enroll` route block ends (after `return app.send_static_file("enroll.html")` at line 776), add:

```python
@app.route("/enroll-virtual")
def enroll_virtual_page():
    # Static file; admin key is read client-side from ?key=... and sent in
    # X-Sync-Key header on the AJAX POSTs, which is what actually enforces
    # auth. Serving the HTML itself is not sensitive.
    return app.send_static_file("enroll-virtual.html")
```

- [ ] **Step 5: Run the route test.**

Run: `cd backend && python -m unittest test_normalize.EnrollVirtualRouteTest -v`

Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add backend/static/enroll-virtual.html backend/app.py backend/test_normalize.py
git commit -m "feat(backend): /enroll-virtual page for bulk virtual-barcode enroll

Second bulk-enrollment link paralleling /enroll. Single email + virtual
barcode field, POSTs to /admin/link-virtual. USB-scanner Enter-to-submit
works the same as the physical page."
```

---

## Task 6: Full test suite, deploy, smoke-test

**Files:** None modified. Verification-only.

- [ ] **Step 1: Run the full backend test suite.**

Run: `cd backend && python -m unittest test_normalize -v`

Expected: PASS on every test class. Count should be approximately 51 existing + 10 new = ~61 total. Note the exact count in the commit/deploy note.

- [ ] **Step 2: Health-check local.**

Start the backend locally to confirm nothing 500s on startup:

```bash
cd backend && SYNC_API_KEY=dev-key python app.py &
sleep 2
curl -s http://localhost:5001/enroll-virtual | head -20
curl -s -X POST http://localhost:5001/admin/link-virtual \
  -H 'Content-Type: application/json' \
  -H 'X-Sync-Key: dev-key' \
  -d '{"email":"x@bison.howard.edu","barcode_id":"123"}'
# Stop the server:
pkill -f 'python app.py'
```

Expected: first curl returns HTML starting with `<!doctype html>` and containing "Virtual Barcodes". Second curl returns `{"error":"email not found"}` with HTTP 404 (the 404 body is expected since the local DB is empty).

- [ ] **Step 3: Push and wait for Render.**

```bash
cd "/Users/karthikbalasubramanian/Library/CloudStorage/GoogleDrive-karthik@balasubramanian.us/My Drive/Sandbox/attendance-checker"
git push origin main
```

Then monitor:

```bash
until curl -s https://attendance-checker-kfba.onrender.com/enroll-virtual | grep -q "Virtual Barcodes"; do
  echo "Waiting for Render deploy..."; sleep 15
done
echo "Deploy live."
```

Expected: the loop exits within 1-3 minutes.

- [ ] **Step 4: Prod smoke-test via curl.**

```bash
curl -s https://attendance-checker-kfba.onrender.com/enroll-virtual | head -30
```

Expected: HTML with `<h1>Bulk Enroll -- Virtual Barcodes</h1>` and the auth badge `<div id="auth-badge" class="auth-no">`.

- [ ] **Step 5: Prod smoke-test via browser.**

Open in an incognito window:

```
https://attendance-checker-kfba.onrender.com/enroll-virtual?key=yMAyLgXljmUcSl_droviK9bQNmKMNRv_f1fFbYOgAw0
```

Verify: green "Admin key loaded. Ready to enroll." badge, email field autofocused, placeholder reads "(scan from Bison app)".

Do NOT submit a real student in this smoke-test. Just verify the UI renders.

- [ ] **Step 6: Update memory.**

Edit `~/.claude/projects/-Users-karthikbalasubramanian-Library-CloudStorage-GoogleDrive-karthik-balasubramanian-us-My-Drive-Sandbox/memory/project_attendance_checker.md` -- add a bullet noting:
- `/enroll-virtual` is the virtual-barcode bulk page (new 2026-04-21)
- `/admin/link-virtual` endpoint has the collision check that `/admin/link-physical` lacks (follow-up noted in spec)

- [ ] **Step 7: Append the session to `org-roam/log.org`.**

Under today's heading `* 2026-04-21 Tue`, add a new `**` entry at the top of that day with: commit SHAs from Tasks 1, 3, 5; test count; the two new URLs. Do NOT edit the earlier "virtual-barcode bulk-enroll spec" entry -- append a new entry above it.

---

## Self-Review Checklist

**Spec coverage:**
- `/admin/link-virtual` endpoint: Tasks 1-4 ✓
- Collision check with 409: Task 3 ✓
- Response shape mirror of `/admin/link-physical`: Task 1 ✓
- `/enroll-virtual` page + route: Task 5 ✓
- Barcode normalization: Task 4 ✓
- Tests covering ~6 scenarios: Tasks 1-5 deliver 10 tests (happy path, missing auth, bad auth, missing email, missing barcode, 404, collision, idempotent, normalization, route) ✓
- No changes to shipped physical flow: confirmed -- only new routes and static file added.

**Placeholder scan:** No TBDs, TODOs, or "implement later" language. Every code step includes complete code.

**Type consistency:** JSON field `barcode_id` used consistently. Endpoint path `/admin/link-virtual` used consistently. Response field `attendance_delta.absent_before/absent_after` matches `/admin/link-physical`.

**Route ordering:** Task 1 adds `/admin/link-virtual` after `/admin/link-physical` (around line 893 in app.py). Task 5 adds `/enroll-virtual` after `/enroll` (around line 777 in app.py). Both placements avoid conflicts with existing static-file serving and sync routes.
