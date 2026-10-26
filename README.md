# 🦆 DuckDB SQL Studio

**A lightweight, local SQL workbench for large CSV, Parquet and Excel files — no server, no Excel freezes, no out-of-memory crashes.**

DuckDB SQL Studio is a single-file [Streamlit](https://streamlit.io) app on top of [DuckDB](https://duckdb.org). Point it at a file (or a whole folder), write SQL, and get results, charts and exports in seconds — even for files with millions of rows on an ordinary laptop.

It also includes a **signaling trace viewer** for ZTE LTE UE trace files (`.udt` / `.stm`), decoding RRC, S1AP, X2AP and NAS messages with the official 3GPP ASN.1 definitions.

> The user interface is in Bahasa Indonesia; this README explains the labels where needed.

---

## Why

| Tool | Problem with large files |
| --- | --- |
| Excel | Stops at 1,048,576 rows; "Not Responding" on files of a few hundred MB |
| pandas | Loads the whole file into RAM; a new script for every filter |
| PostgreSQL / DBeaver | Needs a server and an import step before the first query |

DuckDB reads CSV and Parquet **directly from disk**, column by column, using all CPU cores. This app wraps that in a simple UI so the only thing you need is SQL.

> Example: a 309 MB CSV with 612,675 rows × 76 columns loads and is queryable in about 8 seconds.

---

## Features

### 🧮 Query
- SQL editor with multiple statements per run (`SET …; CREATE …; SELECT …`)
- Preview limited to N rows so the browser stays responsive
- **Export the full result** to CSV, Parquet or Excel via DuckDB `COPY` (streaming, no RAM spike); the target folder is created automatically
- Query history (last 30)

### 📂 Data loading
- Register a file **or a glob** (`D:\data\*.csv`, `D:\data\**\*.parquet`) as a view or table
- Formats: CSV/TSV, Parquet, JSON, Excel `.xlsx` / `.xlsm`, and `.xlsb` / `.xls` (via `python-calamine`)
- Built for messy real-world data:
  - Excel error values (`#N/A`, `NA`, `-`, `#VALUE!`, …) are read as empty cells
  - Quoted fields containing commas are parsed correctly
  - If an unexpected value appears deep in a file, column types are re-detected from the whole file automatically
- Optional **Import jadi tabel** (import as table) for 10–100× faster repeated queries

### 📊 Chart
- Line, bar (stacked / grouped / 100%), area, scatter, histogram, pie / donut
- Aggregations computed by DuckDB on the **full** result, not just the preview
- Automatic time bucketing (minute → month), series limit with an "Other" bucket
- Text columns that contain numbers (e.g. `12,5`) are converted automatically
- Charts are only computed when you press **▶ Buat chart** (build chart)

### 📡 Trace (ZTE LTE signaling)
- Opens ZTE UE trace files (`.udt` = ZIP of `.stm`, a folder of `.stm`, or a single `.stm`)
- Decodes **LTE RRC**, **S1AP**, **X2AP (EN-DC)**, NR measurements inside X2 RRC Transfer, and **NAS** inside S1AP
- Maps every measurement report to its event (**A1–A6, B1**, periodic) using the measConfig of that connection
- Timeline with filters (key events, measurement, mobility, A3/A5/B1, protocol, text, time range)
- Message detail: full ASN.1 decode, raw hex, source file and byte offset
- **Ladder diagram** (UE ↔ eNB ↔ MME ↔ SgNB), downloadable as SVG
- RSRP / RSRQ / NR SINR chart with RLF, SCG failure, release and handover markers
- Decoded result is cached and also available as a SQL table (`trace`)

### 📁 File browser & text editor
- Browse folders, preview data files, register one file or a whole folder in one click
- Edit `.sql`, `.txt`, `.py`, `.json` and other text files; automatic `.bak` backup and overwrite protection

### 📖 SQL cheat sheet
- 60+ ready-to-use DuckDB SQL examples (filters, aggregation, window functions, joins, pivots, export); one click loads an example into the editor

---

## Quick start

**Requirements:** Python 3.10 or newer.

```bash
# 1. Install dependencies
pip install -U duckdb streamlit pandas altair

# Optional
pip install -U python-calamine   # read .xlsb / .xls
pip install -U pycrate           # Trace tab (3GPP decoder)

# 2. Run
streamlit run duckdb_gui.py
```

The app opens at `http://localhost:8501`. Keep the terminal open while using it; press `Ctrl+C` to stop.

**Windows shortcut** — save as `run.bat` next to `duckdb_gui.py` and double-click it:

```bat
@echo off
cd /d %~dp0
python -m streamlit run duckdb_gui.py
pause
```

---

## Usage

1. **Register a file.** In the sidebar, enter a path or glob (e.g. `D:\data\kpi_*.csv`), give it a name such as `data`, and click **Register**.
2. **Query it.**
   ```sql
   SELECT site_id, AVG(dl_thp) AS avg_dl
   FROM data
   WHERE ts >= '2026-10-01'
   GROUP BY site_id
   ORDER BY avg_dl;
   ```
3. **Chart it** in the **Chart** tab, or **export** the full result from the **Query** tab.

### Tips

- **Convert big CSVs to Parquet once.** Queries are typically 10–50× faster and files much smaller:
  ```sql
  COPY (SELECT * FROM read_csv('D:/data/*.csv', union_by_name = true))
  TO 'D:/parquet/data.parquet' (FORMAT parquet, COMPRESSION zstd);
  ```
- **Join several files** by registering each under its own name:
  ```sql
  SELECT k.*, s.city
  FROM kpi k
  LEFT JOIN site s ON k.site_id = s.site_id;
  ```
- **Very large files:** limit memory and spill to a roomy disk:
  ```sql
  SET memory_limit = '8GB';
  SET temp_directory = 'D:/duckdb_tmp';
  ```
- **Keep tables after restart:** fill in a `.duckdb` file path in the sidebar instead of using in-memory mode.

---

## Troubleshooting

| Message | Fix |
| --- | --- |
| `'streamlit' is not recognized` | Run `python -m streamlit run duckdb_gui.py` |
| `Could not convert string '#N/A' to DOUBLE` | Add the value to **Anggap kosong / NULL** (treat as empty) in the sidebar, or register with **Import jadi tabel** enabled |
| `Cannot open file …: The system cannot find the path specified` | Check the path; export folders are created automatically in recent versions |
| `Failed to download extension "excel"` | The network blocks `extensions.duckdb.org`; install `python-calamine` or connect once from another network |
| Excel export stops at ~1M rows | Excel's own limit — export to CSV or Parquet instead |
| Trace: `tidak ada record trace ZTE` (no ZTE trace records) | The error lists the first bytes of each file; open an issue with that output |

---

## How it works

```
Browser (Streamlit UI)
        │
        ▼
duckdb_gui.py ──► DuckDB (in-process) ──► CSV / Parquet / Excel on disk
        │                │
        │                └─► COPY … TO file   (full exports, streaming)
        │
        └─► pandas / Altair  (only small results: previews and chart data)
```

All heavy work — filtering, joins, aggregation, export — runs inside DuckDB. pandas only carries the small results that are shown on screen, so memory use stays low even for very large files.

The trace decoder reads the ZTE record header (timestamp, eNB, cell, RNTI, gNB, message class) and decodes each payload with [pycrate](https://github.com/pycrate-org/pycrate) using the 3GPP ASN.1 specifications. Decoded traces are cached as Parquet in `.trace_cache/` next to the app.

---

## Project structure

```
.
├── duckdb_gui.py     # the whole app
├── run.bat           # optional Windows launcher
└── .trace_cache/     # created automatically; cached decoded traces (safe to delete)
```

---

## Limitations

- Single-user, local tool; not designed as a shared multi-user server.
- A running query cannot be cancelled from the UI.
- The Trace tab supports ZTE LTE UE trace files. The record format was identified by inspecting trace files (tested on ITBBU V5.85.20.20MP05); other software versions may need adjustments. Ciphered NAS messages and ZTE vendor-specific procedures cannot be decoded.
- This project is not affiliated with or endorsed by DuckDB Labs, Streamlit or ZTE.

---

## Built with

[DuckDB](https://duckdb.org) · [Streamlit](https://streamlit.io) · [Altair](https://altair-viz.github.io) · [pandas](https://pandas.pydata.org) · [pycrate](https://github.com/pycrate-org/pycrate) · [python-calamine](https://github.com/dimastbk/python-calamine)
