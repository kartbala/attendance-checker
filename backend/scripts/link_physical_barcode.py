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
