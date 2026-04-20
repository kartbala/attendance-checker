# Physical Barcode Enrollment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gap where students using physical Bison cards are marked absent, by capturing the physical barcode at registration and offering a retroactive self-service claim flow, plus admin tools to unstick affected students immediately.

**Architecture:** Four-phase rollout on the existing Flask + React codebase. Phase 1 ships an admin-only endpoint + debug-page form so Charrikka et al. can be unstuck within the hour. Phase 2 adds a standalone HTML page that Karthik opens in class on a laptop with the classroom USB scanner plugged in, capturing physical barcodes byte-for-byte. Phase 3 adds a self-service claim banner on the attendance view with conservative variant matching. Phase 4 makes physical barcode required-with-skip-reason at registration.

**Tech Stack:** Flask 3.1 + SQLite (backend), React 19 + TypeScript + Vite + Tailwind (frontend), Render.com (backend deploy), GitHub Pages (frontend deploy). Tests use Python `unittest` (not pytest).

**Spec:** `docs/2026-04-20-physical-barcode-enrollment-design.md`

---

## Pre-flight

- [ ] **Step 0: Read the spec.** `docs/2026-04-20-physical-barcode-enrollment-design.md`. It answers the "why" for every decision below.
- [ ] **Step 0.1: Set up local dev.** In one terminal: `cd backend && source venv/bin/activate && SYNC_API_KEY=dev-key DB_PATH=./data/checker.db python app.py` (port 5001). In another: `cd frontend && npm run dev` (port 5173). In a third, for production-DB operations: have `https://attendance-checker-kfba.onrender.com` and the prod `SYNC_API_KEY` (see memory `project_attendance_checker.md`) on hand.
- [ ] **Step 0.2: Verify baseline tests pass.** `cd backend && python -m unittest test_normalize.py -v`. Expected: 9 tests pass. If any fail, stop and investigate — do not proceed.

---

# Phase 1 -- Admin bypass

**Goal of this phase:** Deploy `/admin/link-physical` endpoint and extend `/debug` page so Karthik can link a physical barcode to any registered student. Use it immediately to unstick Charrikka.

## Task 1.1: `POST /admin/link-physical` endpoint

**Files:**
- Modify: `backend/app.py` (add endpoint near `/sync/push`)
- Test: `backend/test_normalize.py` (add new test class `AdminLinkPhysicalTest`)

- [ ] **Step 1: Write the failing test.** Append to `backend/test_normalize.py`:

```python
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
```

- [ ] **Step 2: Run to verify tests fail.**

Run: `cd backend && python -m unittest test_normalize.AdminLinkPhysicalTest -v`
Expected: All 4 tests FAIL with 404 (endpoint doesn't exist) or similar.

- [ ] **Step 3: Implement the endpoint.** In `backend/app.py`, add a helper and a route. Place the helper near `normalize_barcode` and the route directly after `/sync/pull`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass.**

Run: `cd backend && python -m unittest test_normalize.AdminLinkPhysicalTest -v`
Expected: 4 tests pass. If `test_idempotent` or `test_links_physical_barcode_and_returns_delta` fails with wrong delta, print `body` and investigate — likely the `_compute_attendance_delta` helper has a bug.

- [ ] **Step 5: Run full test suite to verify no regressions.**

Run: `cd backend && python -m unittest test_normalize -v`
Expected: all tests pass (13 total now).

- [ ] **Step 6: Commit.**

```bash
git add backend/app.py backend/test_normalize.py
git commit -m "feat(backend): admin endpoint to link physical barcode to student

POST /admin/link-physical is key-authed (X-Sync-Key), takes email +
physical_barcode_id, updates all student rows with that email, returns
attendance delta so callers can show impact."
```

## Task 1.2: Extend `/debug` page with link-physical form

**Files:**
- Modify: `backend/app.py:351-468` (the `debug_view` function)

- [ ] **Step 1: Add an inline HTML form at the top of the debug view,** just after the student-name `<h1>` and before the per-course loop. The form POSTs to `/admin/link-physical` via fetch and reloads on success.

In `backend/app.py` find the line:

```python
        f"<p class='meta'>{email} &middot; HUID: <code>{students[0]['huid'] or '(none)'}</code> &middot; "
```

Immediately before that line, inside the `out.append(...)` / `out = [...]` sequence, insert:

```python
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
```

Note: `{email!r}` injects the email as a quoted Python repr, which is safe for this context because `email` has already passed through `.strip().lower()` and the page only loads for a known-enrolled email.

- [ ] **Step 2: Smoke-test locally.**

Run backend: `cd backend && SYNC_API_KEY=dev-key DB_PATH=./data/checker.db python app.py`
Seed: `python scripts/sync_to_render.py pull --url http://localhost:5001 --key dev-key` (or use any known-good local DB).

In browser, open `http://localhost:5001/debug?email=<some-registered-email>`. Verify the yellow form appears above the course tables. Paste a test barcode, paste `dev-key`, click Link. Expect a success banner and page reload.

- [ ] **Step 3: Commit.**

```bash
git add backend/app.py
git commit -m "feat(backend): link-physical form on /debug page

Inline form at top of the per-email debug page POSTs to
/admin/link-physical. Admin pastes barcode + sync key, page reloads
showing updated attendance counts."
```

## Task 1.3: CLI script for bulk linking

**Files:**
- Create: `backend/scripts/link_physical_barcode.py`

- [ ] **Step 1: Create the script directory if needed.**

```bash
mkdir -p backend/scripts
```

- [ ] **Step 2: Write the script.** Create `backend/scripts/link_physical_barcode.py`:

```python
#!/usr/bin/env python3
"""Link a physical Bison card barcode to a student on the live attendance
checker. Wraps POST /admin/link-physical.

Usage (single):
    python link_physical_barcode.py \\
        --url https://attendance-checker-kfba.onrender.com \\
        --key $SYNC_API_KEY \\
        --email charrikka@bison.howard.edu \\
        --physical-barcode 9988776655

Usage (bulk, CSV with columns 'email,physical_barcode'):
    python link_physical_barcode.py \\
        --url ... --key ... --csv ./links.csv
"""

import argparse
import csv
import json
import sys
import urllib.request


def link_one(url, key, email, barcode):
    payload = json.dumps({
        "email": email.strip().lower(),
        "physical_barcode_id": barcode.strip(),
    }).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/admin/link-physical",
        data=payload,
        headers={"Content-Type": "application/json", "X-Sync-Key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
            d = body.get("attendance_delta", {})
            print(f"OK  {email}  barcode={barcode}  "
                  f"absent: {d.get('absent_before', '?')} -> {d.get('absent_after', '?')}")
            return True
    except urllib.error.HTTPError as e:
        msg = e.read().decode() if hasattr(e, "read") else str(e)
        print(f"ERR {email}  barcode={barcode}  HTTP {e.code}: {msg}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"ERR {email}  barcode={barcode}  {e}", file=sys.stderr)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="Backend base URL")
    ap.add_argument("--key", required=True, help="SYNC_API_KEY value")
    ap.add_argument("--email")
    ap.add_argument("--physical-barcode", dest="barcode")
    ap.add_argument("--csv", help="Path to CSV with columns email,physical_barcode")
    args = ap.parse_args()

    if args.csv:
        with open(args.csv) as f:
            reader = csv.DictReader(f)
            ok = sum(1 for row in reader if link_one(
                args.url, args.key, row["email"], row["physical_barcode"]
            ))
            print(f"\nDone. {ok} linked.")
        return
    if not args.email or not args.barcode:
        ap.error("--email and --physical-barcode required (or pass --csv)")
    link_one(args.url, args.key, args.email, args.barcode)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Make executable.** `chmod +x backend/scripts/link_physical_barcode.py`

- [ ] **Step 4: Smoke-test against local.**

```bash
python backend/scripts/link_physical_barcode.py \
  --url http://localhost:5001 \
  --key dev-key \
  --email <some-registered-test-email> \
  --physical-barcode 9999999
```

Expected: `OK <email>  barcode=9999999  absent: N -> N` (delta may be zero for a random barcode — that's fine; it tests the API wire).

- [ ] **Step 5: Commit.**

```bash
git add backend/scripts/link_physical_barcode.py
git commit -m "feat(scripts): CLI wrapper for /admin/link-physical

Supports single (--email + --physical-barcode) and bulk (--csv) modes.
Uses stdlib urllib, no deps needed."
```

## Task 1.4: Deploy Phase 1 and unstick Charrikka

- [ ] **Step 1: Push to trigger Render deploy.**

```bash
git push origin main
```

Render auto-deploys the backend. Watch `https://dashboard.render.com` or poll health:

```bash
until curl -s https://attendance-checker-kfba.onrender.com/health | grep -q 'ok'; do sleep 5; done
echo "backend is up"
```

- [ ] **Step 2: Open Charrikka's debug page.** In browser: `https://attendance-checker-kfba.onrender.com/debug?email=charrikka.gordon@bison.howard.edu` (or whatever her exact Bison email is — verify against POM roster if unsure).

Eyeball the per-course attendance table. Look at the absent dates. Cross-reference by opening a second tab with the same URL but for other registered students who are present those days — then compare scan_timestamps. Look for a barcode value that appears in `/attendance` table but isn't associated with any registered student (an orphan). You can do this SQL-side locally via `backend/data/checker.db` after a `sync_to_render.py pull`, or via a one-off direct query through the Render shell.

Concretely: in the Render dashboard, open the attendance-checker service, click "Shell", run:

```bash
cd /opt/render/project/src/backend && python -c "
import sqlite3
db = sqlite3.connect('/var/data/checker.db')
db.row_factory = sqlite3.Row
course = 'INFO-335-04'
known = {row['student_id'] for row in db.execute(
    'SELECT DISTINCT barcode_id AS student_id FROM student WHERE course_code = ? '
    'UNION SELECT physical_barcode_id FROM student WHERE course_code = ?',
    (course, course)) if row['student_id']}
orphans = db.execute(
    'SELECT student_id, COUNT(*) n, MIN(scan_date) first_seen, MAX(scan_date) last_seen '
    'FROM attendance WHERE course_code = ? GROUP BY student_id ORDER BY n DESC',
    (course,)).fetchall()
for o in orphans:
    if o['student_id'] not in known:
        print(f\"  {o['student_id']}  n={o['n']}  {o['first_seen']} -> {o['last_seen']}\")
"
```

This lists every unclaimed barcode in POM with scan count and date range. The one with ~13 scans spanning the semester is very likely Charrikka's physical card.

- [ ] **Step 3: Link via `/debug` form.** Back in the browser at her debug page, paste the candidate orphan barcode into the Link form, paste the production `SYNC_API_KEY`, click Link. Watch the delta; her 16 absences should drop to 3 (or whatever her true count is).

- [ ] **Step 4: Draft reply to Charrikka.** Use the draft-edit-send workflow (`/tmp/claude-draft-<TS>.txt`). Tell her the tracker now reflects her physical-card scans, give her the new numbers, and apologize for the confusion. Do **not** send until Karthik approves.

- [ ] **Step 5: Scan the rest of POM and QBA for other students with many orphan-matched dates.** Same query as Step 2, for each course. For any student with ≥5 "absences" in the tracker where a plausible orphan exists in the section, link them proactively before they have to email. Bulk-use the CLI: build a CSV, run `link_physical_barcode.py --csv`.

- [ ] **Step 6: Log the session.** Append to `org-roam/log.org` with names and counts unstuck.

---

# Phase 2 -- In-class bulk re-enrollment

**Goal of this phase:** Serve a static HTML page at `/enroll?key=...` that Karthik opens on a laptop in class with the classroom USB scanner. Students type email, scan card, done.

## Task 2.1: Create `backend/static/enroll.html`

**Files:**
- Create: `backend/static/enroll.html`
- Modify: `backend/app.py` (configure static folder if needed)

- [ ] **Step 1: Check Flask static-folder config.** In `backend/app.py`, the current `app = Flask(__name__)` uses the default static folder `./static`. Create the folder:

```bash
mkdir -p backend/static
```

- [ ] **Step 2: Write the page.** Create `backend/static/enroll.html`:

```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Bulk Enroll -- Physical Barcodes</title>
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
<h1>Bulk Enroll -- Physical Barcodes</h1>
<div id="auth-badge" class="auth-no">No admin key in URL. Add <code>?key=YOUR_SYNC_KEY</code>.</div>
<form id="enroll-form">
  <label for="email">Bison email</label>
  <input type="email" id="email" name="email" autofocus autocomplete="off"
         placeholder="student@bison.howard.edu" required>
  <label for="barcode">Physical card barcode (scan now)</label>
  <input type="text" id="barcode" name="barcode" autocomplete="off"
         placeholder="(scan)" required>
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

  // USB scanners typically end input with \r (Enter), which submits the form.
  // That's the desired flow once both fields are filled.

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
      const r = await fetch('/admin/link-physical', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Sync-Key': key },
        body: JSON.stringify({ email: email, physical_barcode_id: barcode }),
      });
      const j = await r.json();
      if (!r.ok) {
        addEntry('err', `${email} -- ${j.error || r.status}`);
      } else {
        const d = j.attendance_delta;
        const delta = d.absent_before - d.absent_after;
        addEntry('ok',
          `${email} -- barcode ${barcode} linked. ` +
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
    // Keep only the last 30
    while (log.children.length > 30) log.removeChild(log.lastChild);
  }

  // After email is typed, Tab/Enter moves focus to barcode.
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

- [ ] **Step 3: Commit.**

```bash
git add backend/static/enroll.html
git commit -m "feat(backend): in-class bulk enroll static page

Standalone HTML page for Karthik to open on a laptop with the classroom
USB scanner. Email + barcode fields, auto-advance, POSTs to
/admin/link-physical, rolling log of last 30 enrollments."
```

## Task 2.2: Serve `/enroll` route

**Files:**
- Modify: `backend/app.py` (add route)

- [ ] **Step 1: Add the route.** In `backend/app.py`, after the `/debug` route, add:

```python
@app.route("/enroll")
def enroll_page():
    # Static file; admin key is read client-side from ?key=... and sent in
    # X-Sync-Key header on the AJAX POSTs, which is what actually enforces
    # auth. Serving the HTML itself is not sensitive.
    return app.send_static_file("enroll.html")
```

- [ ] **Step 2: Smoke-test locally.**

Start backend with `SYNC_API_KEY=dev-key`. Open `http://localhost:5001/enroll?key=dev-key` in a browser. Verify: green "Admin key loaded" badge, email field autofocused, tab from email to barcode works.

Simulate a scanner by typing into the email, tabbing, typing a barcode, hitting Enter. Verify: log entry appears, form resets, focus returns to email.

- [ ] **Step 3: Commit.**

```bash
git add backend/app.py
git commit -m "feat(backend): /enroll route serves bulk-enroll page"
```

## Task 2.3: Run in-class bulk session

**Not a code task -- a live operation.**

- [ ] **Step 1: Deploy.** `git push origin main`. Wait for Render health check.

- [ ] **Step 2: Bring a laptop to POM.** Plug in the classroom USB scanner. Open `https://attendance-checker-kfba.onrender.com/enroll?key=<PROD_KEY>` **in a private/incognito window** so the key doesn't get auto-saved by the browser.

- [ ] **Step 3: Announce.** First 2 min of class: "Everyone who has a physical Bison card they've used to scan in, come line up. Type your Bison email, scan your card, done."

- [ ] **Step 4: Process the line.** Watch the log for red entries (usually typos in email). Fix and redo on the spot.

- [ ] **Step 5: Close the browser window after class.** Clear URL history to wipe the admin key from the session. (Opening in private/incognito as in Step 2 makes this automatic.)

- [ ] **Step 6: Repeat for QBA.**

- [ ] **Step 7: Log it.** Append to `org-roam/log.org` with enrollment counts (e.g., "POM: 32 physical cards captured, QBA: 28").

---

# Phase 3 -- Self-service claim flow

**Goal of this phase:** Any already-registered student can open the attendance view, see a banner if they have absences and their section has orphan scans, scan their physical card, and see past scans retroactively count.

## Task 3.1: `claim_log` table and migration

**Files:**
- Modify: `backend/app.py` (add to SCHEMA, add init migration)
- Test: `backend/test_normalize.py`

- [ ] **Step 1: Write the failing test.** Add to `backend/test_normalize.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails.**

Run: `cd backend && python -m unittest test_normalize.ClaimLogSchemaTest -v`
Expected: FAIL -- `PRAGMA table_info` returns empty, so asserts fail.

- [ ] **Step 3: Add the table to SCHEMA.** In `backend/app.py`, modify the `SCHEMA` multi-line string (around line 49) to add, before the closing `"""`:

```sql
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
```

- [ ] **Step 4: Run tests.**

Run: `cd backend && python -m unittest test_normalize.ClaimLogSchemaTest -v`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add backend/app.py backend/test_normalize.py
git commit -m "feat(backend): claim_log table for diagnostics

Captures every claim attempt (email, submitted barcode, variants tried,
matched variant, delta) so we can spot camera-vs-scanner decode drift
after rollout."
```

## Task 3.2: `normalize_barcode_variants` function

**Files:**
- Modify: `backend/app.py` (add below `normalize_barcode`)
- Test: `backend/test_normalize.py`

- [ ] **Step 1: Write the failing test.** Add to `test_normalize.py`:

```python
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
```

- [ ] **Step 2: Run to verify fails.**

Run: `cd backend && python -m unittest test_normalize.NormalizeVariantsTest -v`
Expected: FAIL -- `normalize_barcode_variants` doesn't exist.

- [ ] **Step 3: Implement.** In `backend/app.py`, directly after `normalize_barcode`:

```python
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
```

- [ ] **Step 4: Run tests.**

Run: `cd backend && python -m unittest test_normalize.NormalizeVariantsTest -v`
Expected: 7 tests pass.

- [ ] **Step 5: Commit.**

```bash
git add backend/app.py backend/test_normalize.py
git commit -m "feat(backend): normalize_barcode_variants for claim matching

Generates a small set of plausible barcode forms (canonical, minus last
digit, minus first digit) to tolerate camera-vs-scanner decode drift.
Only used at claim time against a bounded orphan-scan set."
```

## Task 3.3: `POST /claim-physical-barcode` endpoint

**Files:**
- Modify: `backend/app.py`
- Test: `backend/test_normalize.py`

- [ ] **Step 1: Write the failing tests.** Add to `test_normalize.py`:

```python
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
        r = self.client.post("/claim-physical-barcode", json={
            "email": "charrikka@bison.howard.edu",
            "physical_barcode_id": "0000000000",
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
```

- [ ] **Step 2: Run to verify fails.**

Run: `cd backend && python -m unittest test_normalize.ClaimPhysicalBarcodeTest -v`
Expected: all 5 fail (404 on endpoint).

- [ ] **Step 3: Implement.** First add `import json` alongside the other top-level imports in `backend/app.py` (it's not currently imported). Then, after `admin_link_physical`, add:

```python
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

    variants = normalize_barcode_variants(submitted)
    matched = None
    for variant in variants:
        hit = db.execute(
            f"SELECT 1 FROM attendance WHERE student_id = ? "
            f"AND course_code IN ({','.join('?' * len(courses))}) LIMIT 1",
            [variant] + courses,
        ).fetchone()
        if hit:
            matched = variant
            break

    canonical = normalize_barcode(submitted)
    to_save = matched or canonical or submitted

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
        "attendance_delta": {
            "absent_before": absent_before, "absent_after": absent_after,
        },
    })
```

- [ ] **Step 4: Run tests.**

Run: `cd backend && python -m unittest test_normalize.ClaimPhysicalBarcodeTest -v`
Expected: 5 pass.

- [ ] **Step 5: Run full suite.**

Run: `cd backend && python -m unittest test_normalize -v`
Expected: all pass.

- [ ] **Step 6: Commit.**

```bash
git add backend/app.py backend/test_normalize.py
git commit -m "feat(backend): self-service /claim-physical-barcode endpoint

Un-authed (email + physical scan is proof). Tries normalized variants
against orphan scans in the student's courses; saves matched form if
found, canonical form otherwise. Returns attendance delta. Every
attempt logged to claim_log for diagnostics."
```

## Task 3.4: `/attendance` response fields

**Files:**
- Modify: `backend/app.py` (inside `attendance()` route)
- Test: `backend/test_normalize.py`

- [ ] **Step 1: Write the failing test.** Add to `test_normalize.py`:

```python
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
```

- [ ] **Step 2: Run to verify fails.**

Run: `cd backend && python -m unittest test_normalize.AttendanceResponseFieldsTest -v`
Expected: 3 fail (`KeyError` or `assertIn`).

- [ ] **Step 3: Modify `/attendance` route.** In `backend/app.py`, find the final `return jsonify({...})` block of the `attendance()` function (around line 336). Before it, compute the two new fields, then add them to the response:

```python
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
```

Then add both fields to the `return jsonify({...})` dict.

- [ ] **Step 4: Run tests.**

Run: `cd backend && python -m unittest test_normalize.AttendanceResponseFieldsTest -v`
Expected: 3 pass.

- [ ] **Step 5: Run full suite.**

Run: `cd backend && python -m unittest test_normalize -v`
Expected: all pass.

- [ ] **Step 6: Commit.**

```bash
git add backend/app.py backend/test_normalize.py
git commit -m "feat(backend): /attendance returns section_orphan_count + has_physical_barcode

Frontend uses these two fields to gate the physical-card claim banner."
```

## Task 3.5: `GET /debug/claims` admin page

**Files:**
- Modify: `backend/app.py`

- [ ] **Step 1: Add the route.** In `backend/app.py`, near the existing `/debug` route:

```python
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
```

- [ ] **Step 2: Smoke-test locally.** Start the backend, POST a few claim attempts via curl, then open `http://localhost:5001/debug/claims?key=dev-key`. Verify the table renders.

- [ ] **Step 3: Commit.**

```bash
git add backend/app.py
git commit -m "feat(backend): /debug/claims admin page

Key-gated HTML table of last 50 claim attempts for diagnostics."
```

## Task 3.6: Extract `BarcodeScanner` component

**Files:**
- Create: `frontend/src/components/BarcodeScanner.tsx`
- Modify: `frontend/src/components/RegisterForm.tsx` (use shared component)

- [ ] **Step 1: Create the shared component.** Factor out the camera + USB-scanner logic from `RegisterForm.tsx:46-96`. Create `frontend/src/components/BarcodeScanner.tsx`:

```tsx
import { useState, useEffect, useRef, useCallback } from 'react';
import { Html5Qrcode, Html5QrcodeSupportedFormats } from 'html5-qrcode';
import { useUsbScanner } from '../hooks/useUsbScanner';

interface BarcodeScannerProps {
  onScan: (barcode: string) => void;
  scannerId?: string;
}

type ScanMode = 'usb' | 'camera';

export function BarcodeScanner({ onScan, scannerId = 'barcode-reader' }: BarcodeScannerProps) {
  const [scanMode, setScanMode] = useState<ScanMode>('camera');
  const [isCameraActive, setIsCameraActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scannerRef = useRef<Html5Qrcode | null>(null);

  const handleScan = useCallback((barcode: string) => {
    onScan(barcode.trim());
    setIsCameraActive(false);
    if (navigator.vibrate) navigator.vibrate(200);
  }, [onScan]);

  useUsbScanner({
    onScan: handleScan,
    enabled: scanMode === 'usb' && !isCameraActive,
    minLength: 3,
    maxDelay: 50,
  });

  useEffect(() => {
    if (!isCameraActive || scanMode !== 'camera') return;
    let mounted = true;
    const start = async () => {
      await new Promise(r => setTimeout(r, 100));
      if (!scannerRef.current) {
        scannerRef.current = new Html5Qrcode(scannerId, {
          verbose: false,
          formatsToSupport: [
            Html5QrcodeSupportedFormats.EAN_13,
            Html5QrcodeSupportedFormats.EAN_8,
            Html5QrcodeSupportedFormats.CODE_128,
            Html5QrcodeSupportedFormats.CODE_39,
            Html5QrcodeSupportedFormats.UPC_A,
            Html5QrcodeSupportedFormats.UPC_E,
          ],
        });
      }
      if (scannerRef.current.isScanning) return;
      try {
        await scannerRef.current.start(
          { facingMode: "environment" },
          { fps: 10, qrbox: { width: 250, height: 150 }, aspectRatio: 1.5 },
          (decodedText) => { if (mounted) handleScan(decodedText); },
          () => {},
        );
      } catch (err) {
        if (mounted) {
          setError(err instanceof Error ? err.message : "Camera failed to start");
          setIsCameraActive(false);
        }
      }
    };
    start();
    return () => {
      mounted = false;
      if (scannerRef.current?.isScanning) scannerRef.current.stop().catch(() => {});
    };
  }, [isCameraActive, scanMode, handleScan, scannerId]);

  return (
    <div className="space-y-3">
      <div className="flex bg-gray-100 rounded-xl p-1">
        <button
          type="button"
          onClick={() => { setIsCameraActive(false); setScanMode('camera'); }}
          className={`flex-1 py-2 px-4 rounded-lg text-sm font-medium transition-all ${
            scanMode === 'camera' ? 'bg-blue-600 text-white shadow-sm' : 'text-gray-600'
          }`}
        >
          Camera
        </button>
        <button
          type="button"
          onClick={() => { setIsCameraActive(false); setScanMode('usb'); }}
          className={`flex-1 py-2 px-4 rounded-lg text-sm font-medium transition-all ${
            scanMode === 'usb' ? 'bg-blue-600 text-white shadow-sm' : 'text-gray-600'
          }`}
        >
          USB Scanner
        </button>
      </div>
      {error && (
        <div className="bg-red-50 border-2 border-red-300 text-red-800 px-4 py-3 rounded-xl text-sm">
          {error}
        </div>
      )}
      {scanMode === 'camera' && (
        <>
          <div
            id={scannerId}
            className={`w-full bg-black rounded-xl overflow-hidden transition-all ${isCameraActive ? 'h-48' : 'h-0'}`}
          />
          {!isCameraActive ? (
            <button type="button" onClick={() => setIsCameraActive(true)}
              className="w-full py-3 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-xl transition-all">
              Start Camera
            </button>
          ) : (
            <button type="button" onClick={() => setIsCameraActive(false)}
              className="w-full py-3 bg-red-500 hover:bg-red-600 text-white font-semibold rounded-xl transition-all">
              Stop Camera
            </button>
          )}
        </>
      )}
      {scanMode === 'usb' && (
        <div className="text-center py-6 bg-gray-50 rounded-xl border-2 border-dashed border-gray-300">
          <p className="text-lg text-gray-600">Point your USB scanner at the barcode</p>
          <p className="text-sm text-gray-400 mt-1">Listening for scanner input...</p>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Refactor `RegisterForm.tsx`** to use `BarcodeScanner`. Remove the inline scanner JSX and hooks (the camera useEffect at lines 47-96 and the scanner mode toggles in the virtual-barcode block at lines 194-249). Replace with two `<BarcodeScanner>` instances (one with `scannerId="barcode-reader-virtual"` and `onScan={setBarcodeId}`, the other with `scannerId="barcode-reader-physical"` and `onScan={setPhysicalBarcodeId}`). Remove the `scanTarget` state since each scanner now has its own `onScan` callback. Keep the "already-scanned" display blocks and the "Rescan" buttons.

- [ ] **Step 3: Verify the frontend still compiles and the register flow works.**

Run: `cd frontend && npm run dev`
Open `http://localhost:5173/attendance-checker/`. Walk through a registration. Verify both virtual and physical scan paths still work.

- [ ] **Step 4: Commit.**

```bash
git add frontend/src/components/BarcodeScanner.tsx frontend/src/components/RegisterForm.tsx
git commit -m "refactor(frontend): extract BarcodeScanner component

Shared by RegisterForm and (next commit) AttendanceView's claim banner.
Each instance needs a unique scannerId to avoid html5-qrcode
DOM-element collision."
```

## Task 3.7: Claim banner + modal in `AttendanceView`

**Files:**
- Modify: `frontend/src/components/AttendanceView.tsx`

- [ ] **Step 1: Extend `AttendanceData` interface** at the top of `AttendanceView.tsx`:

```tsx
interface AttendanceData {
  student_name: string;
  course_code: string;
  course_name: string;
  enrolled: number;
  barcodes_registered: string[];
  total_sessions: number;
  sessions_attended: number;
  excused_count: number;
  unexcused_count: number;
  effective_rate: number;
  dates: DateEntry[];
  section_orphan_count: number;
  has_physical_barcode: boolean;
}
```

- [ ] **Step 2: Add banner + modal state + handlers** at the top of the `AttendanceView` function, just after the existing `useState` hooks:

```tsx
  const [showClaimModal, setShowClaimModal] = useState(false);
  const [claimResult, setClaimResult] = useState<
    { kind: 'success'; delta: number } | { kind: 'no-match'; barcode: string } | null
  >(null);

  const shouldShowBanner =
    data && !data.has_physical_barcode &&
    data.unexcused_count >= 2 &&
    data.section_orphan_count >= 1;

  const handleClaimScan = async (scannedBarcode: string) => {
    try {
      const resp = await fetch(`${apiUrl}/claim-physical-barcode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, physical_barcode_id: scannedBarcode }),
      });
      const j = await resp.json();
      if (!resp.ok) {
        setClaimResult({ kind: 'no-match', barcode: scannedBarcode });
        return;
      }
      const delta = j.attendance_delta.absent_before - j.attendance_delta.absent_after;
      if (delta > 0) {
        setClaimResult({ kind: 'success', delta });
        // Refetch attendance to update the page
        setTimeout(() => { setShowClaimModal(false); setClaimResult(null); window.location.reload(); }, 2500);
      } else {
        setClaimResult({ kind: 'no-match', barcode: scannedBarcode });
      }
    } catch {
      setClaimResult({ kind: 'no-match', barcode: scannedBarcode });
    }
  };
```

- [ ] **Step 3: Render the banner and modal.** In the main return block of `AttendanceView`, directly after the `<button onClick={onBack}>&larr; Back</button>` line (around line 275-277), insert:

```tsx
      {shouldShowBanner && (
        <div className="bg-amber-50 border-2 border-amber-400 rounded-2xl p-5 flex items-start gap-4">
          <div className="text-3xl">🎫</div>
          <div className="flex-1">
            <h3 className="text-lg font-semibold text-amber-900">
              Did you scan your physical Bison card in class?
            </h3>
            <p className="text-base text-amber-800 mt-1">
              There {data.section_orphan_count === 1 ? 'is' : 'are'}{' '}
              <b>{data.section_orphan_count}</b> unclaimed scan
              {data.section_orphan_count === 1 ? '' : 's'} in your section.
              Scan your physical card to count yours.
            </p>
            <button
              onClick={() => { setShowClaimModal(true); setClaimResult(null); }}
              className="mt-3 px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white font-semibold rounded-lg"
            >
              Scan physical card
            </button>
          </div>
        </div>
      )}
```

Then at the very end of the main return block, just before the outer closing `</div>`, insert the modal:

```tsx
      {showClaimModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
             onClick={() => { if (!claimResult || claimResult.kind === 'no-match') { setShowClaimModal(false); setClaimResult(null); } }}>
          <div className="bg-white rounded-2xl shadow-xl p-6 max-w-md w-full" onClick={e => e.stopPropagation()}>
            {!claimResult && (
              <>
                <h3 className="text-xl font-semibold text-gray-900 mb-3">Scan your physical card</h3>
                <BarcodeScanner onScan={handleClaimScan} scannerId="barcode-reader-claim" />
                <button onClick={() => setShowClaimModal(false)}
                        className="w-full mt-3 py-2 text-gray-600 hover:text-gray-800">Cancel</button>
              </>
            )}
            {claimResult?.kind === 'success' && (
              <div className="text-center">
                <div className="text-5xl mb-3">✅</div>
                <h3 className="text-xl font-semibold text-green-800 mb-2">Found {claimResult.delta} scan{claimResult.delta === 1 ? '' : 's'}!</h3>
                <p className="text-gray-600">Reloading your attendance...</p>
              </div>
            )}
            {claimResult?.kind === 'no-match' && (
              <div>
                <div className="text-5xl mb-3 text-center">⚠️</div>
                <h3 className="text-xl font-semibold text-gray-900 mb-2">No matching scans found</h3>
                <p className="text-gray-700 mb-3">
                  We couldn't find class scans matching that barcode. Email Dr. B at{' '}
                  <a href="mailto:karthik.b@Howard.edu" className="text-blue-600 underline">karthik.b@Howard.edu</a>{' '}
                  and include this barcode:
                </p>
                <code className="block bg-gray-100 px-3 py-2 rounded font-mono text-sm break-all">{claimResult.barcode}</code>
                <button onClick={() => { setShowClaimModal(false); setClaimResult(null); }}
                        className="w-full mt-4 py-2 bg-gray-200 hover:bg-gray-300 rounded-lg">Close</button>
              </div>
            )}
          </div>
        </div>
      )}
```

Also add the import at the top of the file: `import { BarcodeScanner } from './BarcodeScanner';`.

- [ ] **Step 4: Smoke-test locally.**
  1. Start backend with a DB that has an orphan match for a test email.
  2. Start frontend, visit `http://localhost:5173/attendance-checker/`.
  3. Enter the test email in the "Already Registered?" lookup.
  4. Verify the amber banner appears.
  5. Click "Scan physical card", use USB or camera to scan the orphan barcode.
  6. Verify the green success modal shows the delta, then page reloads and absences have dropped.
  7. Repeat with a barcode that doesn't match. Verify the warning modal shows with the copy-paste-friendly barcode.

- [ ] **Step 5: Commit.**

```bash
git add frontend/src/components/AttendanceView.tsx
git commit -m "feat(frontend): self-service claim banner on attendance view

Shown when student has no physical barcode registered, >=2 absences,
and their section has >=1 orphan scan. Modal opens BarcodeScanner;
POSTs to /claim-physical-barcode; shows success delta or escalation
instructions with copy-paste barcode."
```

## Task 3.8: Deploy Phase 3

- [ ] **Step 1: Push.**

```bash
git push origin main
```

Render auto-deploys backend; GitHub Pages auto-builds frontend.

- [ ] **Step 2: Smoke-test prod.**
  - Visit `https://kartbala.github.io/attendance-checker/`.
  - Log in as a student known to have orphan scans (e.g., a student you didn't unstick in Phase 1 — use `/debug/claims?key=...` and the orphan query from Phase 1 Step 2 to find candidates).
  - Verify banner shows.
  - Do the scan, verify the delta.

- [ ] **Step 3: Announce to classes.** Quick email to POM and QBA: "If you've been marked absent more than twice, open the attendance checker and look for the yellow banner — scan your physical Bison card to fix it."

- [ ] **Step 4: Log it.**

---

# Phase 4 -- Required-with-skip registration

**Goal of this phase:** New registrations must include either a physical barcode or a logged skip reason.

## Task 4.1: Add `physical_barcode_skip_reason` column

**Files:**
- Modify: `backend/app.py`
- Test: `backend/test_normalize.py`

- [ ] **Step 1: Write the failing test.** Add:

```python
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
```

- [ ] **Step 2: Run to verify fails.**

Run: `cd backend && python -m unittest test_normalize.SkipReasonColumnTest -v`
Expected: FAIL.

- [ ] **Step 3: Add column to SCHEMA and the migration.** In `backend/app.py`, modify the `student` table definition in SCHEMA to add the column, and add an `ALTER TABLE` migration in `init_db()` next to the existing `physical_barcode_id` ALTER:

Modify SCHEMA:
```sql
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
```

In `init_db()`, add an ALTER TABLE:
```python
    try:
        conn.execute("ALTER TABLE student ADD COLUMN physical_barcode_skip_reason TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
```

- [ ] **Step 4: Run tests.**

Run: `cd backend && python -m unittest test_normalize -v`
Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add backend/app.py backend/test_normalize.py
git commit -m "feat(backend): physical_barcode_skip_reason column on student

Nullable. Will be populated by /register when a student explicitly opts
out of registering a physical card."
```

## Task 4.2: Update `POST /register` contract

**Files:**
- Modify: `backend/app.py`
- Test: `backend/test_normalize.py`

- [ ] **Step 1: Write the failing tests.**

```python
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
        self.assertEqual(r.status_code, 400)

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
                "SELECT physical_barcode_skip_reason FROM student "
                "WHERE email = ?", ("s1@bison.howard.edu",)
            ).fetchone()
        self.assertEqual(row[0], "privacy-screen")
```

- [ ] **Step 2: Run to verify fails.** `cd backend && python -m unittest test_normalize.RegisterWithSkipReasonTest -v`. Expect 3 fails.

- [ ] **Step 3: Modify `register` route.** In `backend/app.py`, update the `register()` function around line 182. Add parse + validation for `physical_barcode_skip_reason` and enforce "either/or":

```python
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
```

- [ ] **Step 4: Run tests.** `cd backend && python -m unittest test_normalize -v`. Expect all pass.

- [ ] **Step 5: Commit.**

```bash
git add backend/app.py backend/test_normalize.py
git commit -m "feat(backend): /register accepts physical barcode OR skip reason

Either physical_barcode_id or physical_barcode_skip_reason must be set.
Skip reason persists to student.physical_barcode_skip_reason so we can
audit how often students opt out and why."
```

## Task 4.3: Reorganize `RegisterForm.tsx`

**Files:**
- Modify: `frontend/src/components/RegisterForm.tsx`

- [ ] **Step 1: Change the form so physical scan is always visible after virtual.** Remove the "+ Add physical Bison card (optional)" toggle (`showPhysicalScan` state) so the physical section always renders once `barcodeId` is set.

- [ ] **Step 2: Add skip-reason UI.** Below the physical scan area, add:

```tsx
        {barcodeId && !physicalBarcodeId && (
          <div className="border-t pt-3 mt-2">
            <details>
              <summary className="cursor-pointer text-sm text-gray-600 hover:text-gray-800">
                I can't scan my physical card
              </summary>
              <div className="mt-3 space-y-2 pl-2">
                {[
                  { v: 'no-physical-card', label: "I don't have a physical card" },
                  { v: 'privacy-screen', label: 'Privacy screen blocks scanning' },
                  { v: 'forgot-today', label: 'Forgot card today' },
                  { v: 'other', label: 'Other' },
                ].map(opt => (
                  <label key={opt.v} className="flex items-center gap-2 text-base">
                    <input
                      type="radio"
                      name="skip-reason"
                      value={opt.v}
                      checked={skipReason === opt.v}
                      onChange={e => setSkipReason(e.target.value)}
                    />
                    {opt.label}
                  </label>
                ))}
                {skipReason === 'other' && (
                  <input
                    type="text"
                    value={skipReasonOther}
                    onChange={e => setSkipReasonOther(e.target.value)}
                    placeholder="Tell us why"
                    className="w-full px-3 py-2 text-base border-2 border-gray-300 rounded-xl"
                  />
                )}
              </div>
            </details>
          </div>
        )}
```

- [ ] **Step 3: Add state.** At the top of the `RegisterForm` function body, add:

```tsx
  const [skipReason, setSkipReason] = useState('');
  const [skipReasonOther, setSkipReasonOther] = useState('');

  const effectiveSkipReason = skipReason === 'other' ? skipReasonOther.trim() : skipReason;
  const physicalProvided = !!physicalBarcodeId;
  const skipProvided = !!effectiveSkipReason;
```

- [ ] **Step 4: Update submit payload and disabled condition.**

Replace the fetch body:
```tsx
        body: JSON.stringify({
          email: email.trim().toLowerCase(),
          huid: huid.trim(),
          barcode_id: barcodeId.trim(),
          physical_barcode_id: physicalBarcodeId.trim() || undefined,
          physical_barcode_skip_reason: effectiveSkipReason || undefined,
        }),
```

Replace the submit-button `disabled` logic:
```tsx
disabled={submitting || !email || !huid || !barcodeId || (!physicalProvided && !skipProvided)}
```

- [ ] **Step 5: Smoke-test.** Start backend + frontend. Try registering without physical or skip → submit disabled. Add a skip reason → enabled, submits, DB row has reason. Add a physical scan → enabled, submits, DB row has barcode.

- [ ] **Step 6: Commit.**

```bash
git add frontend/src/components/RegisterForm.tsx
git commit -m "feat(frontend): registration requires physical barcode or skip reason

Physical scan block always visible after virtual scan. Below it, a
collapsed 'I can't scan my physical card' details panel with radio
options (no-physical-card / privacy-screen / forgot-today / other).
Submit disabled until physical barcode OR skip reason is provided."
```

## Task 4.4: Deploy Phase 4

- [ ] **Step 1: Push.** `git push origin main`.

- [ ] **Step 2: Smoke-test prod** by walking through a fresh registration in an incognito window.

- [ ] **Step 3: Log it.**

---

# Final verification

- [ ] **Step 1: All tests pass.** `cd backend && python -m unittest test_normalize -v`. Expected: every test class passes.
- [ ] **Step 2: Health check.** `curl https://attendance-checker-kfba.onrender.com/health` → `{"status": "ok", ...}`.
- [ ] **Step 3: Claim-log audit.** Open `/debug/claims?key=...`. Review the last 50 attempts. Tally: how many `matched_barcode` hits used the canonical form vs. the check-digit/prefix variants? If variants 2/3 fire >20% of the time, consider deeper investigation on the classroom scanner ingest side (out of scope here, but worth filing).
- [ ] **Step 4: Update memory.** `~/.claude/projects/.../memory/project_attendance_checker.md` — note the four new endpoints, the `claim_log` table, the `/enroll` page.
- [ ] **Step 5: Close the loop with students.** Email POM + QBA summarizing: attendance should now be accurate; report any remaining issues.
