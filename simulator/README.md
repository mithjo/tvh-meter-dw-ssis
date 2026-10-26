# Source simulator — Trillium Valley Hydro (fictional)

This folder fakes the outside world for the SSIS project: one customer system (TVH_CIS)
and four file feeds. It is test-data tooling, not part of the ETL. Everything is
synthetic; names, addresses, vendors and prices are invented.

## Quick start

```bat
cd simulator
python generate_sources.py --seed <your number> --meters 120 --days 30    :: dev dataset, ~1 s
python generate_sources.py --seed <your number>                           :: full dataset, ~1 min, ~360 MB
```

Then, in SSMS: run `sql/source/01_create_TVH_CIS.sql`, then `simulator/output/cis_seed/load_seed.sql`.

Each "night":

```bat
python deliver.py --next --inbound C:\TVH\data\inbound --apply-sql
```

That applies the CIS changes recorded the day before and copies the files the vendors
delivered this morning into `inbound\<feed>\`. Then run the SSIS master package for the
business date it prints. Without `--apply-sql` it writes one `.sql` file for you to run in SSMS.

| Option | Default | Meaning |
| --- | --- | --- |
| `--seed` | required | Your own seed. Same seed and options = same data, byte for byte. |
| `--start` | 2025-10-15 | First business date. |
| `--days` | 152 | Business days (default ends 2026-03-15). |
| `--meters` | 800 | Meters at the start; meter swaps add more. |
| `--defect-scale` | 1.0 | Multiplies every defect rate. Use 3 to stress-test your rules. |

The default window covers a rate change (2025-11-01), both daylight-saving changes
(2025-11-02 has 25 hours, 2026-03-08 has 23), and four statutory holidays.

## Output

| Path | What it is |
| --- | --- |
| `output/cis_seed/*.csv` | CIS snapshot as of the start date, 00:00 local |
| `output/cis_seed/load_seed.sql` | BULK INSERT script with this machine's paths |
| `output/cis_changes/YYYY-MM-DD.sql` | CIS changes made during that day; idempotent, order-checked |
| `output/outbox/<delivery date>/<feed>/` | Files grouped by the morning they arrive |
| `output/answer_key/true_daily_kwh.csv` | True kWh per meter per local date, with interval count |
| `output/answer_key/defects.csv` | Every injected defect: feed, file, meter, interval, type |
| `output/answer_key/file_events.csv` | Late, duplicate, correction, rejected and resubmitted files |
| `output/answer_key/cis_events.csv` | Every CIS change, including meter-swap recording lag |
| `output/manifest.json` | Parameters and counts |

The answer key is for your tests (Session 24 of the playbook). The ETL must never read it.

Note: `generate_sources.py` deletes and recreates its `--out` folder on every run, so
never point `--out` at a folder that holds anything else.

## Feed specifications

### Head-end North — CSV, cumulative register reads, UTC

- File: `NORTH_<yyyymmdd>_<nn>.csv`, UTF-8, LF line endings, header row.
- Columns: `meter_id, read_time_utc, register_kwh, read_type`.
- `read_time_utc` is ISO 8601 with `Z`, the END of each hour. The file for local date D holds
  the snapshots that close every hour of D (23, 24 or 25 per meter).
- `register_kwh` is the cumulative dial reading, 3 decimals. Hourly kWh =
  (this snapshot − previous snapshot) × `cis.Meter.Multiplier`.
- `read_type`: `A` actual, `E` estimated by the head-end.
- `NORTH_20251014_01.csv` is the go-live baseline: one snapshot per meter at the start date's
  local midnight. It produces no usage; it seeds the "previous snapshot".
- A new meter's first hour needs `cis.MeterInstall.InitialRegisterKwh` as its baseline.

### Head-end Lake — XML, 15-minute kWh, local time with offset

- File: `LAKE_<yyyymmdd>_<nn>.xml`, UTF-8. Contract: `specs/lake_interval.xsd`.
- `<IntervalData headEnd dataDate fileVersion generatedAt>` → `<Meter id uom>` → `<Reading start minutes value q>`.
- `start` looks like `2025-11-02T01:15:00-05:00`. The offset is the only thing that separates
  the two 01:00–02:00 hours on the fall-back day.
- `q`: `A` actual, `E` estimated by the vendor.
- `fileVersion="2"` is a correction for some meters of an earlier file with the same data date.
  For those meters, version 2 replaces version 1 entirely.

### Head-end Ridge — fixed width, hourly Wh, hour-ending, EST all year

- File: `RIDGE_<yyyymmdd>_<nn>.txt`, ASCII, CRLF, every record 36 characters.
- The file date is an Eastern STANDARD time date (UTC−5 all year, no daylight saving).
  Hour-ending 01 = 00:00–01:00 EST = 05:00–06:00 UTC. In summer time a Ridge "day" runs
  01:00 to 01:00 local clock time.

| Record | Positions (1-based) | Field |
| --- | --- | --- |
| Header `H` | 1 | Record type `H` |
| | 3–12 | Head-end code, left-justified |
| | 14–21 | File date `yyyymmdd` (EST) |
| | 23–36 | Created timestamp `yyyymmddhhmmss` |
| Detail `D` | 1 | Record type `D` |
| | 3–12 | Meter number, left-justified, space-padded |
| | 14–21 | Read date `yyyymmdd` (EST) |
| | 23–24 | Hour ending `01`–`24` |
| | 26–32 | Consumption in Wh, zero-padded integer |
| | 34–35 | Status: `00` good, `07` estimated by head-end, `99` communication failure |
| Trailer `T` | 1 | Record type `T` |
| | 3–9 | Count of detail records |
| | 11–20 | Sum of the Wh field over all detail records |

- Status `99` arrives with `0000000` Wh. That is missing data, not zero usage.
- If the trailer count or total does not match the details, reject the whole file. The
  vendor sends a complete replacement (next sequence number) a day later.

### Weather — JSON, hourly

- File: `WEATHER_<yyyymmdd>_<nn>.json`, one per local date.
- `{"station", "date", "timezone", "hourly": [{"hour_start_local", "temp_c", "humidity_pct"}]}`.
- `hour_start_local` carries an offset, like Lake. `temp_c` is sometimes `null`.

## Problems you will find (on purpose)

| Feed | Problem | Where to handle it |
| --- | --- | --- |
| All | File delivered 2–4 days late | Late-arriving data, rebuild affected days |
| North, Lake, Ridge | Same file sent twice, sometimes byte-identical, sometimes not | File hash, then row-level idempotency |
| North | Missing snapshots (1–4 hours) | Gap rule: spread the register difference |
| North | One snapshot drops then recovers | Bad-register rule |
| North | Duplicate row; `ERR` register; hour `25` | Dedup; error output to quarantine |
| North, Ridge | Meter id in lower case or with spaces | Cleansing before lookup |
| North | `LAB00001`, a lab meter that never appears in CIS | Inferred member that never resolves |
| Lake | Negative value; spike about 40× normal | Validation rules |
| Lake | Missing 15-minute reading; meter missing all day | Completeness |
| Lake | fileVersion 2 corrections | Latest version wins |
| Ridge | Status 99 with 0 Wh; status 07 | Missing vs zero; estimated flag |
| Ridge | Trailer mismatch, then a resubmission | Control totals, file rejection |
| CIS | Meter swap recorded 0–2 days after it happened | Early-arriving facts, inferred meters |
| CIS | Move-out / move-in, rate plan switch, segment change | SCD Type 2 and point-in-time lookups |
| CIS | Names with apostrophes (O'Neil, D'Souza) | Quoting in every script you write |
