# Bulk Enroll -- Virtual Barcodes (Design)

**Date:** 2026-04-21
**Status:** Approved, ready for implementation plan
**Parent:** `2026-04-20-physical-barcode-enrollment-plan.md` (Phase 2)

## Problem

The existing `/enroll` bulk-enrollment page (shipped 2026-04-20) only captures physical Bison card barcodes. In-class, most students will register the virtual barcode from the Bison mobile app -- there is no admin-driven bulk flow for that. Karthik wants a second link for virtual, so the line at the classroom laptop can pick the right flow per student.

## Goals

- One dedicated URL per barcode type (virtual, physical) for in-class use.
- Each page has a single barcode field so USB-scanner Enter-to-submit works cleanly.
- Typo protection on virtual: block if the scanned barcode is already claimed by another student in any of this student's courses.
- **Registration scan doubles as attendance scan:** when a student registers at the laptop in class, that event also marks them present for the session in the class they're attending. Eliminates the "register + then also walk past the wall scanner" double-step.
- Minimal changes to the shipped physical flow (one additive field for attendance-on-link).

## Non-goals

- Not adding collision protection to `/admin/link-physical` (separate concern, small follow-up if wanted).
- Not mode-toggling a single page.
- Not requiring HUID on the admin-driven bulk path (consistent with `/admin/link-physical`).
- Not combining virtual + physical into one submit.

## URLs

URLs accept an optional `course=<course_code>` query parameter. When present, the admin endpoint also writes an attendance row for today. When absent, behavior is unchanged (link only, no attendance write -- backward-compatible with existing bookmarks).

| Kind | URL |
|------|-----|
| Physical (existing shape) | `https://attendance-checker-kfba.onrender.com/enroll?key=...&course=INFO-335-04` |
| Virtual (new) | `https://attendance-checker-kfba.onrender.com/enroll-virtual?key=...&course=INFO-335-04` |

## Files

**New:**

- `backend/static/enroll-virtual.html` -- near-mirror of `enroll.html` with swapped labels and endpoint.
- `/enroll-virtual` route in `backend/app.py` (one-liner, serves the static file).
- `/admin/link-virtual` POST endpoint in `backend/app.py`.
- `AdminLinkVirtualTest` class in `backend/test_normalize.py` (~6 tests).

**Modified (minimal):**

- `enroll.html` -- read `course` from URL params, pass into POST body.
- `/admin/link-physical` endpoint -- accept optional `course_code`, if present and valid, write an attendance row for today (same logic as the new endpoint).

**Unchanged:** `/register`, `/claim-physical-barcode`, the React frontend.

## Endpoint: `/admin/link-virtual`

**Method:** POST
**Auth:** `X-Sync-Key` header matching `SYNC_API_KEY` (same helper as `/admin/link-physical`).
**Body:**

```json
{"email": "student@bison.howard.edu", "barcode_id": "1234567890", "course_code": "INFO-335-04"}
```

`course_code` is optional. When present and the student is enrolled in that course, the endpoint writes an attendance row for today. When absent, behavior is link-only.

**Validation:**

1. `email` and `barcode_id` required (non-empty after strip).
2. `barcode_id` must be numeric (via existing `BARCODE_RE`).
3. Normalize with existing `normalize_barcode()` (strips leading zeros).
4. Reject if normalized barcode is empty or equals `"0"` -- same guard as `/claim-physical-barcode`.
5. If `course_code` is present, it must match one of the student's enrolled courses. If not, return 400 (caller bug, wrong URL).

**Lookup:**

1. Fetch all student rows for this email (may be >1 for students in multiple courses). 404 if none.
2. Collect distinct `course_code` values for the email.

**Collision check:**

Block if any OTHER student in any of those same courses has `barcode_id` equal to the normalized submitted value. Pattern adapted from `/claim-physical-barcode` lines 946--951:

```sql
SELECT email FROM student
WHERE barcode_id = ?
  AND email != ?
  AND course_code IN (...)
LIMIT 1
```

Re-linking the same email to the same barcode is allowed (the `email != ?` clause handles this).

On collision, return HTTP 409 with `{"error": "barcode already claimed by another student in this course"}`.

**Write:**

1. Snapshot `absent_before = _compute_attendance_delta(db, email)`.
2. `UPDATE student SET barcode_id = ? WHERE email = ?`.
3. If `course_code` was provided and validated, `INSERT INTO attendance (student_id, course_code, scan_date, scan_timestamp) VALUES (?, ?, ?, ?)` with `student_id` = normalized barcode, `course_code` = provided, `scan_date` = today (UTC `YYYY-MM-DD`), `scan_timestamp` = now UTC ISO 8601.
4. `commit()`.
5. `absent_after = _compute_attendance_delta(db, email)`.

`_compute_attendance_delta` (app.py lines 79--91) already considers both `barcode_id` and `physical_barcode_id` when computing matches, so setting virtual retroactively captures scans that happened before registration.

**Duplicate attendance:** If a student walks past the wall scanner AND also registers at the laptop in the same session, there will be two attendance rows for that date. The `/attendance` count is `DISTINCT scan_date`, so no double-credit -- just one harmless extra row.

**Response:**

```json
{
  "success": true,
  "email": "student@bison.howard.edu",
  "barcode_id": "1234567890",
  "rows_updated": 2,
  "attendance_marked": true,
  "attendance_delta": {"absent_before": 5, "absent_after": 0}
}
```

`attendance_marked` is `true` when the endpoint wrote an attendance row for today, `false` when `course_code` was absent. Mirrors `/admin/link-physical` for UI consistency (physical gets the same new field).

## Frontend: `enroll-virtual.html`

Near-clone of `enroll.html` with these swaps:

| Field | `enroll.html` (physical) | `enroll-virtual.html` (virtual) |
|-------|--------------------------|---------------------------------|
| `<title>` | "Bulk Enroll -- Physical Barcodes" | "Bulk Enroll -- Virtual Barcodes" |
| `<h1>` | "Bulk Enroll -- Physical Barcodes" | "Bulk Enroll -- Virtual Barcodes" |
| Barcode `<label>` | "Physical card barcode (scan now)" | "Virtual card barcode (scan from phone)" |
| Barcode `<input placeholder>` | "(scan)" | "(scan from Bison app)" |
| POST URL | `/admin/link-physical` | `/admin/link-virtual` |
| JSON body key | `physical_barcode_id` | `barcode_id` |
| Success log line | "barcode X linked" | "virtual barcode X linked" |

**Both pages** also read the `course` URL query parameter and include it in the POST body as `course_code` when present. An extra "Course: INFO-335-04" line appears under the auth badge when the param is set, so Karthik can eyeball which course the laptop is set up for.

Everything else (email validation, auth badge, Tab-from-email-to-barcode, auto-reset, 30-entry rolling log) is identical.

## Route

Add to `backend/app.py` next to `/enroll`:

```python
@app.route("/enroll-virtual")
def enroll_virtual_page():
    return app.send_static_file("enroll-virtual.html")
```

## Tests

New `AdminLinkVirtualTest` class in `backend/test_normalize.py`, parallel to the existing physical-link tests:

1. Happy path: POST with email + barcode (no `course_code`), verify DB write, response shape, `attendance_marked: false`.
2. Missing email -- 400.
3. Missing barcode -- 400.
4. Bad/missing auth -- 401 (matches `/admin/link-physical`).
5. Email not found -- 404.
6. Collision in same course -- 409.
7. Retroactive delta counts prior orphan scans.
8. Barcode normalization strips leading zeros.
9. **With `course_code`:** POST with email + barcode + `course_code`, verify an `attendance` row was inserted with today's date and `attendance_marked: true`.
10. **Invalid `course_code`:** POST with a `course_code` the student is not enrolled in -- 400, no attendance write.
11. **Duplicate attendance tolerated:** call the endpoint twice in a row with the same `course_code`; both succeed; `/attendance` still reports `sessions_attended == 1` for today (DISTINCT scan_date semantics).

Parallel 2 new tests on existing `AdminLinkPhysicalTest` for the same `course_code` additions (happy path writes attendance, without-course_code remains link-only).

Uses the existing `_fresh_app()` real-sqlite pattern (no mocking).

## Deploy / smoke-test

1. `cd backend && python -m unittest test_normalize -v` -- all tests pass (51 existing + ~8 new).
2. `git push origin main` -- Render auto-deploys in 1--3 min.
3. `curl -s https://attendance-checker-kfba.onrender.com/enroll-virtual` -- should return the HTML.
4. Open `https://attendance-checker-kfba.onrender.com/enroll-virtual?key=...` in incognito, verify green "Admin key loaded" badge.
5. Link one test student manually; verify `barcode_id` written and delta returned.

## Rollback

Revert the single feature commit. Physical flow unaffected.

## Open follow-ups (not in this scope)

- If in-class use reveals typos on physical cards, add matching collision check to `/admin/link-physical`.
- If it turns out students often want to register both at once, reconsider combined mode (but `/register` already covers that case for students with HUID).
