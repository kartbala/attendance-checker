# Attendance Checker

Student-facing web app for viewing attendance in Dr. B's classes.

## Architecture

- **Frontend:** React/Vite/Tailwind at `balasubramanian.org/attendance/`
- **Backend:** Flask API on Render with SQLite (`checker.db`)
- **Source of truth:** `life-agent/data/life_agent.db` (local), synced to Render periodically

## Related Projects

- **`Sandbox/barcode-scanner/`** -- Karthik's instructor-side attendance scanner. Writes attendance scans to Google Sheets. This project shares the `useUsbScanner` hook and `html5-qrcode` camera scanning patterns from that codebase.
- **`Sandbox/life-agent/`** -- Teaching pipeline backend. Owns the source-of-truth DB. The sync script (`life-agent/scripts/sync_to_render.py`) pushes student/attendance/excused data to this app's Render backend and pulls barcode registrations back.

## Data Flow

1. Karthik scans barcodes in class using `barcode-scanner` -> Google Sheets
2. `life-agent` ingests attendance from Sheets -> `life_agent.db`
3. `life-agent` sync script pushes data to Render -> `checker.db`
4. Students register barcode on this app -> stored in `checker.db`
5. Sync script pulls registrations back -> `life_agent.db`

## Key Files

- `backend/app.py` -- all API endpoints
- `frontend/src/components/RegisterForm.tsx` -- registration + barcode scanning
- `frontend/src/components/AttendanceView.tsx` -- attendance display
- `frontend/src/hooks/useUsbScanner.ts` -- USB barcode scanner hook (from barcode-scanner)

## Specs

- PRD: `docs/superpowers/specs/2026-04-09-student-attendance-checker-prd.md`
- Design: `docs/superpowers/specs/2026-04-09-student-attendance-checker-design.md`

## Open Issue -- 2026-04-16/17 student bug reports

Three POM students emailed Howard inbox Thu 4/16 ~10:56 PM–11:56 PM reporting wrong attendance on the tracker. Diagnosis below, nothing fixed yet.

### 1. Nia Peake -- confirmed bug (leading-zero barcode mismatch)

- Email: `nia.peake@bison.howard.edu`, HUID `003109035`
- Registered barcode: `07142851387095` (14 digits, leading zero)
- Scans in DB: `7142851387095` (13 digits) -- 18 scans from 2026-02-03 through 2026-04-16
- Backend `/attendance` does `student_id IN (barcode_id, physical_barcode_id)` exact-match (`backend/app.py:240-244`) -- so her registration never matches -> tracker returns `attended: 0 / total: 21`
- She is the only registered student with a leading-zero barcode today (checked `/sync/pull`)
- DB-wide pattern: 797 scans at 14 digits, 82 at 13 digits, long tail shorter. Symptomatic of Code128/UPC scanners dropping leading zeros -- this WILL recur

**Fix options (pick one):**
- **Quick:** rewrite her row -- `UPDATE student SET barcode_id='7142851387095' WHERE email='nia.peake@bison.howard.edu'` on Render. Will be clobbered next `sync_to_render.py push` unless life_agent.db is updated too (COALESCE preserves existing server value, so push is actually safe -- but double check).
- **Proper:** normalize barcodes at both write paths. In `register()`: `barcode_id.lstrip('0') or '0'`. In `sync_push()` attendance insert: same. In `/attendance` lookup: expand `barcodes` list to include both the stripped form and zero-padded forms (up to max observed length, 14). Backfill existing rows with a one-time migration.

### 2. Samara Stennett -- scanner drops, not a data bug

- `samara.stennett@bison.howard.edu`, barcode `47745392782677`
- Shows 15/21 present. Missing: 1/22, 2/12, 2/17, 2/19, 3/26, 4/2
- She says she missed only 2 -- so ~4 scanner no-reads
- 2/19 session only had 8 scans total (probably Zoom day, likely genuine absence for many)

### 3. Emily Mayne -- two issues

- `emily.mayne@bison.howard.edu`, barcode `69105055767766`, in both POM + QBA
- POM: 15/21 present -- same pattern as Samara
- **QBA: 9/21 present (42%)** -- much worse. QBA session sizes 17-32 out of 48 enrolled (wide variance), so scanner reliability in QBA looks systematically worse. This is what she's asking about in her "attendance grade is so low" email

### Context -- what was *not* the problem

- Auth/registration: all 3 are registered correctly on prod
- `/sync/pull` 0-result confusion: endpoint uses header `X-Sync-Key` (not `X-API-Key`). Silent 401 returns empty JSON
- Production DB: Render Starter, persistent disk at `/var/data/checker.db`. `/health` = ok, 103 students, last scan 2026-04-16

### Verification commands

```bash
# Production attendance for the 3 students
for em in nia.peake@bison.howard.edu samara.stennett@bison.howard.edu emily.mayne@bison.howard.edu; do
  curl -s "https://attendance-checker-kfba.onrender.com/attendance?email=$em&course_code=INFO-335-04" | python3 -m json.tool
done

# Pull all registrations (X-Sync-Key header is required)
curl -s -H "X-Sync-Key: yMAyLgXljmUcSl_droviK9bQNmKMNRv_f1fFbYOgAw0" \
  https://attendance-checker-kfba.onrender.com/sync/pull

# Local life_agent.db -- scan length distribution
sqlite3 ~/"Library/CloudStorage/GoogleDrive-karthik@balasubramanian.us/My Drive/Sandbox/life-agent/data/life_agent.db" \
  "SELECT LENGTH(student_id), COUNT(*) FROM attendance GROUP BY 1"
```
