# Physical Barcode Enrollment -- Design

**Date:** 2026-04-20
**Author:** Karthik Balasubramanian (with Claude)
**Status:** Design approved, pending implementation plan

## Problem

Students who check in with their physical Bison card instead of the virtual card are being marked absent. Example: Charrikka Gordon (POM INFO-335-04) reports 16 absences when she believes she has only missed 3 classes. She uses her physical card because a phone privacy screen prevents the virtual barcode from scanning.

The backend already unions virtual and physical barcodes at attendance query time (`app.py:288`). The gap is in:

1. **Data capture** -- students skipped the "optional" physical barcode scan at registration, so their physical card barcode isn't on file.
2. **Retroactive repair** -- there is no post-registration path to add a physical barcode and re-count past scans.

## Scope

**In scope.**

1. Registration UX: physical scan becomes required, with a logged skip-reason path for students who genuinely can't or won't provide one.
2. Self-service claim flow for already-registered students (banner + scan + retroactive re-count).
3. Orphan-scan detection to gate the banner (surface only to students who'd benefit).
4. Conservative normalized matching during claim: strip non-digits, strip leading zeros, try with/without trailing check digit and with/without leading symbology prefix. No fuzzy/edit-distance matching.
5. Admin bypass tools: `/debug` page extension, CLI script, and in-class bulk re-enrollment page.
6. Immediate unsticking of current harmed students (Charrikka etc.) using the admin bypass in advance of self-service.

**Out of scope.**

- Re-engineering the Apps Script scanner ingest pipeline.
- Auth beyond the existing "a scan is proof of identity" trust model.
- Forcing existing students to re-register. They route through the claim flow or in-class bulk re-enrollment.

## Why these choices

- **Required-with-skip registration** (over optional-but-promoted) closes the long-term data quality gap. The logged skip reason gives us signal on whether privacy-screens and similar edge cases warrant further UX work.
- **Conservative matching** (over permissive fuzzy matching) preserves the no-auth trust model. A student can only claim a barcode whose normalized form matches an actual orphan scan in their section. No edit-distance, no pick-from-candidates UI.
- **In-class bulk re-enrollment** eliminates the camera-vs-scanner decoding mismatch risk by construction: the classroom USB scanner is the same hardware that captures daily attendance, so barcodes captured during re-enrollment are byte-identical to historical orphan scans.
- **Phased rollout** ships the admin bypass first so Charrikka and any other student currently affected can be unstuck within a day, before the self-service flow is built.

## Architecture

One new table, one new column, three new endpoints, one changed endpoint, and one shared frontend component extracted from existing code.

### Data model changes

```sql
ALTER TABLE student ADD COLUMN physical_barcode_skip_reason TEXT;

CREATE TABLE IF NOT EXISTS claim_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempted_at TEXT NOT NULL,
    email TEXT,
    submitted_barcode TEXT,
    variants_tried TEXT,    -- JSON array
    matched_barcode TEXT,   -- NULL if no match
    course_code TEXT,
    absent_before INTEGER,
    absent_after INTEGER
);
```

`physical_barcode_skip_reason` is NULL for all existing rows, which correctly means "has not registered a physical card yet." Only set when a student explicitly opts out during registration.

### Backend endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /claim-physical-barcode` | none | Student self-service. Computes normalized variants, tries to match against orphan scans in the student's course(s), saves the matched form (or the normalized raw form if no match), returns attendance delta. |
| `POST /admin/link-physical` | `X-Sync-Key` header | Admin bypass. Writes `physical_barcode_id` without match validation. Returns delta. |
| `GET /enroll?key=...` | query-param admin key | Serves static HTML page for in-class bulk re-enrollment. |

Changed:

- `POST /register` -- accept either `physical_barcode_id` or `physical_barcode_skip_reason`. Reject with 400 if both missing.
- `GET /attendance` -- response adds `section_orphan_count` (distinct barcodes in `attendance` for this course that don't match any registered `barcode_id` or `physical_barcode_id`) and `has_physical_barcode` (bool).
- `GET /debug` -- adds a "Link physical barcode" form at top when accessed with `?email=...`.

### Matching logic

When a student submits a barcode via `/claim-physical-barcode`, generate candidate variants:

1. `normalize_barcode(raw)` -- current implementation: strip leading zeros.
2. Strip non-digits, then normalize leading zeros.
3. Variant 2 minus trailing digit (check-digit candidate).
4. Variant 2 minus leading digit (symbology-prefix candidate).

For each variant, query: does any row in `attendance` for any of the student's courses (a student may be enrolled in multiple; look up all `student` rows by email) have `student_id` equal to this variant? The first variant with at least one match is the "matched form" -- save that as `physical_barcode_id` on **every** student row for that email so daily `/attendance` queries stay on the exact-match path across all their courses.

If no variant matches any orphan in any of the student's courses, save the normalized form (variant 2) anyway across all rows. Return `delta == 0`; frontend prompts the student to email Dr. B.

Every attempt is logged to `claim_log` including variants tried and final delta.

### Frontend changes

**`RegisterForm.tsx`** -- reorganize so physical scan is always visible after virtual scan. Replace the optional toggle with an inline second scan block. Add an "I can't scan my physical card" link revealing a radio + text group (*No physical card*, *Privacy screen blocks it*, *Forgot card today*, *Other*). Submit disabled until physical barcode or skip reason is set.

**`AttendanceView.tsx`** -- read `section_orphan_count` and `has_physical_barcode` from response. Render claim banner when:

- `!has_physical_barcode` AND
- `unexcused_count >= 2` AND
- `section_orphan_count >= 1`.

Banner button opens a barcode scanner modal, POSTs to `/claim-physical-barcode`, handles the two response cases (delta > 0: toast + refetch; delta == 0: error modal with copy-paste-friendly barcode value and instructions to email Dr. B).

**New shared component** -- `BarcodeScanner.tsx` extracted from the existing `RegisterForm` camera/USB logic, reused by both `RegisterForm` and `AttendanceView`.

**New `backend/static/enroll.html`** -- minimal static HTML + vanilla JS. Two fields (email autofocus, barcode auto-submit on Enter), AJAX POST to `/admin/link-physical`, inline activity log of the last ~20 enrollments this session.

### CLI

`backend/scripts/link_physical_barcode.py` -- POSTs to `/admin/link-physical` using `SYNC_API_KEY`. Supports single-link flags (`--email`, `--physical-barcode`) and bulk (`--csv path`).

## Rollout phases

**Phase 1 -- Admin bypass.**

1. Add `/admin/link-physical` endpoint.
2. Extend `/debug` page.
3. Deploy.
4. Manually unstick Charrikka and any similar cases using `/debug?email=...` to eyeball orphan dates, paste the matching barcode into the form. Reply to their emails with "re-check the tracker."

**Phase 2 -- In-class bulk re-enrollment.**

1. Implement `/enroll.html` + the static page.
2. Test locally with a USB scanner.
3. Run a ~15-minute session at the start of the next POM and QBA class. Captures physical barcodes for every present student with byte-exact format match to historical scans.

**Phase 3 -- Self-service claim flow.**

1. `POST /claim-physical-barcode`, `claim_log` table, variant matching.
2. `/attendance` response additions.
3. `AttendanceView.tsx` banner + shared `BarcodeScanner`.
4. Deploy. Any student absent during Phase 2 can still self-resolve.

**Phase 4 -- Required-with-skip registration.**

1. `POST /register` contract change + `physical_barcode_skip_reason` column.
2. `RegisterForm.tsx` reorganization.
3. Deploy. Affects only new registrations; existing students are handled by Phase 2/3.

## Testing

Backend, added to `backend/test_normalize.py`:

- `normalize_barcode_variants` returns the expected set for leading-zero, non-digit, and check-digit inputs.
- `POST /claim-physical-barcode`: happy path (match found, delta > 0), no-match path (delta == 0, write still occurs, claim_log populated), multi-course student (writes to both student rows).
- `POST /admin/link-physical`: 401 on missing/bad key, success on valid key, idempotent on repeat.
- `POST /register`: 400 when both `physical_barcode_id` and skip reason missing, 200 when either present.

Frontend: no unit tests (project has none currently). Manual smoke-test checklist per phase:

- Phase 1: `/debug` form links a test student, delta appears in `/attendance` response.
- Phase 2: full in-class flow on a USB scanner into `/enroll.html`; verify 5 students can go through in under 2 minutes.
- Phase 3: banner renders only when gating conditions met; claim flow with matching barcode flips absences to present; claim flow with non-matching barcode shows escalation message.
- Phase 4: registration form rejects empty-both, accepts either.

## Observability

`claim_log` table captures every claim attempt. A `GET /debug/claims?key=...` admin page renders the last 50 attempts so Karthik can see how often camera-vs-scanner mismatch actually bites and tune variant generation if needed.

## Migration concerns

No destructive migrations. The new column is nullable. The new table is additive. Existing `physical_barcode_id` values are untouched. `normalize_barcode` behavior is unchanged (the variants live only in the claim endpoint, not in the stored form or daily query path).

## Non-goals / rejected alternatives

- **Edit-distance fuzzy matching** -- rejected as opening a social-engineering window that the no-auth trust model can't defend against.
- **Forcing all existing students to re-register** -- rejected as disruptive; the claim flow covers this without user-visible friction.
- **Normalizing scanner ingest more aggressively** (e.g., stripping check digits at the scanner/Apps Script layer) -- rejected as a larger change with risk of breaking existing matches; deferred unless `claim_log` data shows it's warranted.
