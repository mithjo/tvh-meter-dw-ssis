# TVH Meter Data Warehouse — starter kit

Copy everything in this folder into the root of your repo `tvh-meter-dw-ssis/`:

| Path | Contents |
| --- | --- |
| `README.md` | Portfolio front page template. Fill it in during Session 26. |
| `simulator/` | `generate_sources.py`, `deliver.py`, feed specs, Lake XSD |
| `sql/source/01_create_TVH_CIS.sql` | Creates the source system database |
| `.gitignore` | Keeps generated data and build output out of Git |
| `.gitattributes` | Consistent line endings for SSIS packages and SQL scripts |

Everything else in the repo (staging and warehouse DDL, procedures, SSIS packages,
deployment scripts, tests, docs) is yours to build, following the playbook:
https://claude.ai/artifact/Kq5PtUfdY5qkmNkuhAt7L4

First run (Windows, from the repo root):

```bat
cd simulator
python generate_sources.py --seed <pick your own number> --meters 120 --days 30
```

Then in SSMS: `sql\source\01_create_TVH_CIS.sql`, then `simulator\output\cis_seed\load_seed.sql`.
The SQL Server service account must be able to read the `simulator\output` folder.

Each simulated night after that:

```bat
python deliver.py --next --inbound C:\TVH\data\inbound --apply-sql
```

You can delete this START_HERE.md once the repo is set up.
