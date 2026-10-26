#!/usr/bin/env python3
"""
Trillium Valley Hydro (fictional) - "the morning delivery"
==========================================================

Simulates time passing. For a run date X (the morning the nightly ETL runs):

  1. CIS changes recorded on every day before X are applied to TVH_CIS
     (with --apply-sql, through sqlcmd), or written to one script you run in SSMS.
  2. Every file the vendors deliver on or before X is copied from
     output/outbox/<delivery-date>/<feed>/ into <inbound>/<feed>/.

Then run the SSIS master package for business date X - 1.

Examples:
    python deliver.py --run-date 2025-10-16 --inbound C:\\TVH\\data\\inbound --apply-sql
    python deliver.py --next --inbound C:\\TVH\\data\\inbound --apply-sql     (one more day)
    python deliver.py --status
    python deliver.py --reset          (forget what was delivered; does not touch SQL Server)

Standard library only.
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
FEEDS = ("north", "lake", "ridge", "weather")


def load_state(out):
    p = out / "deliver_state.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"last_run_date": None, "changes_done": [], "deliveries_done": []}


def save_state(out, state):
    (out / "deliver_state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Deliver simulated CIS changes and vendor files.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--run-date", type=date.fromisoformat, help="morning of the ETL run (YYYY-MM-DD)")
    g.add_argument("--next", action="store_true", help="advance one day from the last run date")
    g.add_argument("--status", action="store_true", help="show what has been delivered")
    g.add_argument("--reset", action="store_true", help="forget delivery history")
    ap.add_argument("--out", default=str(HERE / "output"), help="generator output folder")
    ap.add_argument("--inbound", default=r"C:\TVH\data\inbound", help="SSIS inbound root folder")
    ap.add_argument("--apply-sql", action="store_true", help="apply CIS changes with sqlcmd")
    ap.add_argument("--server", default="localhost", help="SQL Server instance for sqlcmd")
    ap.add_argument("--move", action="store_true", help="move files instead of copying them")
    a = ap.parse_args(argv)

    out = Path(a.out).resolve()
    manifest_path = out / "manifest.json"
    if not manifest_path.exists():
        sys.exit(f"No manifest at {manifest_path}. Run generate_sources.py first.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    start = date.fromisoformat(manifest["start"])
    state = load_state(out)

    if a.reset:
        save_state(out, {"last_run_date": None, "changes_done": [], "deliveries_done": []})
        print("Delivery history cleared. Empty the inbound folders and rebuild TVH_CIS to start over.")
        return
    if a.status:
        print(json.dumps({"window": [manifest["start"], manifest["end"]],
                          "last_run_date": state["last_run_date"],
                          "cis_change_days_done": len(state["changes_done"]),
                          "delivery_days_done": len(state["deliveries_done"])}, indent=2))
        return

    if a.next:
        if not state["last_run_date"]:
            run_date = date.fromisoformat(manifest["first_run_date"])
        else:
            run_date = date.fromisoformat(state["last_run_date"]) + timedelta(days=1)
    elif a.run_date:
        run_date = a.run_date
    else:
        ap.error("give --run-date, --next, --status or --reset")
    if run_date <= start:
        sys.exit(f"The first possible run date is {start + timedelta(days=1)}.")

    # 1. CIS changes for every day before the run date
    change_dir = out / "cis_changes"
    pending = sorted(p for p in change_dir.glob("*.sql")
                     if date.fromisoformat(p.stem) < run_date and p.stem not in state["changes_done"])
    if pending:
        if a.apply_sql:
            exe = shutil.which("sqlcmd")
            if not exe:
                sys.exit("sqlcmd was not found on PATH. Install it or run without --apply-sql.")
            for p in pending:
                r = subprocess.run([exe, "-S", a.server, "-E", "-b", "-i", str(p)],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    print(r.stdout, r.stderr)
                    save_state(out, state)
                    sys.exit(f"sqlcmd failed on {p.name}. Nothing after it was applied.")
                state["changes_done"].append(p.stem)
            print(f"Applied CIS changes for {len(pending)} day(s): {pending[0].stem} to {pending[-1].stem}")
        else:
            script = out / f"apply_cis_changes_through_{(run_date - timedelta(days=1)).isoformat()}.sql"
            with open(script, "w", encoding="utf-8") as f:
                for p in pending:
                    f.write(p.read_text(encoding="utf-8"))
                    f.write("\nGO\n")
            state["changes_done"] += [p.stem for p in pending]
            print(f"Run this in SSMS before the ETL (safe to run twice):\n    {script}")
    else:
        print("No new CIS changes.")

    # 2. Vendor files delivered on or before the run date
    inbound = Path(a.inbound)
    moved = {f: 0 for f in FEEDS}
    for day_dir in sorted((out / "outbox").iterdir()):
        if not day_dir.is_dir() or day_dir.name in state["deliveries_done"]:
            continue
        if date.fromisoformat(day_dir.name) > run_date:
            continue
        for feed in FEEDS:
            src = day_dir / feed
            if not src.exists():
                continue
            dest = inbound / feed
            dest.mkdir(parents=True, exist_ok=True)
            for f in sorted(src.iterdir()):
                (shutil.move if a.move else shutil.copy2)(str(f), str(dest / f.name))
                moved[feed] += 1
        state["deliveries_done"].append(day_dir.name)

    state["last_run_date"] = run_date.isoformat()
    save_state(out, state)
    print("Files delivered: " + ", ".join(f"{k} {v}" for k, v in moved.items()))
    print(f"Now run the ETL for business date {run_date - timedelta(days=1)}.")


if __name__ == "__main__":
    main()
