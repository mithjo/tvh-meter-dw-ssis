# 00 - Development environment setup

This page records the tools, versions, folders and dataset used to build this project,
so anyone can rebuild the same environment and get the same data.

Last updated: 2026-10-25

## Tools installed

| Tool | Version | Notes |
| --- | --- | --- |
| Windows | Windows 11 Home, build 26200 | `@@VERSION` reports it as "Windows 10 Home 10.0 (Build 26200)"; SQL Server labels Windows 11 as 10.0 |
| SQL Server | 2022 Developer Edition (64-bit), RTM 16.0.1000.6 | No cumulative update installed yet (TODO: install the latest CU) |
| SQL Server Management Studio | 21.6.17 | |
| Visual Studio Community 2026 | 18.11.2 (October 2026 Feature Update, Stable channel) | From Help → About Microsoft Visual Studio |
| SQL Server Integration Services Projects 2022 extension | 2.1.2 | From Extensions → Manage Extensions → Installed. Version 2.2 is available (TODO: update) |
| Git | 2.51.0.windows.1 | |
| Python | 3.11.3 | Run with the `py` launcher; `python.exe` is not on PATH |

Full `SELECT @@VERSION` output:

```
Microsoft SQL Server 2022 (RTM) - 16.0.1000.6 (X64)
Oct  8 2022 05:58:25
Copyright (C) 2022 Microsoft Corporation
Developer Edition (64-bit) on Windows 10 Home 10.0 <X64> (Build 26200: ) (Hypervisor)
```

## SQL Server instance

- **Server:** default instance `MSSQLSERVER` on my laptop. Connect with `localhost`.
- **Features installed:** Database Engine Services, Integration Services.
- **Authentication:** Windows authentication. My Windows user is a sysadmin.

Services, from `SELECT servicename, service_account, startup_type_desc, status_desc FROM sys.dm_server_services;`

| Service | Service account | Startup type | Status | Notes |
| --- | --- | --- | --- | --- |
| SQL Server (MSSQLSERVER) | `NT Service\MSSQLSERVER` | Automatic | Running | |
| SQL Server Agent (MSSQLSERVER) | `NT Service\SQLSERVERAGENT` | Manual | Stopped | Set to Automatic and start it before scheduling jobs (Session 21) |

## Folders and permissions

| Folder | Purpose | Permission given |
| --- | --- | --- |
| `C:\Projects\tvh-meter-dw-ssis` | Git repo: code and docs | My Windows user (owner) |
| `C:\Projects\tvh-meter-dw-ssis\simulator\output` | Generated source data, never committed to Git | None needed: I load it with my own Windows login |
| `C:\TVH\data\inbound` | Vendor files arrive here each night | None yet. The SQL Agent account needs access when jobs run (Session 22) |
| `C:\TVH\data\archive` | Files that loaded successfully | None yet |
| `C:\TVH\data\rejected` | Files refused as a whole | None yet |

The data folders sit outside the repo on purpose: code goes to Git, data files do not.

## My dataset

- **Seed:** 3455
- **Command:** `py generate_sources.py --seed 3455 --meters 120 --days 30` (run inside `simulator\`)
- **Business dates:** 2025-10-15 to 2025-11-13 (30 days)
- **Daylight-saving day in the window:** 2025-11-02 (25 hours)
- **Simulated nights to run:** first run date 2025-10-16, last delivery 2025-11-16

| Item | Count |
| --- | --- |
| Meters at start | 120 |
| Meters including swaps | 121 |
| North files / rows | 32 / 38,252 |
| Lake files / rows | 34 / 121,223 |
| Ridge files / rows | 32 / 19,344 |
| Weather files / rows | 30 / 721 |
| CIS change statements | 35 |
| Injected defects | 149, across 13 types |

| File event | Count |
| --- | --- |
| Normal | 118 |
| Baseline (go-live register snapshot) | 1 |
| Late | 3 |
| Duplicate, byte-identical | 1 |
| Duplicate, regenerated | 3 |
| Lake version-2 correction | 2 |

| CIS event | Count |
| --- | --- |
| Contact correction | 7 |
| Rate switch | 5 |
| Move-out / move-in | 3 |
| Meter swap | 1 |

**Not covered by this small dataset:** no Ridge trailer mismatch, no customer segment change and no
statutory holiday. These cases need extra hand-made tests later (Sessions 12, 15 and 8).

`TVH_CIS` after loading the seed:

| Table | Rows loaded |
| --- | --- |
| cis.Customer | 120 |
| cis.ServiceAccount | 120 |
| cis.Meter | 120 |
| cis.MeterInstall | 120 |
| cis.AccountRatePlan | 120 |

## Setup steps

1. Installed SQL Server 2022 Developer edition (Custom install: Database Engine Services and
   Integration Services; added my Windows user as administrator).
2. Installed SSMS, Visual Studio Community with the SQL Server Integration Services Projects
   extension, the Microsoft OLE DB Driver for SQL Server, Git and Python.
3. Created the public GitHub repo `tvh-meter-dw-ssis` and cloned it:
   ```
   cd C:\Projects
   git clone https://github.com/mithjo/tvh-meter-dw-ssis.git
   ```
4. Unzipped the starter kit into `C:\Projects\tvh-meter-dw-ssis`.
5. Created the data folders:
   ```
   mkdir C:\TVH\data\inbound C:\TVH\data\archive C:\TVH\data\rejected
   ```
6. Generated the dataset:
   ```
   cd C:\Projects\tvh-meter-dw-ssis\simulator
   py generate_sources.py --seed 3455 --meters 120 --days 30
   ```
   Output: `simulator\output\` with `cis_seed`, `cis_changes`, `outbox`, `answer_key` and `manifest.json`.
7. In SSMS, ran `sql\source\01_create_TVH_CIS.sql`, then `simulator\output\cis_seed\load_seed.sql`.
   Every table loaded 120 rows.

## Problems I hit and how I fixed them

| Problem | Cause | Fix |
| --- | --- | --- |
| `'python' is not recognized as an internal or external command` | Python was installed without being added to PATH | `py --version` showed 3.11.3, so I run scripts with the `py` launcher instead of `python` |

SSMS connected to the instance (TODO: add screenshot):

![SSMS connected](images/setup-ssms.png)
