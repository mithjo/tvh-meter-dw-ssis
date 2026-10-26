#!/usr/bin/env python3
"""
Trillium Valley Hydro (fictional) - source system simulator
===========================================================

Generates everything the SSIS project reads, plus an answer key for testing:

  output/cis_seed/*.csv                 CIS snapshot as of --start 00:00 local
  output/cis_seed/load_seed.sql         BULK INSERT script with this machine's paths
  output/cis_changes/YYYY-MM-DD.sql     CIS changes made during that local day
  output/outbox/<delivery-date>/<feed>/ head-end and weather files, grouped by the
                                        morning they are delivered (use deliver.py)
  output/answer_key/                    ground truth: daily kWh per meter, every
                                        injected defect, every file event, CIS events
  output/manifest.json                  parameters and counts

Standard library only. America/Toronto daylight-saving rules are implemented
directly (2nd Sunday of March 02:00 -> 1st Sunday of November 02:00), so the
script runs on Windows without the tzdata package.

Usage (development dataset, about 1 minute):
    python generate_sources.py --seed 777 --meters 120 --days 30

Usage (full dataset for performance work, a few minutes):
    python generate_sources.py --seed 777

Pick your OWN seed: it makes your dataset different from anyone else's.
"""

import argparse
import json
import math
import random
import shutil
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

# --------------------------------------------------------------------------
# Time zone helpers: America/Toronto without tzdata
# --------------------------------------------------------------------------
_DST_CACHE = {}


def _dst_window_utc(year):
    """UTC instants when daylight time starts and ends in Ontario for a year."""
    mar1 = date(year, 3, 1)
    second_sun_mar = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    nov1 = date(year, 11, 1)
    first_sun_nov = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    start = datetime(year, 3, second_sun_mar.day, 7)   # 02:00 EST = 07:00 UTC
    end = datetime(year, 11, first_sun_nov.day, 6)     # 02:00 EDT = 06:00 UTC
    return start, end


def offset_hours(utc_dt):
    """-4 during EDT, -5 during EST, for a naive UTC datetime."""
    y = utc_dt.year
    if y not in _DST_CACHE:
        _DST_CACHE[y] = _dst_window_utc(y)
    start, end = _DST_CACHE[y]
    return -4 if start <= utc_dt < end else -5


def utc_to_local(utc_dt):
    return utc_dt + timedelta(hours=offset_hours(utc_dt))


def local_to_utc(local_dt):
    """Convert a local wall-clock time that is neither skipped nor repeated."""
    cand = local_dt + timedelta(hours=5)
    if offset_hours(cand) == -4:
        cand = local_dt + timedelta(hours=4)
    return cand


def local_midnight_utc(d):
    return local_to_utc(datetime(d.year, d.month, d.day))


def fmt_offset(utc_dt):
    return "-04:00" if offset_hours(utc_dt) == -4 else "-05:00"


def iso_local(utc_dt):
    return utc_to_local(utc_dt).strftime("%Y-%m-%dT%H:%M:%S") + fmt_offset(utc_dt)


def iso_utc(utc_dt):
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def sql_dt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def csv_field(v):
    if v is None:
        return ""
    s = str(v)
    if "," in s or '"' in s:
        return '"' + s.replace('"', '""') + '"'
    return s


def sql_str(s):
    if s is None:
        return "NULL"
    return "N'" + str(s).replace("'", "''") + "'"


def ontario_holidays(years):
    """Ontario statutory holidays used by TVH's (fictional) TOU calendar."""
    out = {}
    for y in years:
        def nth_weekday(month, weekday, n):
            d = date(y, month, 1)
            d += timedelta(days=(weekday - d.weekday()) % 7)
            return d + timedelta(days=7 * (n - 1))

        # Easter (anonymous Gregorian algorithm) -> Good Friday
        a = y % 19
        b, c = divmod(y, 100)
        d_, e = divmod(b, 4)
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d_ - g + 15) % 30
        i, k = divmod(c, 4)
        l_ = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l_) // 451
        month = (h + l_ - 7 * m + 114) // 31
        day = ((h + l_ - 7 * m + 114) % 31) + 1
        easter = date(y, month, day)
        may25 = date(y, 5, 25)
        victoria = may25 - timedelta(days=(may25.weekday() - 0) % 7 or 7)
        out[date(y, 1, 1)] = "New Year's Day"
        out[nth_weekday(2, 0, 3)] = "Family Day"
        out[easter - timedelta(days=2)] = "Good Friday"
        out[victoria] = "Victoria Day"
        out[date(y, 7, 1)] = "Canada Day"
        out[nth_weekday(9, 0, 1)] = "Labour Day"
        out[nth_weekday(10, 0, 2)] = "Thanksgiving"
        out[date(y, 12, 25)] = "Christmas Day"
        out[date(y, 12, 26)] = "Boxing Day"
    return out


# --------------------------------------------------------------------------
# Reference data (all fictional)
# --------------------------------------------------------------------------
FIRST_NAMES = [
    "Aarav", "Abena", "Adrian", "Aisha", "Alejandro", "Amara", "Anika", "Arjun",
    "Beatriz", "Benjamin", "Bilal", "Camila", "Chen", "Chloe", "Daniel", "Deepa",
    "Diego", "Elena", "Emeka", "Emma", "Farah", "Gabriel", "Grace", "Hana",
    "Hassan", "Isabella", "Jasleen", "Jin", "Jonah", "Kavya", "Kenji", "Laila",
    "Liam", "Lucia", "Malik", "Maya", "Mei", "Mohammed", "Nadia", "Noah",
    "Olivia", "Omar", "Priya", "Rafael", "Rohan", "Sakura", "Samuel", "Sara",
    "Tariq", "Thea", "Tomas", "Uma", "Victor", "Wei", "Yara", "Yusuf", "Zara",
]
LAST_NAMES = [
    "Abbott", "Adeyemi", "Alvarez", "Bhatt", "Bouchard", "Campbell", "Chen",
    "Costa", "D'Souza", "Dubois", "Fernandes", "Gagnon", "Gill", "Gupta",
    "Haddad", "Ivanova", "Kaur", "Kim", "Kowalski", "Lam", "Leblanc", "MacLeod",
    "Mensah", "Mohammed", "Nakamura", "Nguyen", "O'Neil", "Okafor", "Patel",
    "Pereira", "Rahman", "Reyes", "Rossi", "Santos", "Sharma", "Singh", "Smith",
    "Tremblay", "Wong", "Yilmaz",
]
STREETS = [
    "Maple", "Cedar", "Birch", "Willow", "Trillium", "Lakeshore", "Ridgeview",
    "Orchard", "Heron", "Fox Run", "Pine Hollow", "Millstone", "Riverbend",
    "Sumac", "Juniper", "Elm", "Aspen", "Granite", "Meadowlark", "Bluejay",
]
STREET_TYPES = ["St", "Ave", "Cres", "Dr", "Rd", "Crt", "Blvd", "Way"]
CITIES = ["Trillium Valley", "Cedar Falls", "North Ridge", "Lakeview Heights", "Maple Crossing"]
FSA = ["L0K", "L3T", "L4Z", "L5M", "L6P", "L7A", "L9T", "K0L", "K8N", "K9J"]
MANUFACTURERS = {
    "NORTH": [("Voltanic", "V-200R"), ("Voltanic", "V-210R")],
    "LAKE": [("Meridian Metering", "MM-RX4"), ("Meridian Metering", "MM-RX5")],
    "RIDGE": [("Kestrel Instruments", "KI-45"), ("Kestrel Instruments", "KI-47")],
}

RATE_PLANS = [
    ("TOU", "Time-of-Use", "Price depends on time of day, day type and season"),
    ("TIER", "Tiered", "Lower price up to a monthly kWh limit, higher price above it"),
    ("ULO", "Ultra-Low Overnight", "Very low overnight price, high on-peak price"),
]
# Fictional prices in cents per kWh. Winter prices take effect 2025-11-01.
RATE_PRICES = [
    # plan, period, cents, tier limit, from, to
    ("TOU", "OFF", 7.95, None, "2025-05-01", "2025-10-31"),
    ("TOU", "MID", 12.10, None, "2025-05-01", "2025-10-31"),
    ("TOU", "ON", 16.40, None, "2025-05-01", "2025-10-31"),
    ("TOU", "OFF", 8.15, None, "2025-11-01", None),
    ("TOU", "MID", 12.45, None, "2025-11-01", None),
    ("TOU", "ON", 16.85, None, "2025-11-01", None),
    ("ULO", "ULO", 2.95, None, "2025-05-01", "2025-10-31"),
    ("ULO", "OFF", 7.95, None, "2025-05-01", "2025-10-31"),
    ("ULO", "MID", 12.10, None, "2025-05-01", "2025-10-31"),
    ("ULO", "ON", 28.60, None, "2025-05-01", "2025-10-31"),
    ("ULO", "ULO", 3.05, None, "2025-11-01", None),
    ("ULO", "OFF", 8.15, None, "2025-11-01", None),
    ("ULO", "MID", 12.45, None, "2025-11-01", None),
    ("ULO", "ON", 29.10, None, "2025-11-01", None),
    ("TIER", "T1", 9.40, 600, "2025-05-01", "2025-10-31"),
    ("TIER", "T2", 11.05, None, "2025-05-01", "2025-10-31"),
    ("TIER", "T1", 9.65, 1000, "2025-11-01", None),
    ("TIER", "T2", 11.30, None, "2025-11-01", None),
]
SEASONS = [("SUMMER", 5, 1, 10, 31), ("WINTER", 11, 1, 4, 30)]
# (plan, season, daytype, hour_start, hour_end_exclusive, period)
TOU_WINDOWS = [
    ("TOU", "WINTER", "WEEKDAY", 0, 7, "OFF"), ("TOU", "WINTER", "WEEKDAY", 7, 11, "ON"),
    ("TOU", "WINTER", "WEEKDAY", 11, 17, "MID"), ("TOU", "WINTER", "WEEKDAY", 17, 19, "ON"),
    ("TOU", "WINTER", "WEEKDAY", 19, 24, "OFF"), ("TOU", "WINTER", "WEEKEND_HOLIDAY", 0, 24, "OFF"),
    ("TOU", "SUMMER", "WEEKDAY", 0, 7, "OFF"), ("TOU", "SUMMER", "WEEKDAY", 7, 11, "MID"),
    ("TOU", "SUMMER", "WEEKDAY", 11, 17, "ON"), ("TOU", "SUMMER", "WEEKDAY", 17, 19, "MID"),
    ("TOU", "SUMMER", "WEEKDAY", 19, 24, "OFF"), ("TOU", "SUMMER", "WEEKEND_HOLIDAY", 0, 24, "OFF"),
    ("ULO", "WINTER", "WEEKDAY", 0, 7, "ULO"), ("ULO", "WINTER", "WEEKDAY", 7, 16, "MID"),
    ("ULO", "WINTER", "WEEKDAY", 16, 21, "ON"), ("ULO", "WINTER", "WEEKDAY", 21, 23, "MID"),
    ("ULO", "WINTER", "WEEKDAY", 23, 24, "ULO"), ("ULO", "WINTER", "WEEKEND_HOLIDAY", 0, 7, "ULO"),
    ("ULO", "WINTER", "WEEKEND_HOLIDAY", 7, 23, "OFF"), ("ULO", "WINTER", "WEEKEND_HOLIDAY", 23, 24, "ULO"),
    ("ULO", "SUMMER", "WEEKDAY", 0, 7, "ULO"), ("ULO", "SUMMER", "WEEKDAY", 7, 16, "MID"),
    ("ULO", "SUMMER", "WEEKDAY", 16, 21, "ON"), ("ULO", "SUMMER", "WEEKDAY", 21, 23, "MID"),
    ("ULO", "SUMMER", "WEEKDAY", 23, 24, "ULO"), ("ULO", "SUMMER", "WEEKEND_HOLIDAY", 0, 7, "ULO"),
    ("ULO", "SUMMER", "WEEKEND_HOLIDAY", 7, 23, "OFF"), ("ULO", "SUMMER", "WEEKEND_HOLIDAY", 23, 24, "ULO"),
]

RES_SHAPE = [0.05, 0.0, 0.0, 0.0, 0.0, 0.05, 0.25, 0.55, 0.45, 0.25, 0.2, 0.2,
             0.25, 0.2, 0.2, 0.25, 0.4, 0.75, 0.95, 0.9, 0.8, 0.6, 0.35, 0.15]

# Defect probabilities (multiplied by --defect-scale)
RATES = {
    "north_gap": 0.004,          # per meter-day: 1-4 consecutive snapshots missing
    "north_dup_row": 0.003,      # per meter-day: one snapshot row repeated
    "north_bad_register": 0.002, # per meter-day: one snapshot drops then recovers
    "north_fmt_id": 0.002,       # per row: meter id lower-case with spaces
    "north_estimated": 0.004,    # per row: read_type E (value is still correct)
    "north_malformed": 0.15,     # per file: one row with a corrupt value
    "lake_missing_meter": 0.002, # per meter-day: meter absent from the file
    "lake_negative": 0.002,      # per meter-day: one 15-min value sign-flipped
    "lake_spike": 0.002,         # per meter-day: one 15-min value x40
    "lake_missing_quarter": 0.003,
    "lake_vendor_estimate": 0.01,
    "lake_correction": 0.5,      # per defective file: a fileVersion=2 correction follows
    "ridge_comm_fail": 0.004,    # per meter-day: 1-3 hours status 99 with 0 Wh
    "ridge_estimate": 0.006,     # per meter-day: one hour status 07, value off by up to 10%
    "ridge_blank_line": 0.10,    # per file
    "ridge_trailer_mismatch": 0.02,
    "file_late": 0.03,           # per file: delivered 2-4 days after the data date
    "file_duplicate": 0.02,      # per file: sent again the next day
    "weather_null": 0.01,        # per hour
}
EVENT_RATES_PER_1000 = {         # CIS events per day per 1,000 meters
    "contact_correction": 2.0,
    "rate_switch": 1.0,
    "move_out_in": 0.6,
    "meter_swap": 0.5,
    "segment_change": 0.15,
    "billing_cycle_change": 0.1,
}


class Simulator:
    def __init__(self, args):
        self.args = args
        self.rng = random.Random(args.seed)
        self.start = args.start
        self.end = args.start + timedelta(days=args.days - 1)
        self.out = Path(args.out).resolve()
        self.holidays = ontario_holidays(range(self.start.year - 1, self.end.year + 2))
        self.scale = args.defect_scale

        # CIS state
        self.customers, self.premises, self.accounts = {}, {}, {}
        self.meters, self.installs, self.arps = {}, {}, {}
        self.next_id = defaultdict(lambda: 1)
        self.premise_profile = {}
        self.pending_profile = {}          # premise_id -> (effective date, profile)
        self.ev_nights = {}
        self.changes = defaultdict(list)   # change date -> SQL statements
        self.cis_events = []

        # Output bookkeeping
        self.truth = defaultdict(lambda: [0.0, 0])  # (meter_no, local_date) -> [kwh, hours]
        self.defects = []
        self.file_events = []
        self.seq = Counter()
        self.ridge_buf = defaultdict(lambda: defaultdict(list))
        self.counts = Counter()
        self.rogue = {"number": "LAB00001", "reg": 125.0}

    # ---------------------------------------------------------------- utils
    def p(self, key):
        return min(1.0, RATES[key] * self.scale)

    def new_id(self, kind):
        v = self.next_id[kind]
        self.next_id[kind] += 1
        return v

    def rand_past(self, years_back_min, years_back_max):
        days = self.rng.randint(int(365 * years_back_min), int(365 * years_back_max))
        d = datetime(self.start.year, self.start.month, self.start.day) - timedelta(days=days)
        return d + timedelta(hours=self.rng.randint(8, 17), minutes=self.rng.randint(0, 59))

    def person(self):
        fn = self.rng.choice(FIRST_NAMES)
        ln = self.rng.choice(LAST_NAMES)
        num = self.rng.randint(10, 999)
        email = f"{fn}.{ln}{num}@example.com".lower().replace("'", "")
        phone = f"{self.rng.choice(['905', '289', '416', '647', '705'])}-555-{self.rng.randint(100, 199):04d}"
        return fn, ln, email, phone

    # ------------------------------------------------------- population
    def build_population(self):
        n = self.args.meters
        rng = self.rng
        for _ in range(n):
            pid = self.new_id("premise")
            ptype = rng.choices(["House", "Townhouse", "Apartment", "Retail", "Office"],
                                [55, 18, 17, 6, 4])[0]
            created = self.rand_past(6, 25)
            self.premises[pid] = {
                "id": pid,
                "street": f"{rng.randint(1, 9999)} {rng.choice(STREETS)} {rng.choice(STREET_TYPES)}",
                "city": rng.choice(CITIES), "prov": "ON",
                "postal": f"{rng.choice(FSA)} {rng.randint(1, 9)}{rng.choice('ABCEGHJKLMNPRSTVWXYZ')}{rng.randint(1, 9)}",
                "type": ptype, "created": created, "modified": created,
            }
            self.premise_profile[pid] = self.make_profile(ptype)

            segment = "SMB" if ptype in ("Retail", "Office") else "RES"
            cid = self.new_id("customer")
            fn, ln, email, phone = self.person()
            ccreated = self.rand_past(0.3, 10)
            self.customers[cid] = {"id": cid, "number": f"C{cid:07d}", "first": fn, "last": ln,
                                   "email": email, "phone": phone, "segment": segment,
                                   "created": ccreated, "modified": ccreated}

            aid = self.new_id("account")
            self.accounts[aid] = {"id": aid, "number": f"{1000000000 + aid}", "customer": cid,
                                  "premise": pid, "cycle": rng.choice(["BC01", "BC02", "BC03", "BC04"]),
                                  "status": "Active", "start": ccreated.date(), "end": None,
                                  "created": ccreated, "modified": ccreated}

            prof = self.premise_profile[pid]
            if segment == "SMB":
                plan = rng.choices(["TOU", "TIER"], [80, 20])[0]
            elif prof.get("ev"):
                plan = rng.choices(["ULO", "TOU", "TIER"], [50, 40, 10])[0]
            else:
                plan = rng.choices(["TOU", "TIER", "ULO"], [72, 20, 8])[0]
            arp_id = self.new_id("arp")
            eff = max(ccreated.date(), date(2020, 11, 1))
            self.arps[arp_id] = {"id": arp_id, "account": aid, "plan": plan, "from": eff, "to": None,
                                 "created": ccreated, "modified": ccreated}

            headend = rng.choices(["NORTH", "LAKE", "RIDGE"], [45, 30, 25])[0]
            self.add_meter(pid, headend, installed_local=self.rand_past(0.5, 9), segment=segment,
                           seed_row=True)

    def make_profile(self, ptype):
        rng = self.rng
        if ptype in ("Retail", "Office"):
            return {"smb": True, "kind": ptype,
                    "open": (0, 1, 2, 3, 4, 5) if ptype == "Retail" else (0, 1, 2, 3, 4),
                    "from": 10 if ptype == "Retail" else 8, "to": 20 if ptype == "Retail" else 18,
                    "load_open": rng.uniform(2.0, 6.5), "load_closed": rng.uniform(0.4, 1.2),
                    "heat_coef": rng.uniform(0.05, 0.25) if rng.random() < 0.5 else 0.0}
        heat_p = 0.6 if ptype == "Apartment" else 0.3
        return {"smb": False, "base": rng.uniform(0.15, 0.45), "scale": rng.uniform(0.6, 1.5),
                "heat": rng.random() < heat_p, "heat_coef": rng.uniform(0.06, 0.15),
                "ev": rng.random() < 0.08, "ev_kw": rng.choice([3.3, 6.6, 7.2])}

    def add_meter(self, premise_id, headend, installed_local, segment, seed_row, recorded_on=None):
        rng = self.rng
        mid = self.new_id("meter")
        manu, model = rng.choice(MANUFACTURERS[headend])
        mult = 10 if (headend == "NORTH" and segment == "SMB" and rng.random() < 0.3) else 1
        number = f"M{10000000 + mid}"
        created = installed_local if seed_row else datetime.combine(recorded_on, datetime.min.time()) + timedelta(
            hours=rng.randint(9, 16), minutes=rng.randint(0, 59))
        self.meters[mid] = {"id": mid, "number": number, "headend": headend, "manu": manu,
                            "model": model, "mult": mult, "created": created, "modified": created,
                            "reg": round(rng.uniform(1000, 60000), 3) if seed_row else round(rng.uniform(0, 50), 3)}
        iid = self.new_id("install")
        self.installs[iid] = {"id": iid, "meter": mid, "premise": premise_id,
                              "installed_local": installed_local,
                              "installed_utc": local_to_utc(installed_local),
                              "removed_local": None, "removed_utc": None,
                              "initial_reg": (None if headend != "NORTH" else
                                              (round(rng.uniform(0, 500), 3) if seed_row else self.meters[mid]["reg"])),
                              "created": created, "modified": created}
        return mid, iid

    # ------------------------------------------------------- CIS seed
    def write_seed(self):
        d = self.out / "cis_seed"
        d.mkdir(parents=True, exist_ok=True)

        def w(name, header, rows):
            with open(d / name, "w", encoding="utf-8", newline="\n") as f:
                f.write(",".join(header) + "\n")
                for r in rows:
                    f.write(",".join(csv_field(v) for v in r) + "\n")

        def dt(x):
            return sql_dt(x) if isinstance(x, datetime) else (x.isoformat() if x else None)

        w("rate_plan.csv", ["RatePlanCode", "RatePlanName", "Description"], RATE_PLANS)
        w("season.csv", ["SeasonCode", "StartMonth", "StartDay", "EndMonth", "EndDay"], SEASONS)
        hol = sorted(self.holidays.items())
        w("holiday.csv", ["HolidayDate", "HolidayName"], [(k.isoformat(), v) for k, v in hol])
        w("rate_price.csv", ["RatePriceId", "RatePlanCode", "PeriodCode", "CentsPerKwh", "TierLimitKwh",
                             "EffectiveFrom", "EffectiveTo"],
          [(i + 1, *r) for i, r in enumerate(RATE_PRICES)])
        w("tou_window.csv", ["TouWindowId", "RatePlanCode", "SeasonCode", "DayType", "HourStart", "HourEnd",
                             "PeriodCode"], [(i + 1, *r) for i, r in enumerate(TOU_WINDOWS)])
        w("customer.csv", ["CustomerId", "CustomerNumber", "FirstName", "LastName", "Email", "Phone",
                           "CustomerSegment", "CreatedAt", "ModifiedAt"],
          [(c["id"], c["number"], c["first"], c["last"], c["email"], c["phone"], c["segment"],
            dt(c["created"]), dt(c["modified"])) for c in self.customers.values()])
        w("premise.csv", ["PremiseId", "StreetAddress", "City", "Province", "PostalCode", "PremiseType",
                          "CreatedAt", "ModifiedAt"],
          [(p["id"], p["street"], p["city"], p["prov"], p["postal"], p["type"], dt(p["created"]),
            dt(p["modified"])) for p in self.premises.values()])
        w("service_account.csv", ["AccountId", "AccountNumber", "CustomerId", "PremiseId", "BillingCycle",
                                  "AccountStatus", "StartDate", "EndDate", "CreatedAt", "ModifiedAt"],
          [(a["id"], a["number"], a["customer"], a["premise"], a["cycle"], a["status"], dt(a["start"]),
            dt(a["end"]), dt(a["created"]), dt(a["modified"])) for a in self.accounts.values()])
        w("meter.csv", ["MeterId", "MeterNumber", "HeadEndCode", "Manufacturer", "Model", "Multiplier",
                        "CreatedAt", "ModifiedAt"],
          [(m["id"], m["number"], m["headend"], m["manu"], m["model"], m["mult"], dt(m["created"]),
            dt(m["modified"])) for m in self.meters.values()])
        w("meter_install.csv", ["MeterInstallId", "MeterId", "PremiseId", "InstalledAt", "RemovedAt",
                                "InitialRegisterKwh", "CreatedAt", "ModifiedAt"],
          [(i["id"], i["meter"], i["premise"], dt(i["installed_local"]), dt(i["removed_local"]),
            None if i["initial_reg"] is None else f"{i['initial_reg']:.3f}", dt(i["created"]),
            dt(i["modified"])) for i in self.installs.values()])
        w("account_rate_plan.csv", ["AccountRatePlanId", "AccountId", "RatePlanCode", "EffectiveFrom",
                                    "EffectiveTo", "CreatedAt", "ModifiedAt"],
          [(r["id"], r["account"], r["plan"], dt(r["from"]), dt(r["to"]), dt(r["created"]),
            dt(r["modified"])) for r in self.arps.values()])

        # Load script with this machine's absolute paths
        order = ["rate_plan", "season", "holiday", "rate_price", "tou_window", "customer", "premise",
                 "service_account", "meter", "meter_install", "account_rate_plan"]
        table = {"rate_plan": "RatePlan", "season": "Season", "holiday": "Holiday",
                 "rate_price": "RatePrice", "tou_window": "TouWindow", "customer": "Customer",
                 "premise": "Premise", "service_account": "ServiceAccount", "meter": "Meter",
                 "meter_install": "MeterInstall", "account_rate_plan": "AccountRatePlan"}
        lines = [
            "-- Generated by generate_sources.py. Loads the CIS seed into TVH_CIS.",
            "-- Run AFTER sql/source/01_create_TVH_CIS.sql. The SQL Server service account",
            "-- must be able to read the folder below.",
            "USE TVH_CIS;", "SET NOCOUNT ON;", "SET XACT_ABORT ON;", "",
            "IF EXISTS (SELECT 1 FROM cis.Customer)",
            "    THROW 50000, 'TVH_CIS already has data. Re-run 01_create_TVH_CIS.sql to start clean.', 1;",
            "", "BEGIN TRANSACTION;",
        ]
        for name in order:
            path = str(d / f"{name}.csv")
            lines.append(
                f"BULK INSERT cis.{table[name]} FROM '{path}'\n"
                f"    WITH (FORMAT = 'CSV', FIRSTROW = 2, FIELDTERMINATOR = ',', ROWTERMINATOR = '0x0a',\n"
                f"          CODEPAGE = '65001', KEEPNULLS, CHECK_CONSTRAINTS, TABLOCK);")
        seed_date = self.start - timedelta(days=1)
        lines += [
            f"INSERT INTO cis.SimulatorLog (ChangeDate, Note) VALUES ('{seed_date.isoformat()}', N'Seed snapshot as of {self.start.isoformat()} 00:00');",
            "COMMIT;", "",
            "SELECT 'Customer' AS TableName, COUNT(*) AS RowsLoaded FROM cis.Customer",
            "UNION ALL SELECT 'ServiceAccount', COUNT(*) FROM cis.ServiceAccount",
            "UNION ALL SELECT 'Meter', COUNT(*) FROM cis.Meter",
            "UNION ALL SELECT 'MeterInstall', COUNT(*) FROM cis.MeterInstall",
            "UNION ALL SELECT 'AccountRatePlan', COUNT(*) FROM cis.AccountRatePlan;",
        ]
        (d / "load_seed.sql").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ------------------------------------------------------- CIS events
    def active_accounts(self, on_day):
        return [a for a in self.accounts.values()
                if a["status"] == "Active" and a["start"] <= on_day and a["end"] is None]

    def current_install_for_premise(self, pid):
        for i in self.installs.values():
            if i["premise"] == pid and i["removed_utc"] is None:
                return i
        return None

    def gen_events(self, day):
        rng = self.rng
        factor = self.args.meters / 1000.0
        touched = set()

        def count(kind):
            lam = EVENT_RATES_PER_1000[kind] * factor
            # Poisson draw (Knuth), fine for small lambda
            L, k, p = math.exp(-lam), 0, 1.0
            while True:
                p *= rng.random()
                if p <= L:
                    return k
                k += 1

        def at(hmin=9, hmax=16):
            return datetime(day.year, day.month, day.day, rng.randint(hmin, hmax), rng.randint(0, 59),
                            rng.randint(0, 59))

        accounts = self.active_accounts(day)
        rng.shuffle(accounts)

        def take():
            while accounts:
                a = accounts.pop()
                if a["id"] not in touched:
                    touched.add(a["id"])
                    return a
            return None

        sql = self.changes

        for _ in range(count("contact_correction")):
            a = take()
            if not a:
                break
            c = self.customers[a["customer"]]
            ts = at()
            what = rng.choice(["email", "phone", "last"])
            if what == "email":
                c["email"] = c["email"].replace("@example.com", f".{rng.randint(1, 99)}@example.com")
                sql[day].append(f"UPDATE cis.Customer SET Email = {sql_str(c['email'])}, ModifiedAt = '{sql_dt(ts)}' WHERE CustomerId = {c['id']};")
            elif what == "phone":
                c["phone"] = f"{rng.choice(['905', '289', '416', '647'])}-555-{rng.randint(100, 199):04d}"
                sql[day].append(f"UPDATE cis.Customer SET Phone = {sql_str(c['phone'])}, ModifiedAt = '{sql_dt(ts)}' WHERE CustomerId = {c['id']};")
            else:
                c["last"] = rng.choice(LAST_NAMES)
                sql[day].append(f"UPDATE cis.Customer SET LastName = {sql_str(c['last'])}, ModifiedAt = '{sql_dt(ts)}' WHERE CustomerId = {c['id']};")
            c["modified"] = ts
            self.cis_events.append((day, "contact_correction", "Customer", c["id"], what))

        for _ in range(count("rate_switch")):
            a = take()
            if not a:
                break
            cur = next(r for r in self.arps.values() if r["account"] == a["id"] and r["to"] is None)
            new_plan = rng.choice([p for p in ("TOU", "TIER", "ULO") if p != cur["plan"]])
            ts = at()
            cur["to"], cur["modified"] = day, ts
            rid = self.new_id("arp")
            self.arps[rid] = {"id": rid, "account": a["id"], "plan": new_plan, "from": day + timedelta(days=1),
                              "to": None, "created": ts, "modified": ts}
            sql[day].append(f"UPDATE cis.AccountRatePlan SET EffectiveTo = '{day}', ModifiedAt = '{sql_dt(ts)}' WHERE AccountRatePlanId = {cur['id']};")
            sql[day].append(f"INSERT INTO cis.AccountRatePlan (AccountRatePlanId, AccountId, RatePlanCode, EffectiveFrom, EffectiveTo, CreatedAt, ModifiedAt) VALUES ({rid}, {a['id']}, '{new_plan}', '{day + timedelta(days=1)}', NULL, '{sql_dt(ts)}', '{sql_dt(ts)}');")
            self.cis_events.append((day, "rate_switch", "ServiceAccount", a["id"], f"{cur['plan']}->{new_plan} effective {day + timedelta(days=1)}"))

        for _ in range(count("move_out_in")):
            a = take()
            if not a:
                break
            ts = at()
            nxt = day + timedelta(days=1)
            a["status"], a["end"], a["modified"] = "Closed", day, ts
            sql[day].append(f"UPDATE cis.ServiceAccount SET AccountStatus = 'Closed', EndDate = '{day}', ModifiedAt = '{sql_dt(ts)}' WHERE AccountId = {a['id']};")
            cur = next(r for r in self.arps.values() if r["account"] == a["id"] and r["to"] is None)
            cur["to"], cur["modified"] = day, ts
            sql[day].append(f"UPDATE cis.AccountRatePlan SET EffectiveTo = '{day}', ModifiedAt = '{sql_dt(ts)}' WHERE AccountRatePlanId = {cur['id']};")
            old_c = self.customers[a["customer"]]
            cid = self.new_id("customer")
            fn, ln, email, phone = self.person()
            self.customers[cid] = {"id": cid, "number": f"C{cid:07d}", "first": fn, "last": ln, "email": email,
                                   "phone": phone, "segment": old_c["segment"], "created": ts, "modified": ts}
            sql[day].append(f"INSERT INTO cis.Customer (CustomerId, CustomerNumber, FirstName, LastName, Email, Phone, CustomerSegment, CreatedAt, ModifiedAt) VALUES ({cid}, 'C{cid:07d}', {sql_str(fn)}, {sql_str(ln)}, {sql_str(email)}, {sql_str(phone)}, '{old_c['segment']}', '{sql_dt(ts)}', '{sql_dt(ts)}');")
            aid = self.new_id("account")
            self.accounts[aid] = {"id": aid, "number": f"{1000000000 + aid}", "customer": cid, "premise": a["premise"],
                                  "cycle": a["cycle"], "status": "Active", "start": nxt, "end": None,
                                  "created": ts, "modified": ts}
            touched.add(aid)
            sql[day].append(f"INSERT INTO cis.ServiceAccount (AccountId, AccountNumber, CustomerId, PremiseId, BillingCycle, AccountStatus, StartDate, EndDate, CreatedAt, ModifiedAt) VALUES ({aid}, '{1000000000 + aid}', {cid}, {a['premise']}, '{a['cycle']}', 'Active', '{nxt}', NULL, '{sql_dt(ts)}', '{sql_dt(ts)}');")
            plan = rng.choice(["TOU", "TOU", "TIER", "ULO"]) if old_c["segment"] == "RES" else "TOU"
            rid = self.new_id("arp")
            self.arps[rid] = {"id": rid, "account": aid, "plan": plan, "from": nxt, "to": None,
                              "created": ts, "modified": ts}
            sql[day].append(f"INSERT INTO cis.AccountRatePlan (AccountRatePlanId, AccountId, RatePlanCode, EffectiveFrom, EffectiveTo, CreatedAt, ModifiedAt) VALUES ({rid}, {aid}, '{plan}', '{nxt}', NULL, '{sql_dt(ts)}', '{sql_dt(ts)}');")
            ptype = self.premises[a["premise"]]["type"]
            self.pending_profile[a["premise"]] = (nxt, self.make_profile(ptype))
            self.cis_events.append((day, "move_out_in", "ServiceAccount", a["id"], f"closed; new account {aid} for customer {cid} from {nxt}"))

        for _ in range(count("meter_swap")):
            a = take()
            if not a:
                break
            inst = self.current_install_for_premise(a["premise"])
            if not inst:
                continue
            old_m = self.meters[inst["meter"]]
            swap_local = datetime(day.year, day.month, day.day, rng.randint(9, 16))
            swap_utc = local_to_utc(swap_local)
            lag = rng.choices([0, 1, 2], [60, 30, 10])[0]
            rec_day = day + timedelta(days=lag)
            new_headend = old_m["headend"]
            if old_m["headend"] == "RIDGE" and rng.random() < 0.7:
                new_headend = "NORTH"
            seg = self.customers[a["customer"]]["segment"]
            inst["removed_local"], inst["removed_utc"] = swap_local, swap_utc
            mid, iid = self.add_meter(a["premise"], new_headend, installed_local=swap_local, segment=seg,
                                      seed_row=False, recorded_on=rec_day)
            m, i = self.meters[mid], self.installs[iid]
            ts = m["created"]
            inst["modified"] = ts
            if rec_day <= self.end:
                sql[rec_day].append(f"UPDATE cis.MeterInstall SET RemovedAt = '{sql_dt(swap_local)}', ModifiedAt = '{sql_dt(ts)}' WHERE MeterInstallId = {inst['id']};")
                sql[rec_day].append(f"INSERT INTO cis.Meter (MeterId, MeterNumber, HeadEndCode, Manufacturer, Model, Multiplier, CreatedAt, ModifiedAt) VALUES ({mid}, '{m['number']}', '{m['headend']}', {sql_str(m['manu'])}, {sql_str(m['model'])}, {m['mult']}, '{sql_dt(ts)}', '{sql_dt(ts)}');")
                init = "NULL" if i["initial_reg"] is None else f"{i['initial_reg']:.3f}"
                sql[rec_day].append(f"INSERT INTO cis.MeterInstall (MeterInstallId, MeterId, PremiseId, InstalledAt, RemovedAt, InitialRegisterKwh, CreatedAt, ModifiedAt) VALUES ({iid}, {mid}, {a['premise']}, '{sql_dt(swap_local)}', NULL, {init}, '{sql_dt(ts)}', '{sql_dt(ts)}');")
            self.cis_events.append((day, "meter_swap", "Meter", mid,
                                    f"{old_m['number']} ({old_m['headend']}) -> {m['number']} ({new_headend}) at {sql_dt(swap_local)} local; recorded in CIS on {rec_day} (lag {lag} d)"))

        for _ in range(count("segment_change")):
            a = take()
            if not a:
                break
            c = self.customers[a["customer"]]
            if c["segment"] != "RES":
                continue
            ts = at()
            c["segment"], c["modified"] = "SMB", ts
            sql[day].append(f"UPDATE cis.Customer SET CustomerSegment = 'SMB', ModifiedAt = '{sql_dt(ts)}' WHERE CustomerId = {c['id']};")
            self.cis_events.append((day, "segment_change", "Customer", c["id"], "RES->SMB"))

        for _ in range(count("billing_cycle_change")):
            a = take()
            if not a:
                break
            ts = at()
            new_cycle = rng.choice([c for c in ("BC01", "BC02", "BC03", "BC04") if c != a["cycle"]])
            self.cis_events.append((day, "billing_cycle_change", "ServiceAccount", a["id"], f"{a['cycle']}->{new_cycle}"))
            a["cycle"], a["modified"] = new_cycle, ts
            sql[day].append(f"UPDATE cis.ServiceAccount SET BillingCycle = '{new_cycle}', ModifiedAt = '{sql_dt(ts)}' WHERE AccountId = {a['id']};")

    def write_changes(self):
        d = self.out / "cis_changes"
        d.mkdir(parents=True, exist_ok=True)
        day = self.start
        while day <= self.end:
            prev = day - timedelta(days=1)
            stmts = self.changes.get(day, [])
            body = [
                f"-- TVH_CIS changes recorded during {day} (simulated). Generated by generate_sources.py.",
                "-- Safe to re-run: it skips itself if already applied, and refuses to run out of order.",
                "USE TVH_CIS;", "SET NOCOUNT ON;", "SET XACT_ABORT ON;",
                f"IF EXISTS (SELECT 1 FROM cis.SimulatorLog WHERE ChangeDate = '{day}')",
                "BEGIN",
                f"    PRINT 'Changes for {day} already applied - skipped.';",
                "    RETURN;",
                "END;",
                f"IF NOT EXISTS (SELECT 1 FROM cis.SimulatorLog WHERE ChangeDate = '{prev}')",
                f"    THROW 50001, 'Apply the changes for {prev} first.', 1;",
                "BEGIN TRANSACTION;",
            ] + stmts + [
                f"INSERT INTO cis.SimulatorLog (ChangeDate, Note) VALUES ('{day}', N'{len(stmts)} statements');",
                "COMMIT;",
                f"PRINT 'Applied {len(stmts)} CIS changes for {day}.';",
            ]
            (d / f"{day}.sql").write_text("\n".join(body) + "\n", encoding="utf-8")
            self.counts["cis_change_statements"] += len(stmts)
            day += timedelta(days=1)

    # ------------------------------------------------------- weather and usage
    def temperature(self, utc_hour, day_anom):
        local = utc_to_local(utc_hour)
        doy = local.timetuple().tm_yday
        seasonal = 8.5 - 15.5 * math.cos(2 * math.pi * (doy - 25) / 365.0)
        diurnal = 4.0 * math.sin(2 * math.pi * (local.hour - 9) / 24.0)
        return seasonal + diurnal + day_anom + self.rng.gauss(0, 0.6)

    def usage(self, pid, utc_hour, temp):
        rng = self.rng
        prof = self.premise_profile[pid]
        local = utc_to_local(utc_hour)
        lh, ld = local.hour, local.date()
        offday = local.weekday() >= 5 or ld in self.holidays
        if prof["smb"]:
            open_ = (local.weekday() in prof["open"]) and ld not in self.holidays and prof["from"] <= lh < prof["to"]
            v = prof["load_open"] if open_ else prof["load_closed"]
            v += max(0.0, 16.0 - temp) * prof["heat_coef"] * (1.0 if open_ else 0.5)
            return max(0.02, v * rng.uniform(0.9, 1.1))
        v = prof["base"] + prof["scale"] * RES_SHAPE[lh]
        if offday and 9 <= lh <= 16:
            v += 0.15 * prof["scale"]
        if prof["heat"]:
            v += max(0.0, 16.0 - temp) * prof["heat_coef"] * (1.1 if lh in (6, 7, 8, 17, 18, 19, 20) else 0.9)
        if prof["ev"] and (lh >= 23 or lh < 3):
            night = ld if lh >= 23 else ld - timedelta(days=1)
            key = (pid, night)
            if key not in self.ev_nights:
                self.ev_nights[key] = rng.random() < 0.6
            if self.ev_nights[key]:
                v += prof["ev_kw"] * rng.uniform(0.8, 1.0)
        return max(0.02, v * rng.uniform(0.85, 1.15))

    # ------------------------------------------------------- outbox helpers
    def deliver_path(self, delivery, feed, name):
        p = self.out / "outbox" / delivery.isoformat() / feed
        p.mkdir(parents=True, exist_ok=True)
        return p / name

    def next_name(self, feed, data_date, ext):
        key = (feed, data_date)
        self.seq[key] += 1
        return f"{feed.upper()}_{data_date:%Y%m%d}_{self.seq[key]:02d}.{ext}"

    def delivery_for(self, data_date):
        if self.rng.random() < self.p("file_late"):
            delay = self.rng.randint(2, 4)
            return data_date + timedelta(days=delay), f"late by {delay - 1} extra day(s)"
        return data_date + timedelta(days=1), None

    def emit(self, feed, data_date, ext, content, regenerate=None, allow_dup=True, delivery=None,
             event="normal", detail=None):
        name = self.next_name(feed, data_date, ext)
        if delivery is None:
            delivery, late = self.delivery_for(data_date)
            if late:
                event, detail = "late", late
        self.deliver_path(delivery, feed, name).write_bytes(content)
        self.file_events.append((feed, name, data_date, delivery, event, detail or ""))
        self.counts[f"{feed}_files"] += 1
        if allow_dup and self.rng.random() < self.p("file_duplicate"):
            identical = regenerate is None or self.rng.random() < 0.5
            dup_content = content if identical else regenerate()
            dup_name = self.next_name(feed, data_date, ext)
            dup_delivery = delivery + timedelta(days=1)
            self.deliver_path(dup_delivery, feed, dup_name).write_bytes(dup_content)
            self.file_events.append((feed, dup_name, data_date, dup_delivery,
                                     "duplicate_identical" if identical else "duplicate_regenerated",
                                     f"resend of {name}"))
            self.counts[f"{feed}_files"] += 1
        return name, delivery

    def add_truth(self, meter_no, local_date, kwh):
        if self.start <= local_date <= self.end:
            t = self.truth[(meter_no, local_date)]
            t[0] += kwh
            t[1] += 1

    # ------------------------------------------------------- writers
    def write_north(self, day, rows_by_meter, baseline_only):
        rng = self.rng
        lines_struct = []   # (meter_no, ts, value_str, type)
        for meter_no, snaps in rows_by_meter.items():
            if baseline_only:
                snaps = snaps[-1:]
            snaps = list(snaps)
            if not baseline_only and len(snaps) > 3:
                if rng.random() < self.p("north_gap"):
                    k = rng.randint(1, 4)
                    i0 = rng.randint(0, max(0, len(snaps) - k - 1))
                    for s in snaps[i0:i0 + k]:
                        self.defects.append(("north", None, meter_no, iso_utc(s[0] - timedelta(hours=1)), "GAP",
                                             f"snapshot at {iso_utc(s[0])} missing"))
                    del snaps[i0:i0 + k]
                if rng.random() < self.p("north_bad_register") and len(snaps) > 3:
                    i = rng.randint(1, len(snaps) - 2)
                    ts, val = snaps[i]
                    bad = round(val - rng.uniform(50, 500), 3)
                    snaps[i] = (ts, bad)
                    self.defects.append(("north", None, meter_no, iso_utc(ts - timedelta(hours=1)), "BAD_REGISTER",
                                         f"register {bad:.3f} instead of {val:.3f}"))
                if rng.random() < self.p("north_dup_row"):
                    i = rng.randint(0, len(snaps) - 1)
                    snaps.insert(i, snaps[i])
                    self.defects.append(("north", None, meter_no, iso_utc(snaps[i][0] - timedelta(hours=1)),
                                         "DUP_ROW", "same snapshot twice"))
            for ts, val in snaps:
                rtype = "E" if rng.random() < self.p("north_estimated") else "A"
                mid = meter_no
                if rng.random() < self.p("north_fmt_id"):
                    mid = f"  {meter_no.lower()} "
                    self.defects.append(("north", None, meter_no, iso_utc(ts - timedelta(hours=1)), "FMT_METER_ID",
                                         "lower case with spaces"))
                lines_struct.append([mid, iso_utc(ts), f"{val:.3f}", rtype])
        if not baseline_only and lines_struct and rng.random() < self.p("north_malformed"):
            i = rng.randint(0, len(lines_struct) - 1)
            if rng.random() < 0.5:
                lines_struct[i][2] = "ERR"
                what = "register is ERR"
            else:
                lines_struct[i][1] = lines_struct[i][1][:11] + "25" + lines_struct[i][1][13:]
                what = "hour is 25"
            self.defects.append(("north", None, lines_struct[i][0].strip().upper(), lines_struct[i][1],
                                 "MALFORMED_ROW", what))

        def render(rows):
            return ("meter_id,read_time_utc,register_kwh,read_type\n" +
                    "".join(",".join(r) + "\n" for r in rows)).encode("utf-8")

        content = render(lines_struct)

        def regenerate():
            shuffled = list(lines_struct)
            random.Random(len(shuffled)).shuffle(shuffled)
            return render(shuffled)

        n_before = len(self.defects)
        name, _ = self.emit("north", day, "csv", content, regenerate=regenerate,
                            allow_dup=not baseline_only,
                            delivery=(self.start if baseline_only else None),
                            event="baseline" if baseline_only else "normal",
                            detail="opening register snapshot at go-live" if baseline_only else None)
        self.counts["north_rows"] += len(lines_struct)
        self._stamp_defects(n_before, name)

    def _stamp_defects(self, from_index, file_name):
        for i in range(from_index, len(self.defects)):
            if self.defects[i][1] is None:
                d = list(self.defects[i])
                d[1] = file_name
                self.defects[i] = tuple(d)

    def write_lake(self, day, quarters_by_meter):
        rng = self.rng
        n_before = len(self.defects)
        written = {}
        correctable = set()
        for meter_no, qs in quarters_by_meter.items():
            qs = [list(q) + ["A"] for q in qs]  # [utc, value, q]
            if rng.random() < self.p("lake_missing_meter"):
                self.defects.append(("lake", None, meter_no, iso_utc(qs[0][0]), "MISSING_METER_DAY",
                                     f"{len(qs)} readings absent"))
                correctable.add(meter_no)
                continue
            if rng.random() < self.p("lake_negative"):
                i = rng.randint(0, len(qs) - 1)
                qs[i][1] = -qs[i][1] if qs[i][1] > 0 else -0.111
                self.defects.append(("lake", None, meter_no, iso_utc(qs[i][0]), "NEGATIVE", f"value {qs[i][1]:.3f}"))
                correctable.add(meter_no)
            if rng.random() < self.p("lake_spike"):
                i = rng.randint(0, len(qs) - 1)
                qs[i][1] = round(qs[i][1] * 40 + 5, 3)
                self.defects.append(("lake", None, meter_no, iso_utc(qs[i][0]), "SPIKE", f"value {qs[i][1]:.3f}"))
                correctable.add(meter_no)
            if rng.random() < self.p("lake_missing_quarter"):
                i = rng.randint(0, len(qs) - 1)
                self.defects.append(("lake", None, meter_no, iso_utc(qs[i][0]), "MISSING_QUARTER", ""))
                del qs[i]
            if rng.random() < self.p("lake_vendor_estimate") and len(qs) >= 8:
                i = rng.randint(0, len(qs) // 4 - 1) * 4
                block = qs[i:i + 4]
                if len(block) == 4 and block[0][0].minute == 0 and all(b[0] == block[0][0] + timedelta(minutes=15 * j) for j, b in enumerate(block)):
                    total = sum(b[1] for b in block)
                    vals = [round(total / 4, 3)] * 3
                    vals.append(round(total - sum(vals), 3))
                    for b, v in zip(block, vals):
                        b[1], b[2] = v, "E"
                    self.defects.append(("lake", None, meter_no, iso_utc(block[0][0]), "VENDOR_ESTIMATE",
                                         "hour flagged q=E, total preserved"))
            written[meter_no] = qs

        def render(meters, version, gen_utc):
            out = ['<?xml version="1.0" encoding="UTF-8"?>',
                   f'<IntervalData headEnd="LAKE" dataDate="{day.isoformat()}" fileVersion="{version}" '
                   f'generatedAt="{iso_local(gen_utc)}">']
            for mno, qs in meters.items():
                out.append(f'  <Meter id="{mno}" uom="kWh">')
                for utc, val, q in qs:
                    out.append(f'    <Reading start="{iso_local(utc)}" minutes="15" value="{val:.3f}" q="{q}"/>')
                out.append("  </Meter>")
            out.append("</IntervalData>")
            return ("\n".join(out) + "\n").encode("utf-8")

        gen = local_midnight_utc(day + timedelta(days=1)) + timedelta(hours=2, minutes=rng.randint(0, 59),
                                                                         seconds=rng.randint(0, 59))
        content = render(written, 1, gen)
        name, delivery = self.emit("lake", day, "xml", content,
                                   regenerate=lambda: render(written, 1, gen + timedelta(minutes=37)))
        self.counts["lake_rows"] += sum(len(v) for v in written.values())
        self._stamp_defects(n_before, name)

        if correctable and rng.random() < self.p("lake_correction"):
            fixed = {m: [list(q) + ["A"] for q in quarters_by_meter[m]] for m in sorted(correctable)}
            content2 = render(fixed, 2, gen + timedelta(days=1))
            name2, _ = self.emit("lake", day, "xml", content2, allow_dup=False,
                                 delivery=delivery + timedelta(days=1), event="correction",
                                 detail=f"fileVersion 2 replaces {len(fixed)} meter(s) from {name}")
            self.counts["lake_rows"] += sum(len(v) for v in fixed.values())
            for m in fixed:
                self.defects.append(("lake", name2, m, "", "CORRECTED_IN_V2", f"replaces values sent in {name}"))

    def flush_ridge(self, upto_utc, final=False):
        for est_date in sorted(list(self.ridge_buf.keys())):
            est_end = datetime(est_date.year, est_date.month, est_date.day) + timedelta(days=1, hours=5)
            if est_end > upto_utc:
                continue
            data = self.ridge_buf.pop(est_date)
            if est_date < self.start - timedelta(days=1) or est_date > self.end:
                continue
            self.write_ridge(est_date, data)

    def write_ridge(self, est_date, data):
        rng = self.rng
        n_before = len(self.defects)
        details = []
        for meter_no in sorted(data):
            hours = [[he, wh, "00", utc] for he, wh, utc in sorted(data[meter_no])]
            if rng.random() < self.p("ridge_comm_fail") and len(hours) > 4:
                k = rng.randint(1, 3)
                i0 = rng.randint(0, len(hours) - k)
                for h in hours[i0:i0 + k]:
                    self.defects.append(("ridge", None, meter_no, iso_utc(h[3]), "COMM_FAIL_ZERO",
                                         f"status 99, true value {h[1]} Wh"))
                    h[1], h[2] = 0, "99"
            if rng.random() < self.p("ridge_estimate"):
                h = rng.choice(hours)
                est = max(0, int(round(h[1] * rng.uniform(0.9, 1.1))))
                self.defects.append(("ridge", None, meter_no, iso_utc(h[3]), "HEADEND_ESTIMATE",
                                     f"status 07, sent {est} Wh, true {h[1]} Wh"))
                h[1], h[2] = est, "07"
            pad = meter_no
            if rng.random() < self.p("north_fmt_id") * 5:
                pad = meter_no.lower()
                self.defects.append(("ridge", None, meter_no, iso_utc(hours[0][3]), "FMT_METER_ID",
                                     "lower case for the whole meter-day"))
            for he, wh, status, _ in hours:
                details.append(f"D {pad:<10} {est_date:%Y%m%d} {he:02d} {wh:07d} {status}".ljust(36))

        def render(det, created, count=None, total=None):
            count = len(det) if count is None else count
            total = sum(int(x[25:32]) for x in det) if total is None else total
            lines = [f"H {'RIDGE':<10} {est_date:%Y%m%d} {created:%Y%m%d%H%M%S}"]
            body = list(det)
            if rng.random() < self.p("ridge_blank_line") and body:
                body.insert(rng.randint(0, len(body)), "")
            lines += body
            lines.append(f"T {count:07d} {total:010d}".ljust(36))
            return ("\r\n".join(lines) + "\r\n").encode("ascii")

        created = datetime(est_date.year, est_date.month, est_date.day) + timedelta(days=1, hours=3,
                                                                                    minutes=rng.randint(0, 59))
        full_total = sum(int(x[25:32]) for x in details)
        if details and rng.random() < self.p("ridge_trailer_mismatch"):
            i = rng.randint(0, len(details) - 1)
            dropped = details[i]
            broken = details[:i] + details[i + 1:]
            content = render(broken, created, count=len(details), total=full_total)
            name, delivery = self.emit("ridge", est_date, "txt", content, allow_dup=False,
                                       event="trailer_mismatch", detail="one detail record lost in transmission")
            self.defects.append(("ridge", name, dropped[2:12].strip().upper(), "", "TRAILER_MISMATCH",
                                 "trailer counts a record that is not in the file"))
            self._stamp_defects(n_before, name)
            name2, _ = self.emit("ridge", est_date, "txt", render(details, created + timedelta(days=1)),
                                 allow_dup=False, delivery=delivery + timedelta(days=1), event="resubmission",
                                 detail=f"complete file replacing {name}")
            self.counts["ridge_rows"] += len(broken) + len(details)
            return
        content = render(details, created)
        name, _ = self.emit("ridge", est_date, "txt", content,
                            regenerate=lambda: render(details, created + timedelta(minutes=11)))
        self.counts["ridge_rows"] += len(details)
        self._stamp_defects(n_before, name)

    def write_weather(self, day, hourly):
        items = []
        for utc, temp in hourly:
            t = "null" if self.rng.random() < self.p("weather_null") else f"{temp:.1f}"
            hum = self.rng.randint(45, 95)
            items.append(f'    {{"hour_start_local": "{iso_local(utc)}", "temp_c": {t}, "humidity_pct": {hum}}}')
        doc = ('{\n  "station": "TVH-CENTRAL",\n'
               f'  "date": "{day.isoformat()}",\n  "timezone": "America/Toronto",\n'
               '  "hourly": [\n' + ",\n".join(items) + "\n  ]\n}\n")
        self.emit("weather", day, "json", doc.encode("utf-8"), allow_dup=False)
        self.counts["weather_rows"] += len(items)

    # ------------------------------------------------------- main loop
    def run(self):
        if self.out.exists():
            shutil.rmtree(self.out)
        self.out.mkdir(parents=True)
        print(f"Building population of {self.args.meters} meters (seed {self.args.seed}) ...")
        self.build_population()
        self.write_seed()

        day = self.start - timedelta(days=1)
        last = self.end + timedelta(days=1)
        day_anom = 0.0
        total_days = (last - day).days + 1
        n = 0
        while day <= last:
            n += 1
            if n % 10 == 0 or n == total_days:
                print(f"  day {n}/{total_days}  {day}")
            # occupant changes that take effect today
            for pid, (eff, prof) in list(self.pending_profile.items()):
                if eff <= day:
                    self.premise_profile[pid] = prof
                    del self.pending_profile[pid]
            if self.start <= day <= self.end:
                self.gen_events(day)

            h0, h1 = local_midnight_utc(day), local_midnight_utc(day + timedelta(days=1))
            hours = [h0 + timedelta(hours=k) for k in range(int((h1 - h0).total_seconds() // 3600))]
            day_anom = 0.7 * day_anom + self.rng.gauss(0, 2.2)
            temps = [(h, self.temperature(h, day_anom)) for h in hours]

            north_rows, lake_rows = defaultdict(list), defaultdict(list)
            live = [i for i in self.installs.values()
                    if i["installed_utc"] < h1 and (i["removed_utc"] is None or i["removed_utc"] > h0)]
            for inst in live:
                m = self.meters[inst["meter"]]
                for h, temp in temps:
                    if not (inst["installed_utc"] <= h and (inst["removed_utc"] is None or h < inst["removed_utc"])):
                        continue
                    kwh = self.usage(inst["premise"], h, temp)
                    if m["headend"] == "NORTH":
                        before = round(m["reg"], 3)
                        m["reg"] = m["reg"] + kwh / m["mult"]
                        after = round(m["reg"], 3)
                        north_rows[m["number"]].append((h + timedelta(hours=1), after))
                        self.add_truth(m["number"], day, (after - before) * m["mult"])
                    elif m["headend"] == "LAKE":
                        w = [self.rng.uniform(0.8, 1.2) for _ in range(4)]
                        sw = sum(w)
                        hour_total = 0.0
                        for j in range(4):
                            v = round(kwh * w[j] / sw, 3)
                            lake_rows[m["number"]].append((h + timedelta(minutes=15 * j), v))
                            hour_total += v
                        self.add_truth(m["number"], day, hour_total)
                    else:
                        wh = int(round(kwh * 1000))
                        est = h - timedelta(hours=5)
                        self.ridge_buf[est.date()][m["number"]].append((est.hour + 1, wh, h))
                        self.add_truth(m["number"], day, wh / 1000.0)
            # lab test meter that never appears in CIS
            if self.start - timedelta(days=1) <= day <= self.end:
                for h, _ in temps:
                    self.rogue["reg"] += 0.01
                    north_rows[self.rogue["number"]].append((h + timedelta(hours=1), round(self.rogue["reg"], 3)))

            if day == self.start - timedelta(days=1):
                self.write_north(day, north_rows, baseline_only=True)
            elif self.start <= day <= self.end:
                self.write_north(day, north_rows, baseline_only=False)
                self.write_lake(day, lake_rows)
                self.write_weather(day, temps)
            self.flush_ridge(h1)
            day += timedelta(days=1)

        self.write_changes()
        self.write_answer_key()

    def write_answer_key(self):
        d = self.out / "answer_key"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "true_daily_kwh.csv", "w", encoding="utf-8", newline="\n") as f:
            f.write("meter_number,local_date,intervals,true_kwh\n")
            for (mno, ld), (kwh, hrs) in sorted(self.truth.items()):
                f.write(f"{mno},{ld},{hrs},{kwh:.3f}\n")
        with open(d / "defects.csv", "w", encoding="utf-8", newline="\n") as f:
            f.write("feed,file_name,meter_number,interval_start_utc,defect_type,detail\n")
            for r in self.defects:
                f.write(",".join("" if v is None else str(v).replace(",", ";") for v in r) + "\n")
        with open(d / "file_events.csv", "w", encoding="utf-8", newline="\n") as f:
            f.write("feed,file_name,data_date,delivery_date,event,detail\n")
            for r in self.file_events:
                f.write(",".join(str(v).replace(",", ";") for v in r) + "\n")
        with open(d / "cis_events.csv", "w", encoding="utf-8", newline="\n") as f:
            f.write("event_date,event_type,entity,entity_id,detail\n")
            for r in self.cis_events:
                f.write(",".join(str(v).replace(",", ";") for v in r) + "\n")
        dst = [k for k in (self.start + timedelta(days=i) for i in range(self.args.days))
               if (local_midnight_utc(k + timedelta(days=1)) - local_midnight_utc(k)).total_seconds() != 86400]
        manifest = {
            "company": "Trillium Valley Hydro (fictional)",
            "seed": self.args.seed, "start": self.start.isoformat(), "end": self.end.isoformat(),
            "days": self.args.days, "meters_at_start": self.args.meters,
            "meters_total_including_swaps": len(self.meters), "defect_scale": self.scale,
            "dst_days": [x.isoformat() for x in dst],
            "holidays_in_window": {k.isoformat(): v for k, v in sorted(self.holidays.items())
                                   if self.start <= k <= self.end},
            "counts": dict(sorted(self.counts.items())),
            "defects_by_type": dict(sorted(Counter(r[4] for r in self.defects).items())),
            "file_events_by_type": dict(sorted(Counter(r[4] for r in self.file_events).items())),
            "cis_events_by_type": dict(sorted(Counter(r[1] for r in self.cis_events).items())),
            "first_run_date": (self.start + timedelta(days=1)).isoformat(),
            "last_delivery_date": max(r[3] for r in self.file_events).isoformat(),
        }
        (self.out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(json.dumps(manifest, indent=2))


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Generate Trillium Valley Hydro source data.")
    ap.add_argument("--seed", type=int, required=True, help="your own random seed (makes your data unique)")
    ap.add_argument("--start", type=date.fromisoformat, default=date(2025, 10, 15),
                    help="first business date, default 2025-10-15")
    ap.add_argument("--days", type=int, default=152, help="number of business days, default 152 (to 2026-03-15)")
    ap.add_argument("--meters", type=int, default=800, help="meters at start, default 800")
    ap.add_argument("--defect-scale", type=float, default=1.0, help="multiply all defect rates, default 1.0")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "output"), help="output folder")
    a = ap.parse_args(argv)
    if a.days < 2 or a.meters < 10:
        ap.error("use at least 2 days and 10 meters")
    return a


if __name__ == "__main__":
    Simulator(parse_args()).run()
