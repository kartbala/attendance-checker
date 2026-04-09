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
