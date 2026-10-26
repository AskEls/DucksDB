"""
DuckDB SQL GUI sederhana (offline) - mirip pgAdmin query tool.
Install : pip install -U duckdb streamlit pandas altair python-calamine pycrate
Jalankan: streamlit run duckdb_gui.py

Tips CSV besar:
- Masukkan PATH file (bukan upload), DuckDB baca langsung dari disk tanpa load ke RAM.
- Bisa glob: D:/data/*.csv  -> semua file digabung jadi satu tabel.
- Preview dibatasi N baris; hasil penuh di-export pakai COPY (streaming, cepat).
- Tab "Docs" berisi cheat sheet SQL; klik "Pakai" untuk memuat contoh ke editor.
- Tab "Chart" membuat line/bar/area/scatter/histogram/pie dari hasil query terakhir.
- Tab "Trace" membuka UE trace ZTE (.udt/.stm): timeline signaling, decode 3GPP, ladder, RSRP/RSRQ.
- Tab "File" untuk jelajah folder, preview/register file, dan text editor (.sql, .txt, dll).
"""
import datetime
import os
import re
import shutil
import string
import time

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

st.set_page_config(page_title="DuckDB SQL Studio", page_icon="🦆", layout="wide")

# ---------- Tampilan & animasi ----------
st.markdown(
    """
<style>
@keyframes fadeUp {from {opacity:0; transform:translateY(10px)} to {opacity:1; transform:none}}
@keyframes pop {from {opacity:0; transform:scale(.97)} to {opacity:1; transform:scale(1)}}
@keyframes shimmer {to {background-position:200% center}}
@keyframes bob {0%,100% {transform:translateY(0) rotate(0)} 50% {transform:translateY(-5px) rotate(-8deg)}}
.hero {padding:.2rem 0 .9rem; animation:fadeUp .6s ease both}
.hero-duck {display:inline-block; font-size:2rem; animation:bob 2.6s ease-in-out infinite}
.hero-title {font-size:2rem; font-weight:800; letter-spacing:-.02em;
  background:linear-gradient(90deg,#2a78d6,#1baf7a,#eda100,#e87ba4,#2a78d6); background-size:200% auto;
  -webkit-background-clip:text; background-clip:text; color:transparent; animation:shimmer 8s linear infinite}
.hero-sub {display:block; opacity:.65; font-size:.95rem; margin-top:.1rem}
[data-testid="stTabPanel"] {animation:fadeUp .35s ease both}
[data-testid="stDataFrame"], [data-testid="stVegaLiteChart"] {animation:pop .5s cubic-bezier(.2,.8,.2,1) both}
[data-testid="stAlertContainer"] {animation:fadeUp .3s ease both}
[data-testid="stSidebar"] [data-testid="stExpander"] {animation:fadeUp .4s ease both}
[data-testid="stExpander"] details {transition:box-shadow .2s ease, border-color .2s ease}
[data-testid="stExpander"] details:hover {box-shadow:0 2px 14px rgba(42,120,214,.18); border-color:rgba(42,120,214,.45)}
.stButton button, .stDownloadButton button {transition:transform .15s ease, box-shadow .2s ease}
.stButton button:hover, .stDownloadButton button:hover {transform:translateY(-2px); box-shadow:0 6px 16px rgba(42,120,214,.28)}
.stButton button:active {transform:translateY(0) scale(.97)}
@media (prefers-reduced-motion: reduce) {* {animation:none !important; transition:none !important}}
</style>
<div class="hero">
  <span class="hero-duck">🦆</span> <span class="hero-title">DuckDB SQL Studio</span>
  <span class="hero-sub">Query file besar · Chart · Export — semuanya lokal di laptop</span>
</div>
""",
    unsafe_allow_html=True,
)

# Palet kategori (urutan tetap, sudah divalidasi aman untuk buta warna)
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]

# ---------- Cheat sheet SQL (judul, penjelasan, sql) ----------
# Contoh memakai view bernama "data" dengan kolom contoh:
# site_id, cell_id, msisdn, ts (timestamp), rsrp, sinr, dl_thp, ul_thp
DOCS = {
    "1. Dasar": [
        ("Lihat semua kolom", "Ambil 100 baris pertama.",
         "SELECT *\nFROM data\nLIMIT 100;"),
        ("Pilih kolom tertentu + alias", "AS untuk mengganti nama kolom di hasil.",
         "SELECT site_id, cell_id, dl_thp AS throughput_dl\nFROM data\nLIMIT 100;"),
        ("Urutkan", "ORDER BY ... DESC = besar ke kecil, ASC = kecil ke besar.",
         "SELECT *\nFROM data\nORDER BY dl_thp DESC\nLIMIT 50;"),
        ("Nilai unik", "DISTINCT menghapus duplikat.",
         "SELECT DISTINCT site_id\nFROM data\nORDER BY site_id;"),
        ("Hitung jumlah baris", "",
         "SELECT COUNT(*) AS jumlah_baris\nFROM data;"),
        ("Lihat struktur kolom & tipe", "DESCRIBE = daftar kolom; SUMMARIZE = statistik cepat tiap kolom (min, max, null, unik).",
         "SUMMARIZE data;"),
    ],
    "2. Filter (WHERE)": [
        ("Filter angka", "Operator: =  <>  >  >=  <  <=",
         "SELECT *\nFROM data\nWHERE rsrp < -110\n  AND sinr < 0;"),
        ("Filter teks persis / beberapa nilai", "IN untuk banyak nilai sekaligus. Teks pakai kutip tunggal.",
         "SELECT *\nFROM data\nWHERE site_id IN ('JKT001', 'JKT002', 'BDG010');"),
        ("Filter mengandung teks", "LIKE: % = apa saja. ILIKE = tidak peduli huruf besar/kecil.",
         "SELECT *\nFROM data\nWHERE cell_id ILIKE '%jkt%';"),
        ("Rentang nilai / tanggal", "BETWEEN termasuk batas bawah & atas.",
         "SELECT *\nFROM data\nWHERE ts BETWEEN '2026-10-01' AND '2026-10-07 23:59:59';"),
        ("Cek kosong (NULL)", "Jangan pakai = NULL, harus IS NULL / IS NOT NULL.",
         "SELECT *\nFROM data\nWHERE sinr IS NULL;"),
        ("Kebalikan filter", "NOT IN, NOT LIKE, NOT BETWEEN.",
         "SELECT *\nFROM data\nWHERE site_id NOT IN ('JKT001')\n  AND cell_id NOT LIKE '%TEST%';"),
        ("Filter pakai regex", "regexp_matches(kolom, pola).",
         "SELECT *\nFROM data\nWHERE regexp_matches(msisdn, '^628[0-9]{9,11}$');"),
    ],
    "3. Agregasi (GROUP BY)": [
        ("Rata-rata per site", "Fungsi agregat: COUNT, SUM, AVG, MIN, MAX.",
         "SELECT site_id,\n       COUNT(*)        AS n,\n       AVG(dl_thp)     AS avg_dl,\n       MIN(rsrp)       AS min_rsrp,\n       MAX(rsrp)       AS max_rsrp\nFROM data\nGROUP BY site_id\nORDER BY avg_dl;"),
        ("Filter setelah agregasi", "HAVING = WHERE tapi untuk hasil GROUP BY.",
         "SELECT site_id, AVG(sinr) AS avg_sinr\nFROM data\nGROUP BY site_id\nHAVING AVG(sinr) < 5\nORDER BY avg_sinr;"),
        ("Hitung yang memenuhi syarat", "COUNT(*) FILTER (WHERE ...) = hitung bersyarat.",
         "SELECT site_id,\n       COUNT(*) AS total,\n       COUNT(*) FILTER (WHERE rsrp < -110) AS n_bad_rsrp,\n       ROUND(100.0 * COUNT(*) FILTER (WHERE rsrp < -110) / COUNT(*), 2) AS pct_bad\nFROM data\nGROUP BY site_id\nORDER BY pct_bad DESC;"),
        ("Median & percentile", "median(), quantile_cont(kolom, 0.1) = P10.",
         "SELECT cell_id,\n       median(dl_thp)              AS p50,\n       quantile_cont(dl_thp, 0.1)  AS p10,\n       quantile_cont(dl_thp, 0.9)  AS p90\nFROM data\nGROUP BY cell_id;"),
        ("Hitung unik", "COUNT(DISTINCT ...).",
         "SELECT site_id, COUNT(DISTINCT msisdn) AS jumlah_user\nFROM data\nGROUP BY site_id\nORDER BY jumlah_user DESC;"),
        ("GROUP BY semua kolom non-agregat", "Shortcut DuckDB: GROUP BY ALL.",
         "SELECT site_id, cell_id, AVG(dl_thp) AS avg_dl\nFROM data\nGROUP BY ALL;"),
    ],
    "4. Tanggal & waktu": [
        ("Agregasi per jam / hari", "date_trunc('hour'|'day'|'week'|'month', ts).",
         "SELECT date_trunc('hour', ts) AS jam,\n       AVG(dl_thp) AS avg_dl\nFROM data\nGROUP BY jam\nORDER BY jam;"),
        ("Ambil bagian tanggal", "hour(), dayofweek() (0=Minggu), CAST ke DATE.",
         "SELECT CAST(ts AS DATE) AS tgl,\n       hour(ts) AS jam,\n       dayofweek(ts) AS hari\nFROM data\nLIMIT 100;"),
        ("Teks → timestamp", "strptime(teks, format). Contoh '24/10/2026 13:00'.",
         "SELECT strptime(waktu_teks, '%d/%m/%Y %H:%M') AS ts\nFROM data\nLIMIT 10;"),
        ("Timestamp → teks", "strftime(ts, format).",
         "SELECT strftime(ts, '%Y-%m-%d %H:00') AS jam_label\nFROM data\nLIMIT 10;"),
        ("N hari terakhir", "now() / current_date, INTERVAL.",
         "SELECT *\nFROM data\nWHERE ts >= current_date - INTERVAL 7 DAY;"),
        ("Busy hour (jam tersibuk per site)", "GROUP BY + QUALIFY. Catatan: QUALIFY belum bisa dipakai bareng GROUP BY ALL, tulis kolomnya eksplisit.",
         "SELECT site_id, hour(ts) AS jam, SUM(dl_thp) AS total\nFROM data\nGROUP BY site_id, jam\nQUALIFY ROW_NUMBER() OVER (PARTITION BY site_id ORDER BY total DESC) = 1;"),
    ],
    "5. Teks": [
        ("Fungsi teks umum", "upper, lower, trim, length, replace, substr(kolom, mulai, panjang).",
         "SELECT upper(site_id), trim(cell_id), length(msisdn),\n       replace(msisdn, '+62', '0'), substr(cell_id, 1, 6)\nFROM data\nLIMIT 10;"),
        ("Gabung teks", "|| atau concat().",
         "SELECT site_id || '_' || cell_id AS key\nFROM data\nLIMIT 10;"),
        ("Pecah teks", "split_part(teks, pemisah, ke-n).",
         "SELECT split_part(cell_id, '_', 1) AS prefix\nFROM data\nLIMIT 10;"),
        ("Ambil pola dengan regex", "regexp_extract(teks, pola, grup).",
         "SELECT regexp_extract(cell_id, '([A-Z]{3})([0-9]+)', 2) AS nomor\nFROM data\nLIMIT 10;"),
    ],
    "6. Kondisi & konversi": [
        ("CASE WHEN (if-else)", "Kategorisasi nilai.",
         "SELECT msisdn, rsrp,\n       CASE\n         WHEN rsrp >= -90  THEN 'Excellent'\n         WHEN rsrp >= -100 THEN 'Good'\n         WHEN rsrp >= -110 THEN 'Fair'\n         ELSE 'Poor'\n       END AS kategori_rsrp\nFROM data;"),
        ("Ganti NULL", "COALESCE(a, b) = ambil yang tidak NULL pertama.",
         "SELECT COALESCE(sinr, 0) AS sinr\nFROM data;"),
        ("Ubah tipe data", "CAST(x AS tipe). TRY_CAST = jadi NULL kalau gagal (aman untuk data kotor).",
         "SELECT TRY_CAST(rsrp AS DOUBLE) AS rsrp_num,\n       CAST(ts AS DATE)            AS tgl\nFROM data;"),
        ("Pembulatan & hindari bagi nol", "ROUND(x, desimal). NULLIF(y, 0) agar x/0 jadi NULL.",
         "SELECT ROUND(dl_thp / NULLIF(ul_thp, 0), 2) AS rasio_dl_ul\nFROM data;"),
    ],
    "7. JOIN (gabung tabel)": [
        ("INNER JOIN", "Hanya baris yang cocok di kedua tabel. Register file kedua sebagai view lain dulu (mis. 'site').",
         "SELECT d.*, s.kota, s.cluster\nFROM data d\nJOIN site s ON d.site_id = s.site_id;"),
        ("LEFT JOIN", "Semua baris kiri tetap muncul; yang tidak cocok kolom kanan = NULL.",
         "SELECT d.*, s.kota\nFROM data d\nLEFT JOIN site s ON d.site_id = s.site_id;"),
        ("Cari yang TIDAK ada di tabel lain", "ANTI JOIN (kebalikan dari cocok).",
         "SELECT d.*\nFROM data d\nANTI JOIN site s ON d.site_id = s.site_id;"),
        ("Gabung baris dua tabel", "UNION ALL (tumpuk). UNION BY NAME = cocokkan nama kolom, urutan bebas.",
         "SELECT * FROM data_sept\nUNION ALL BY NAME\nSELECT * FROM data_okt;"),
    ],
    "8. Window function": [
        ("Ranking", "ROW_NUMBER / RANK per grup (PARTITION BY).",
         "SELECT site_id, cell_id, AVG(dl_thp) AS avg_dl,\n       RANK() OVER (PARTITION BY site_id ORDER BY AVG(dl_thp) DESC) AS rnk\nFROM data\nGROUP BY site_id, cell_id;"),
        ("Top N per grup", "QUALIFY = filter hasil window (khas DuckDB, tidak perlu subquery).",
         "SELECT *\nFROM data\nQUALIFY ROW_NUMBER() OVER (PARTITION BY site_id ORDER BY dl_thp DESC) <= 3;"),
        ("Data terakhir per user", "Ambil baris terbaru tiap msisdn.",
         "SELECT *\nFROM data\nQUALIFY ROW_NUMBER() OVER (PARTITION BY msisdn ORDER BY ts DESC) = 1;"),
        ("Nilai sebelumnya / selisih", "LAG = baris sebelumnya, LEAD = sesudahnya.",
         "SELECT msisdn, ts, cell_id,\n       LAG(cell_id) OVER (PARTITION BY msisdn ORDER BY ts) AS cell_sebelumnya\nFROM data;"),
        ("Moving average", "Rata-rata bergerak 24 baris (mis. 24 jam).",
         "SELECT cell_id, ts, dl_thp,\n       AVG(dl_thp) OVER (PARTITION BY cell_id ORDER BY ts\n                         ROWS BETWEEN 23 PRECEDING AND CURRENT ROW) AS ma_24\nFROM data;"),
    ],
    "9. Query bertingkat (CTE)": [
        ("WITH (CTE)", "Pecah query panjang jadi langkah-langkah bernama, lebih gampang dibaca.",
         "WITH per_site AS (\n  SELECT site_id, AVG(sinr) AS avg_sinr\n  FROM data\n  GROUP BY site_id\n),\nburuk AS (\n  SELECT * FROM per_site WHERE avg_sinr < 3\n)\nSELECT d.*\nFROM data d\nJOIN buruk b USING (site_id);"),
        ("Subquery di WHERE", "",
         "SELECT *\nFROM data\nWHERE site_id IN (\n  SELECT site_id FROM data GROUP BY site_id HAVING AVG(rsrp) < -105\n);"),
    ],
    "10. Shortcut khas DuckDB": [
        ("Semua kolom kecuali beberapa", "SELECT * EXCLUDE (...).",
         "SELECT * EXCLUDE (msisdn, ul_thp)\nFROM data\nLIMIT 100;"),
        ("Ganti satu kolom, sisanya tetap", "SELECT * REPLACE (...).",
         "SELECT * REPLACE (ROUND(dl_thp, 1) AS dl_thp)\nFROM data\nLIMIT 100;"),
        ("Pilih kolom pakai pola", "COLUMNS('regex') - mis. semua kolom yang diawali 'dl_'.",
         "SELECT site_id, AVG(COLUMNS('^dl_|^ul_'))\nFROM data\nGROUP BY site_id;"),
        ("FROM duluan", "Boleh tulis FROM tanpa SELECT.",
         "FROM data\nWHERE rsrp < -110\nLIMIT 20;"),
        ("Pivot (baris → kolom)", "Mis. throughput per jam jadi kolom.",
         "PIVOT (SELECT site_id, hour(ts) AS jam, dl_thp FROM data)\nON jam\nUSING AVG(dl_thp)\nGROUP BY site_id;"),
        ("Sampling acak", "Ambil sampel cepat dari data besar.",
         "SELECT *\nFROM data\nUSING SAMPLE 1%;"),
    ],
    "11. File, tabel & export": [
        ("Baca CSV langsung tanpa register", "Path pakai '/' atau '\\\\'. Glob * untuk banyak file.",
         "SELECT *\nFROM read_csv('D:/data/*.csv', union_by_name=true)\nLIMIT 100;"),
        ("Baca CSV dengan opsi", "delim, header, all_varchar, skip (lewati N baris awal).",
         "SELECT *\nFROM read_csv('D:/data/file.csv', delim=';', header=true, all_varchar=true, skip=0)\nLIMIT 100;"),
        ("Tahu asal file tiap baris", "filename=true menambah kolom nama file.",
         "SELECT filename, COUNT(*)\nFROM read_csv('D:/data/*.csv', filename=true)\nGROUP BY filename;"),
        ("Simpan hasil jadi tabel", "Tabel materialized = query berikutnya lebih cepat. Pakai file .duckdb agar permanen.",
         "CREATE OR REPLACE TABLE data_bad AS\nSELECT * FROM data WHERE rsrp < -110;"),
        ("CSV → Parquet", "Kompresi zstd, file jauh lebih kecil & cepat.",
         "COPY (SELECT * FROM read_csv('D:/data/*.csv', union_by_name=true))\nTO 'D:/output/data.parquet' (FORMAT parquet, COMPRESSION zstd);"),
        ("Parquet dipartisi per tanggal", "Query yang filter tanggal hanya baca folder yang relevan. OVERWRITE = timpa folder lama.",
         "COPY (SELECT *, CAST(ts AS DATE) AS tgl FROM data)\nTO 'D:/output/data_parquet' (FORMAT parquet, PARTITION_BY (tgl), OVERWRITE);"),
        ("Export ke CSV", "",
         "COPY (SELECT * FROM data WHERE rsrp < -110)\nTO 'D:/output/bad_rsrp.csv' (HEADER, DELIMITER ',');"),
        ("Hapus tabel/view", "",
         "DROP VIEW IF EXISTS data_lama;\n-- DROP TABLE IF EXISTS nama_tabel;"),
    ],
    "12. Performa & setting": [
        ("Batasi RAM & folder temp", "Untuk file sangat besar: spill ke disk yang lega.",
         "SET memory_limit = '8GB';\nSET temp_directory = 'D:/duckdb_tmp';\nSET threads = 8;"),
        ("Lihat rencana eksekusi", "EXPLAIN ANALYZE = jalankan + tampilkan waktu tiap langkah.",
         "EXPLAIN ANALYZE\nSELECT site_id, AVG(dl_thp) FROM data GROUP BY site_id;"),
        ("Cek setting saat ini", "",
         "SELECT name, value\nFROM duckdb_settings()\nWHERE name IN ('memory_limit', 'threads', 'temp_directory');"),
    ],
}

# ---------- Koneksi ----------
# Satu database dibagi semua sesi (view/tabel kelihatan di semua tab browser),
# tapi tiap run memakai cursor() sendiri supaya tidak bentrok "connection is busy".
@st.cache_resource
def get_db(db_path: str):
    db = duckdb.connect(db_path if db_path else ":memory:")
    db.execute("SET preserve_insertion_order=false")  # lebih hemat RAM utk file besar
    return db

@st.cache_resource
def schema_cache(db_path: str) -> dict:
    """Cache DESCRIBE per objek, supaya sidebar tidak membaca ulang CSV setiap klik."""
    return {}

if "history" not in st.session_state:
    st.session_state.history = []

# ---------- Sidebar: koneksi & register file ----------
# ---------- Baca file (CSV, Parquet, JSON, Excel) ----------
EXCEL_DUCK = (".xlsx", ".xlsm")          # dibaca DuckDB (extension excel), cadangan: calamine
EXCEL_PD = (".xlsb", ".xls")             # dibaca lewat pandas + python-calamine
DATA_EXT = (".csv", ".tsv", ".parquet", ".json", ".jsonl") + EXCEL_DUCK + EXCEL_PD


def sql_str(path: str) -> str:
    return path.replace("\\", "/").replace("'", "''")


NULL_DEFAULT = "#N/A, N/A, NA, -, #VALUE!, #DIV/0!, #REF!, #NAME?, #NUM!, #NULL!"


def nullstr_list() -> list[str]:
    raw = st.session_state.get("nullstr_cfg", NULL_DEFAULT)
    return [v.strip() for v in raw.split(",") if v.strip()]


def source_sql(path: str, delim: str = "", all_varchar: bool = False, sheet: str = "") -> str | None:
    """Ekspresi FROM untuk file/glob. None = harus lewat pandas (xlsb/xls)."""
    p = sql_str(path)
    ext = os.path.splitext(p.lower())[1]
    if ext in EXCEL_PD:
        return None
    if ext == ".parquet":
        return f"read_parquet('{p}')"
    if ext in (".json", ".jsonl"):
        return f"read_json_auto('{p}')"
    if ext in EXCEL_DUCK:
        opts = ["all_varchar=true"] if all_varchar else []
        if sheet:
            opts.append(f"sheet='{sheet.replace(chr(39), chr(39) * 2)}'")
        return f"read_xlsx('{p}'" + "".join(", " + o for o in opts) + ")"
    # kutip ganda standar CSV/Excel: koma di dalam "..." bukan pemisah kolom. Tanpa ini, DuckDB bisa
    # menyimpulkan "tanpa kutip" dari 20 ribu baris awal, lalu kolom bergeser di baris yang berkutip.
    opts = ["quote='\"'", "escape='\"'"]
    if any(ch in p for ch in "*?["):
        opts.append("union_by_name=true")  # hanya perlu untuk banyak file
    if delim:
        opts.append(f"delim='{delim}'")
    if all_varchar:
        opts.append("all_varchar=true")
    nulls = nullstr_list()
    if nulls:
        # nilai seperti #N/A dari Excel dianggap kosong (NULL), bukan bikin error tipe data
        opts.append("nullstr=[" + ", ".join("'" + v.replace("'", "''") + "'" for v in nulls + [""]) + "]")
    return f"read_csv('{p}'" + "".join(", " + o for o in opts) + ")"


def read_excel_pd(path: str, sheet: str = "", nrows: int | None = None, all_varchar: bool = False) -> pd.DataFrame:
    try:
        import python_calamine  # noqa: F401
    except ImportError:
        raise RuntimeError("Untuk file .xlsb/.xls butuh library tambahan: pip install python-calamine")
    df = pd.read_excel(path, engine="calamine", sheet_name=sheet or 0, nrows=nrows,
                       dtype=str if all_varchar else None,
                       na_values=nullstr_list(), keep_default_na=True)  # NA, #N/A, - dll -> kosong
    df.columns = [str(c) for c in df.columns]
    for c in df.columns:
        if df[c].dtype == object:  # kolom campuran angka+teks -> teks semua, supaya DuckDB tidak bingung
            df[c] = df[c].map(lambda v: v if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
    return df


def _q(c: str) -> str:
    return '"' + str(c).replace('"', '""') + '"'


def smart_cast_table(con, vname: str):
    """Kolom teks yang isinya angka (selain nilai 'Anggap kosong') diubah jadi DOUBLE."""
    nulls = ", ".join("'" + v.replace("'", "''") + "'" for v in nullstr_list() + [""])
    exprs = []
    for name, typ, *_ in con.execute(f'DESCRIBE "{vname}"').fetchall():
        q = _q(name)
        if typ == "VARCHAR":
            v = f"trim({q})"
            ok = con.execute(
                f"SELECT COUNT(*) FILTER (WHERE {v} IS NOT NULL AND {v} NOT IN ({nulls}) "
                f"AND TRY_CAST({v} AS DOUBLE) IS NULL) = 0 AND COUNT(TRY_CAST({v} AS DOUBLE)) > 0 "
                f'FROM "{vname}"').fetchone()[0]
            if ok:
                exprs.append(f"TRY_CAST(CASE WHEN {v} IN ({nulls}) THEN NULL ELSE {v} END AS DOUBLE) AS {q}")
                continue
            exprs.append(f"CASE WHEN {v} IN ({nulls}) THEN NULL ELSE {q} END AS {q}")
        else:
            exprs.append(q)
    con.execute(f'CREATE OR REPLACE TABLE "{vname}" AS SELECT {", ".join(exprs)} FROM "{vname}"')


def excel_sheets(path: str) -> list[str]:
    try:
        from python_calamine import CalamineWorkbook
        return list(CalamineWorkbook.from_path(path).sheet_names)
    except Exception:
        return []


def register_file(con, path: str, vname: str, delim: str = "", all_varchar: bool = False,
                  as_table: bool = True, sheet: str = "") -> str:
    """Daftarkan file/glob sebagai VIEW atau TABLE. Return 'Tabel'/'View'."""
    # hapus objek lama dengan nama sama, apa pun jenisnya (view atau tabel)
    for (ttype,) in con.execute("SELECT table_type FROM information_schema.tables "
                                "WHERE table_schema = 'main' AND table_name = ?", [vname]).fetchall():
        con.execute(f'DROP {"VIEW" if ttype == "VIEW" else "TABLE"} "{vname}"')
    src = source_sql(path, delim, all_varchar, sheet)
    if src is not None:
        kind = "TABLE" if as_table else "VIEW"
        try:
            con.execute(f'CREATE {kind} "{vname}" AS SELECT * FROM {src}')
            return "Tabel" if as_table else "View"
        except Exception as e:
            is_csv = src.startswith("read_csv(")
            if is_csv and ("Conversion Error" in str(e) or "Could not convert" in str(e)):
                # tipe ditebak dari 20 ribu baris awal, lalu ketemu nilai aneh di bawahnya.
                # Ulangi: deteksi tipe dari SELURUH file sekali, lalu kunci tipenya (view tetap cepat).
                full = src[:-1] + ", sample_size=-1)"
                cols_t = con.execute(f"DESCRIBE SELECT * FROM {full}").fetchall()
                types = "{" + ", ".join("'" + c[0].replace("'", "''") + "': '" + c[1] + "'" for c in cols_t) + "}"
                fixed = src[:-1] + f", types={types})"
                con.execute(f'CREATE {kind} "{vname}" AS SELECT * FROM {fixed}')
                st.session_state.reg_note = ("Ada nilai tak terduga di bawah baris ke-20.000; tipe kolom dideteksi "
                                             "ulang dari seluruh file. Kolom campuran dibaca sebagai teks.")
                return "Tabel" if as_table else "View"
            if os.path.splitext(path.lower())[1] not in EXCEL_DUCK:
                raise
            # Excel gagal (mis. teks 'NA' di kolom angka, atau extension diblok proxy):
            # 1) lewat calamine (kalau terinstall), 2) baca semua sebagai teks lalu konversi kolom angka
            try:
                df_x = read_excel_pd(path, sheet, all_varchar=all_varchar)
            except RuntimeError:
                try:
                    con.execute(f'CREATE TABLE "{vname}" AS SELECT * FROM '
                                f'{source_sql(path, all_varchar=True, sheet=sheet)}')
                except Exception:
                    raise e
                if not all_varchar:
                    smart_cast_table(con, vname)
                st.session_state.reg_note = "Excel: nilai seperti NA/#N/A dianggap kosong."
                return "Tabel"
    else:
        df_x = read_excel_pd(path, sheet, all_varchar=all_varchar)
    con.register("__tmp_df", df_x)
    try:
        con.execute(f'CREATE TABLE "{vname}" AS SELECT * FROM __tmp_df')
    finally:
        con.unregister("__tmp_df")
    return "Tabel"


def preview_file(con, path: str, sheet: str = "", n: int = 100) -> pd.DataFrame:
    src = source_sql(path, sheet=sheet)
    if src is not None:
        try:
            return con.execute(f"SELECT * FROM {src} LIMIT {n}").df()
        except Exception:
            if os.path.splitext(path.lower())[1] not in EXCEL_DUCK:
                raise
            try:
                return read_excel_pd(path, sheet, nrows=n)
            except RuntimeError:
                return con.execute(f"SELECT * FROM {source_sql(path, all_varchar=True, sheet=sheet)} LIMIT {n}").df()
    return read_excel_pd(path, sheet, nrows=n)


def safe_name(stem: str) -> str:
    s = re.sub(r"\W+", "_", stem).strip("_").lower() or "data"
    return s if not s[0].isdigit() else "t_" + s


# ---------- Trace ZTE (.udt / .stm): parser + decoder 3GPP ----------
# .udt = ZIP berisi file .stm (format LteSignalAnalysis ZTE). Tiap record diawali marker ZTE,
# header 0x70 byte (waktu, eNB, cell, RNTI, gNB, kelas & ID pesan), lalu payload ASN.1 3GPP.
import hashlib
import io
import zipfile

ZTE_MARK = b"\xaa\x55"
# byte ke-3 beda per versi (cd, ce, ...), byte 4-7 bisa beda per task -> pola longgar + validasi panjang
ZTE_MARK_RE = re.compile(rb"\xaa\x55[\xc0-\xdf]\x06", re.S)
ZTE_EPOCH = datetime.datetime(2000, 1, 1)
TRACE_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".trace_cache")
TRACE_CACHE_VER = "v4"


def stm_blobs(path: str):
    """(nama_file, bytes) untuk setiap .stm di .udt/.zip, folder, atau file .stm tunggal."""
    path = os.path.abspath(os.path.expanduser(path.strip().strip('"')))
    if os.path.isdir(path):
        for root, _, files in os.walk(path):
            for f in sorted(files):
                if f.lower().endswith(".stm"):
                    with open(os.path.join(root, f), "rb") as fh:
                        yield f, fh.read()
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for info in sorted(z.infolist(), key=lambda i: i.filename):
                if info.filename.lower().endswith(".stm"):
                    yield info.filename.split("/")[-1], z.read(info)
    else:
        with open(path, "rb") as fh:
            yield os.path.basename(path), fh.read()


def scan_stm(name: str, d: bytes, include_internal: bool = False):
    """Pecah satu file .stm menjadi record. cls 0 = S1AP, 1 = X2AP, 2 = RRC LTE, lainnya = internal ZTE."""
    out = []
    for m in ZTE_MARK_RE.finditer(d):
        a = m.start()
        L = int.from_bytes(d[a + 8:a + 10], "big") + 12
        if L < 0x70 or a + L > len(d):
            continue
        r = d[a:a + L]
        pl = int.from_bytes(r[0x60:0x62], "big")
        if 0x70 + pl > L + 16:          # panjang payload tidak masuk akal -> bukan record
            continue
        cls = r[0x5d]
        if cls not in (0, 1, 2) and not include_internal:
            continue
        ts = int.from_bytes(r[0x54:0x58], "big")
        ms = int.from_bytes(r[0x58:0x5a], "big")
        out.append(dict(
            time=ZTE_EPOCH + datetime.timedelta(seconds=ts, milliseconds=ms),
            file=name, offset=a,
            enb=int.from_bytes(r[0x10:0x14], "big"), cell=int.from_bytes(r[0x14:0x16], "big"),
            rnti=int.from_bytes(r[0x16:0x18], "big"), gnb=int.from_bytes(r[0x30:0x34], "big"),
            cls=cls, mid=int.from_bytes(r[0x5e:0x60], "big"),
            hdr=r[:0x70].hex(" "), payload=r[0x70:0x70 + pl]))
    return out


@st.cache_resource(show_spinner=False)
def asn1_types():
    try:
        from pycrate_asn1dir import RRCLTE, S1AP, X2AP, RRCNR
    except ImportError:
        raise RuntimeError("Decoder trace butuh library pycrate: pip install pycrate")
    E = RRCLTE.EUTRA_RRC_Definitions
    return dict(UL=E.UL_DCCH_Message, DL=E.DL_DCCH_Message, ULC=E.UL_CCCH_Message, DLC=E.DL_CCCH_Message,
                S1=S1AP.S1AP_PDU_Descriptions.S1AP_PDU, X2=X2AP.X2AP_PDU_Descriptions.X2AP_PDU,
                NRUL=RRCNR.NR_RRC_Definitions.UL_DCCH_Message)


# ID pesan RRC ZTE -> (tipe ASN.1, arah)
RRC_MID = {0xe1bc: ("DL", "DL"), 0xe1be: ("UL", "UL"), 0xe1c0: ("DL", "DL"), 0xe1c1: ("UL", "UL"),
           0xe1c2: ("ULC", "UL"), 0xe1c3: ("DLC", "DL"), 0xe1c4: ("UL", "UL"), 0xe1c5: ("DLC", "DL"),
           0xe1c6: ("ULC", "UL"), 0xe1c7: ("DLC", "DL"), 0xe1c8: ("UL", "UL"), 0xe1c9: ("DLC", "DL"),
           0xe1ca: ("DL", "DL"), 0xe1cb: ("DL", "DL"), 0xe1cc: ("UL", "UL"), 0xe1ce: ("DL", "DL"),
           0xe1cf: ("UL", "UL"), 0xe1d1: ("UL", "UL"), 0xe1d2: ("DL", "DL"), 0xe1d3: ("UL", "UL"),
           0xe1e3: ("UL", "UL"), 0xe1e4: ("UL", "UL")}


@st.cache_resource(show_spinner=False)
def nas_parsers():
    import contextlib
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            from pycrate_mobile.NAS import parse_NAS_MO, parse_NAS_MT
        return parse_NAS_MO, parse_NAS_MT
    except Exception:
        return None, None


def decode_nas(b: bytes, downlink: bool, full: bool = False):
    """Return (nama, teks). NAS yang terenkripsi hanya bisa dibaca header-nya."""
    mo, mt = nas_parsers()
    if not mo:
        return "NAS", ""
    try:
        m, err = (mt if downlink else mo)(b)
        if m is None:
            return "NAS?", ""
        if m._name == "EMMSecProtNASMessage":
            sh = b[0] >> 4
            if sh in (1, 3) and len(b) > 6:        # integrity only: isi masih terbaca
                m2, _ = (mt if downlink else mo)(b[6:])
                if m2 is not None:
                    return m2._name, (m2.show() if full else "")
            return "ciphered", (m.show() if full else "")
        return m._name, (m.show() if full else "")
    except Exception as e:
        return "NAS?", f"(gagal decode NAS: {e})"


def _find(o, key, acc):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key:
                acc.append(v)
            _find(v, key, acc)
    elif isinstance(o, (list, tuple)):
        for v in o:
            _find(v, key, acc)
    return acc


def _rrc_name(v):
    m = v["message"]
    while m[0] in ("c1", "c2", "messageClassExtension", "criticalExtensions"):
        m = m[1]
    return m[0]


def _mr_lte(v):
    mr = _find(v, "measResults", [])[0]
    pc = mr["measResultPCell"]
    s = f"measId={mr['measId']} PCell RSRP={pc['rsrpResult'] - 140} RSRQ={pc['rsrqResult'] / 2 - 20}"
    nb = mr.get("measResultNeighCells")
    if nb and nb[0] == "measResultListEUTRA":
        s += " | LTE nbr: " + ", ".join(
            f"PCI{c['physCellId']}:{c['measResult']['rsrpResult'] - 140}" if "rsrpResult" in c["measResult"]
            else f"PCI{c['physCellId']}:na" for c in nb[1])
    elif nb and "NR" in nb[0]:
        parts = []
        for f in nb[1]:
            for c in f.get("measResultCellListNR-r15", []):
                r = c["measResultCell-r15"]
                parts.append(f"NR PCI{c['pci-r15']}:" + (str(r["rsrpResult-r15"] - 156) if "rsrpResult-r15" in r else "na"))
        s += " | NR nbr: " + ", ".join(parts)
    for f in mr.get("measResultServFreqListNR-r15", []) or []:
        sc = f.get("measResultSCell-r15")
        if sc:
            r = sc["measResultCell-r15"]
            s += (f" | NR SCG PCI{sc['pci-r15']} RSRP=" + (str(r["rsrpResult-r15"] - 156) if "rsrpResult-r15" in r else "na")
                  + " SINR=" + (str(r["rs-sinr-Result-r15"] / 2 - 23) if "rs-sinr-Result-r15" in r else "na"))
    return s, mr["measId"]


def _mr_nr(b, T):
    T["NRUL"].from_uper(b)
    v = T["NRUL"].get_val()
    mr = _find(v, "measResults", [])
    if not mr:
        return "NR " + _rrc_name(v), None
    mr = mr[0]
    s = f"NR MR measId={mr['measId']}"
    for sc in mr.get("measResultServingMOList", []):
        r = _find(sc["measResultServingCell"], "cellResults", [])
        ss = r[0].get("resultsSSB-Cell", {}) if r else {}
        s += (f" | serv PCI{sc['measResultServingCell'].get('physCellId', '?')} RSRP="
              + (str(ss["rsrp"] - 156) if "rsrp" in ss else "na")
              + " SINR=" + (str(ss["sinr"] / 2 - 23) if "sinr" in ss else "na"))
    return s, mr["measId"]


def decode_record(cls: int, mid: int, payload: bytes, T: dict, full: bool = False):
    """Return (proto, msg, arah, info, measId[, teks ASN.1 lengkap])."""
    proto, msg, d, info, measid, txt = "ZTE-internal", f"0x{mid:04x}", "", "", None, ""
    try:
        if cls == 2 and mid in RRC_MID:
            tn, d = RRC_MID[mid]
            t = T[tn]
            proto = "RRC"
            t.from_uper(payload)
            v = t.get_val()
            proto, msg = "RRC", _rrc_name(v)
            if msg == "measurementReport":
                info, measid = _mr_lte(v)
            elif msg == "ulInformationTransferMRDC-r15":
                b = _find(v, "ul-DCCH-MessageNR-r15", [])
                if b:
                    info, measid = _mr_nr(b[0], T)
            elif msg in ("rrcConnectionReconfiguration", "rrcConnectionRelease", "scgFailureInformationNR-r15",
                         "rrcConnectionReestablishmentRequest", "ueInformationResponse-r9", "rrcConnectionRequest"):
                txt = t.to_asn1()
            if full and not txt:
                txt = t.to_asn1()
        elif cls == 2:
            # ID RRC belum dikenal: coba semua kanal LTE RRC, ambil yang encode-ulangnya identik
            proto = "RRC?"
            for tn, dd in (("UL", "UL"), ("DL", "DL"), ("ULC", "UL"), ("DLC", "DL")):
                t = T[tn]
                try:
                    t.from_uper(payload)
                    if t.to_uper().rstrip(b"\x00") != payload.rstrip(b"\x00"):
                        continue
                except Exception:
                    continue
                RRC_MID[mid] = (tn, dd)
                return decode_record(cls, mid, payload, T, full)
        elif cls in (0, 1):
            t = T["S1"] if cls == 0 else T["X2"]
            proto = "S1AP" if cls == 0 else "X2AP"
            t.from_aper(payload)
            v = t.get_val()
            proto = "S1AP" if cls == 0 else "X2AP"
            msg = v[1]["value"][0]
            if isinstance(v[1]["value"][1], bytes) or msg.startswith("_unk"):
                msg = f"proc{v[1]['procedureCode']} (vendor)"   # prosedur di luar 3GPP, IE tetap terbaca
            txt = t.to_asn1()
            cs = re.findall(r"[Cc]ause:? (\S+ : \S+)", txt)   # Cause IE, juga cause per E-RAB di dalam list
            rab = re.findall(r"e-RAB-ID (\d+)", txt)
            qci = re.findall(r"qCI (\d+)", txt)
            if rab and msg.startswith("E-RAB"):
                info = f"E-RAB {','.join(rab)}" + (f" QCI{','.join(qci)}" if qci else "") + " "
            if cs:
                info += "Cause=" + cs[0].replace(" : ", ":")
            g = re.findall(r"gNB-ID : '([0-9A-F]+)'H", txt)
            if g:
                info += f" targetGNB={int(g[0], 16)}"
            m = re.search(r"cell-ID '([0-9A-F]+)'H", txt) if msg == "InitialUEMessage" else None
            if m:
                info += f" CGI={m.group(1)}"
            if cls == 0:
                nm = re.search(r"NAS-PDU ?: '([0-9A-F]+)'H", txt)
                if nm:
                    nname, ntxt = decode_nas(bytes.fromhex(nm.group(1)), msg in S1_MME_TO_ENB, full)
                    info = (info + " " if info else "") + f"NAS={nname.replace('EMM', '').replace('ESM', 'ESM:')}"
                    if full and ntxt:
                        txt += "\n\n----- NAS (decode) -----\n" + ntxt
            if msg == "RRCTransfer":
                m = re.search(r"uENRMeasurements '([0-9A-F]+)'H", txt)
                if m:
                    msg = "RRCTransfer(NR MR)"
                    try:
                        info, measid = _mr_nr(bytes.fromhex(m.group(1)), T)
                    except Exception:
                        info = "NR meas (tidak ter-decode)"
    except Exception as e:
        info = "DECODE_ERR " + str(e)[:80]
    if txt and proto == "RRC":
        if msg == "rrcConnectionReconfiguration":
            tags = []
            if "mobilityControlInfo" in txt:
                m = re.search(r"targetPhysCellId (\d+)", txt)
                f = re.search(r"dl-CarrierFreq (\d+)", txt)
                tags.append(f"HO cmd -> PCI{m.group(1) if m else '?'}" + (f" EARFCN{f.group(1)}" if f else ""))
            m = re.search(r"nr-Config-r15 (setup|release)", txt)
            if m:
                tags.append("nr-Config " + m.group(1))
            if "measConfig" in txt:
                tags.append("measConfig")
            if "nr-SecondaryCellGroupConfig" in txt:
                tags.append("SCG config")
            info = "; ".join(tags)
        elif msg == "rrcConnectionRelease":
            m = re.search(r"releaseCause (\S+)", txt)
            pr = re.findall(r"carrierFreq (\d+),\s+cellReselectionPriority (\d+)", txt)
            info = "releaseCause=" + (m.group(1) if m else "?") + ("".join(f" prio{f}={p}" for f, p in pr) if pr else "")
        elif msg == "scgFailureInformationNR-r15":
            m = re.search(r"failureType-r15 (\S+)", txt)
            info = "failureType=" + (m.group(1) if m else "?")
        elif msg == "rrcConnectionReestablishmentRequest":
            m = re.search(r"reestablishmentCause (\S+)", txt)
            p = re.search(r"physCellId (\d+)", txt)
            info = f"cause={m.group(1) if m else '?'} srcPCI={p.group(1) if p else '?'}"
        elif msg == "rrcConnectionRequest":
            m = re.search(r"establishmentCause (\S+)", txt)
            info = "establishmentCause=" + (m.group(1) if m else "?")
        elif msg == "ueInformationResponse-r9" and "rlf-Report" in txt:
            info = "RLF report"
    if full:
        return proto, msg, d, info, measid, txt
    return proto, msg, d, info, measid


# --- measConfig: measId -> event (A1..A6, B1/B2, periodik) per koneksi ---
def _thr(t):
    """('threshold-RSRP', x) dsb. -> teks dBm/dB."""
    if not isinstance(t, tuple):
        return str(t)
    k, x = t
    if k == "threshold-RSRP":
        return f"{x - 140}dBm"
    if k == "threshold-RSRQ":
        return f"{x / 2 - 20:g}dB"
    if k.startswith("nr-RSRP"):
        return f"{x - 156}dBm"
    if k.startswith("nr-RSRQ"):
        return f"{x / 2 - 43:g}dB"
    if k.startswith("nr-SINR"):
        return f"{x / 2 - 23:g}dB"
    return f"{k}={x}"


def report_config_label(rc) -> str:
    kind, body = rc
    trig = body.get("triggerType")
    if not trig:
        return kind
    if trig[0] == "periodical":
        return "Periodic " + str(trig[1].get("purpose", ""))
    ev = trig[1]
    eid, p = ev["eventId"]
    name = eid.replace("event", "").replace("-r15", "").replace("-r10", "").replace("-NR", "-NR")
    parts = [name]
    for key, val in p.items():
        if "Offset" in key:
            parts.append(f"off{val / 2:+g}dB")
        elif "Threshold" in key:
            nm = "thr" + ("1" if key.endswith("Threshold1") else "2" if key.endswith("Threshold2") else "")
            parts.append(f"{nm} {_thr(val)}")
    if ev.get("hysteresis"):
        parts.append(f"hys{ev['hysteresis'] / 2:g}dB")
    if ev.get("timeToTrigger"):
        parts.append("TTT" + str(ev["timeToTrigger"]).replace("ms", ""))
    return " ".join(parts)


def apply_meas_config(state: dict, mc: dict):
    objs, rcs, mids = state.setdefault("obj", {}), state.setdefault("rc", {}), state.setdefault("mid", {})
    for i in mc.get("measObjectToRemoveList", []) or []:
        objs.pop(i, None)
    for i in mc.get("reportConfigToRemoveList", []) or []:
        rcs.pop(i, None)
    for i in mc.get("measIdToRemoveList", []) or []:
        mids.pop(i, None)
    for o in mc.get("measObjectToAddModList", []) or []:
        kind, body = o["measObject"]
        f = body.get("carrierFreq", body.get("carrierFreq-r15"))
        objs[o["measObjectId"]] = ("NR " if "NR" in kind else "") + str(f)
    for r in mc.get("reportConfigToAddModList", []) or []:
        try:
            rcs[r["reportConfigId"]] = report_config_label(r["reportConfig"])
        except Exception:
            rcs[r["reportConfigId"]] = "?"
    for m in mc.get("measIdToAddModList", []) or []:
        mids[m["measId"]] = (m["measObjectId"], m["reportConfigId"])


def meas_label(state: dict, measid) -> str | None:
    if not state or measid is None:
        return None
    om = state.get("mid", {}).get(measid)
    if not om:
        return None
    ev = state.get("rc", {}).get(om[1], "?")
    fr = state.get("obj", {}).get(om[0])
    return ev + (f" @{fr}" if fr else "")


def tag_meas_events(recs: list, T: dict):
    """Isi kolom meas_event pada measurement report LTE berdasarkan measConfig terakhir di koneksi yang sama."""
    states, pending = {}, None
    for r in recs:
        r["meas_event"] = None
        if r.get("proto") != "RRC":
            continue
        key = (r["enb"], r["cell"], r["rnti"])
        if r["msg"] == "rrcConnectionReconfiguration":
            try:
                t = T[RRC_MID.get(r["mid"], ("DL", "DL"))[0]]
                t.from_uper(bytes.fromhex(r["payload_hex"]))
                v = t.get_val()
            except Exception:
                continue
            mc = _find(v, "measConfig", [])
            ho = bool(_find(v, "mobilityControlInfo", []))
            st_ = states.setdefault(key, {})
            if ho:   # measConfig di HO command berlaku di cell tujuan
                import copy
                pending = copy.deepcopy(st_)
                for m in mc:
                    apply_meas_config(pending, m)
            else:
                for m in mc:
                    apply_meas_config(st_, m)
            if mc:
                added = [m2["measId"] for m in mc for m2 in (m.get("measIdToAddModList") or [])]
                lab = [f"id{i}={meas_label(pending if ho else st_, i)}" for i in added]
                if lab:
                    r["info"] = (r["info"] + "; " if r["info"] else "") + "measId: " + ", ".join(lab)
        elif r["msg"] == "measurementReport":
            if key not in states and pending is not None:
                states[key], pending = pending, None
            lab = meas_label(states.get(key), r.get("meas_id"))
            if lab:
                r["meas_event"] = lab
                r["info"] = f"[{lab}] " + (r["info"] or "")


def trace_cache_path(path: str, include_internal: bool) -> str:
    p = os.path.abspath(path.strip().strip('"'))
    if os.path.isdir(p):
        sig = ";".join(f"{f}:{os.path.getsize(os.path.join(r, f))}:{os.path.getmtime(os.path.join(r, f))}"
                       for r, _, fs in os.walk(p) for f in sorted(fs) if f.lower().endswith(".stm"))
    else:
        sig = f"{os.path.getsize(p)}:{os.path.getmtime(p)}"
    key = hashlib.md5(f"{TRACE_CACHE_VER}|{p}|{sig}|{include_internal}".encode()).hexdigest()[:16]
    return os.path.join(TRACE_CACHE, f"{key}.parquet")


def load_trace(con, path: str, table: str = "trace", include_internal: bool = False, progress=None) -> dict:
    """Parse + decode trace, simpan cache Parquet, lalu buat tabel DuckDB. Return ringkasan."""
    cache = trace_cache_path(path, include_internal)
    from_cache = os.path.exists(cache)
    if not from_cache:
        T = asn1_types()
        recs = []
        blobs = list(stm_blobs(path))
        if not blobs:
            raise ValueError("Tidak ada file .stm di path ini.")
        for i, (name, d) in enumerate(blobs):
            recs.extend(scan_stm(name, d, include_internal))
            if progress:
                progress(0.15 * (i + 1) / len(blobs), f"Membaca {name} ({i + 1}/{len(blobs)})")
        if not recs:
            diag = []
            for name, d in blobs[:3]:
                diag.append(f"- {name}: {len(d):,} byte, marker aa55xx06 = {len(ZTE_MARK_RE.findall(d)):,}x, "
                            f"64 byte awal: {d[:64].hex(' ')}")
            raise ValueError("File terbaca, tapi tidak ada record trace ZTE di dalamnya.\n\n"
                             f"Jumlah file .stm: {len(blobs)}\n" + "\n".join(diag) +
                             "\n\nKirim info ini (atau file-nya) supaya formatnya bisa dicocokkan.")
        recs.sort(key=lambda r: r["time"])
        n = len(recs)
        step = max(1, n // 200)
        for i, r in enumerate(recs):
            if r["cls"] in (0, 1, 2):
                r["proto"], r["msg"], r["dir"], r["info"], r["meas_id"] = decode_record(r["cls"], r["mid"], r["payload"], T)
            else:
                r["proto"], r["msg"], r["dir"], r["info"], r["meas_id"] = "ZTE-internal", f"0x{r['mid']:04x}", "", "", None
            r["payload_hex"] = r.pop("payload").hex()
            if progress and i % step == 0:
                progress(0.15 + 0.8 * i / n, f"Decode {i:,}/{n:,} pesan")
        if progress:
            progress(0.96, "Memetakan measId ke event (A3/A5/B1...)")
        tag_meas_events(recs, T)
        df_t = pd.DataFrame(recs)
        os.makedirs(TRACE_CACHE, exist_ok=True)
        con.register("__trace_df", df_t)
        try:
            con.execute(f"COPY (SELECT * FROM __trace_df) TO '{sql_str(cache)}' (FORMAT parquet, COMPRESSION zstd)")
        finally:
            con.unregister("__trace_df")
    # tabel trace + kolom angka siap pakai untuk chart
    for (ttype,) in con.execute("SELECT table_type FROM information_schema.tables "
                                "WHERE table_schema='main' AND table_name=?", [table]).fetchall():
        con.execute(f'DROP {"VIEW" if ttype == "VIEW" else "TABLE"} "{table}"')
    con.execute(f"""
        CREATE TABLE "{table}" AS
        SELECT row_number() OVER (ORDER BY time, file, "offset") AS no,
               time, proto, dir, msg, meas_event, info, meas_id, enb, cell, rnti, gnb,
               TRY_CAST(regexp_extract(info, 'PCell RSRP=(-?[0-9.]+)', 1) AS DOUBLE) AS pcell_rsrp,
               TRY_CAST(regexp_extract(info, 'PCell RSRP=-?[0-9.]+ RSRQ=(-?[0-9.]+)', 1) AS DOUBLE) AS pcell_rsrq,
               TRY_CAST(regexp_extract(info, '(?:SCG|serv) PCI[0-9]+ RSRP=(-?[0-9.]+)', 1) AS DOUBLE) AS nr_rsrp,
               TRY_CAST(regexp_extract(info, '(?:SCG|serv) PCI[0-9]+ RSRP=-?[0-9.na]+ SINR=(-?[0-9.]+)', 1) AS DOUBLE) AS nr_sinr,
               cls, mid, file, "offset", hdr, payload_hex
        FROM read_parquet('{sql_str(cache)}')""")
    s = con.execute(f'SELECT COUNT(*), MIN(time), MAX(time), COUNT(DISTINCT (enb, cell)), '
                    f"COUNT(*) FILTER (WHERE info LIKE 'DECODE_ERR%') FROM \"{table}\"").fetchone()
    return dict(rows=s[0], start=s[1], end=s[2], cells=s[3], errors=s[4], from_cache=from_cache, cache=cache)


# --- kategori event & arah panah untuk diagram ladder ---
KEY_EVENT_SQL = """(
  msg IN ('rrcConnectionReestablishmentRequest','rrcConnectionRelease','rrcConnectionRequest',
          'scgFailureInformationNR-r15','UEContextReleaseRequest','UEContextReleaseCommand','InitialUEMessage',
          'PathSwitchRequest','HandoverRequired','HandoverRequest','SgNBReleaseRequired','SgNBReleaseRequest',
          'SgNBAdditionRequest','SgNBChangeRequired','SgNBModificationRequired')
  OR info LIKE 'HO cmd%' OR info LIKE '%RLF report%' OR info LIKE 'Cause=%' OR info LIKE 'DECODE_ERR%')"""
MEAS_SQL = "(msg IN ('measurementReport', 'RRCTransfer(NR MR)', 'ulInformationTransferMRDC-r15'))"
MOBILITY_SQL = ("(info LIKE 'HO cmd%' OR msg IN ('rrcConnectionReestablishmentRequest','PathSwitchRequest',"
                "'HandoverRequired','HandoverRequest','HandoverCommand','HandoverNotify','SgNBChangeRequired',"
                "'SgNBAdditionRequest','SgNBReleaseRequired','SgNBReleaseRequest','scgFailureInformationNR-r15'))")

S1_MME_TO_ENB = {"DownlinkNASTransport", "InitialContextSetupRequest", "UEContextReleaseCommand", "E-RABSetupRequest",
                 "E-RABModifyRequest", "E-RABReleaseCommand", "UEContextModificationRequest",
                 "PathSwitchRequestAcknowledge", "PathSwitchRequestFailure", "HandoverCommand",
                 "HandoverPreparationFailure", "HandoverRequest", "MMEStatusTransfer", "Paging",
                 "E-RABModificationConfirm", "ConnectionEstablishmentIndication", "HandoverCancelAcknowledge",
                 "DownlinkUEAssociatedLPPaTransport", "TraceStart", "DeactivateTrace", "LocationReportingControl",
                 "UERadioCapabilityMatchRequest", "DownlinkS1cdma2000tunnelling"}
X2_TO_GNB = {"SgNBAdditionRequest", "SgNBReconfigurationComplete", "SgNBModificationRequest", "SgNBReleaseRequest",
             "SgNBModificationConfirm", "SgNBReleaseConfirm", "SgNBChangeConfirm", "RRCTransfer(NR MR)",
             "UEContextRelease", "SgNBCounterCheckRequest", "HandoverRequest", "SNStatusTransfer"}


def ladder_ends(proto: str, d: str, msg: str):
    """(dari, ke) di antara lajur UE, eNB, MME, gNB."""
    if proto == "RRC":
        return ("UE", "eNB") if d == "UL" else ("eNB", "UE")
    if proto == "S1AP":
        return ("MME", "eNB") if msg in S1_MME_TO_ENB else ("eNB", "MME")
    if proto == "X2AP":
        return ("eNB", "gNB") if msg in X2_TO_GNB else ("gNB", "eNB")
    return ("eNB", "eNB")


def ladder_svg(rows: list[dict]) -> str:
    lanes = ["UE", "eNB", "MME", "gNB"]
    names = {"UE": "UE / CPE", "eNB": "eNB (MeNB)", "MME": "MME", "gNB": "SgNB / X2 peer"}
    color = {"RRC": "#2a78d6", "S1AP": "#eb6834", "X2AP": "#1baf7a"}
    W, x0, gap, top, rh = 1100, 170, 270, 70, 46
    xs = {k: x0 + i * gap for i, k in enumerate(lanes)}
    H = top + rh * len(rows) + 30
    esc = lambda s: (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         f'style="font-family:Inter,DejaVu Sans,sans-serif;background:#fff">',
         '<defs>' + "".join(f'<marker id="a{k}" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto">'
                            f'<path d="M0,0 L10,4 L0,8 Z" fill="{c}"/></marker>' for k, c in color.items())
         + '<marker id="aX" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto">'
           '<path d="M0,0 L10,4 L0,8 Z" fill="#888"/></marker></defs>']
    for k in lanes:
        o.append(f'<rect x="{xs[k] - 70}" y="14" width="140" height="34" rx="8" fill="#1c2533"/>'
                 f'<text x="{xs[k]}" y="36" text-anchor="middle" font-size="14" font-weight="700" fill="#fff">{names[k]}</text>'
                 f'<line x1="{xs[k]}" y1="48" x2="{xs[k]}" y2="{H - 10}" stroke="#c9ced6" stroke-width="2" stroke-dasharray="4 4"/>')
    for i, r in enumerate(rows):
        y = top + rh * i + 22
        a, b = ladder_ends(r["proto"], r.get("dir") or "", r["msg"])
        c = color.get(r["proto"], "#888")
        mk = r["proto"] if r["proto"] in color else "X"
        key = bool(r.get("key"))
        o.append(f'<text x="12" y="{y + 4}" font-size="12" fill="#5a6372">{esc(r["time"].strftime("%H:%M:%S.%f")[:-3])}</text>')
        if a == b:
            o.append(f'<circle cx="{xs[a]}" cy="{y}" r="5" fill="{c}"/>')
            x1, x2 = xs[a], xs[a]
        else:
            x1, x2 = xs[a], xs[b]
            o.append(f'<line x1="{x1}" y1="{y}" x2="{x2 + (-8 if x2 > x1 else 8)}" y2="{y}" stroke="{c}" '
                     f'stroke-width="{3 if key else 2}" marker-end="url(#a{mk})"/>')
        label = r["msg"]
        mx = (x1 + x2) / 2
        o.append(f'<text x="{mx}" y="{y - 7}" text-anchor="middle" font-size="13" font-weight="{700 if key else 500}" '
                 f'fill="{"#b42318" if key else "#1c2533"}">{esc(label)}</text>')
        if r.get("info"):
            o.append(f'<text x="{mx}" y="{y + 17}" text-anchor="middle" font-size="11" fill="#5a6372">'
                     f'{esc(str(r["info"])[:70])}</text>')
    o.append("</svg>")
    return "".join(o)


with st.sidebar:
    st.header("🦆 Koneksi")
    db_path = st.text_input("File database (.duckdb), kosong = in-memory", "")
    con = get_db(db_path).cursor()
    scache = schema_cache(db_path)

    st.divider()
    st.subheader("Register file sebagai view")
    fpath = st.text_input("Path file / glob", placeholder=r"D:\data\cpe_hourly_*.csv")
    vname = st.text_input("Nama view", "data")
    c1, c2 = st.columns(2)
    delim = c1.text_input("Delimiter (auto=kosong)", "")
    all_varchar = c2.checkbox("Semua kolom teks", help="Pakai kalau auto-detect tipe gagal")
    as_table = st.checkbox(
        "Import jadi tabel (query jauh lebih cepat)",
        value=True,
        help="Baca file sekali ke DuckDB. Proses awal agak lama, tapi query berikutnya 10-100x lebih cepat. "
             "Matikan kalau file terlalu besar untuk RAM/disk, atau kalau file sering berubah.",
    )
    sheet = st.text_input("Sheet (khusus Excel, kosong = sheet pertama)", "")
    st.text_input("Anggap kosong / NULL (pisah koma)", NULL_DEFAULT, key="nullstr_cfg",
                  help="Nilai di CSV yang dianggap sel kosong, mis. error Excel #N/A. Berlaku juga di tab File.")
    if st.button("Register", width="stretch") and fpath:
        try:
            t0 = time.time()
            with st.spinner("Membaca file..."):
                kind = register_file(con, fpath, vname, delim, all_varchar, as_table, sheet)
            scache.clear()
            st.toast(f"{kind} '{vname}' siap ({time.time()-t0:.1f} detik)", icon="✅")
            note = st.session_state.pop("reg_note", None)
            if note:
                st.toast(note, icon="ℹ️")
        except Exception as e:
            st.error(str(e))

    st.divider()
    oc1, oc2 = st.columns([3, 1])
    oc1.subheader("Objek")
    if oc2.button("↻", help="Refresh daftar kolom"):
        scache.clear()
    try:
        objs = con.execute(
            "SELECT table_name, table_type FROM information_schema.tables "
            "WHERE table_schema='main' ORDER BY table_name"
        ).fetchall()
        for name, ttype in objs:
            icon = "👁" if ttype == "VIEW" else "📋"
            with st.expander(f"{icon} {name}"):
                if name not in scache:
                    scache[name] = con.execute(f'DESCRIBE "{name}"').fetchall()
                st.code("\n".join(f"{c[0]}  {c[1]}" for c in scache[name]), language=None)
        if not objs:
            st.caption("Belum ada tabel/view.")
    except Exception as e:
        st.error(str(e))

def clean_sql(q: str) -> str:
    """Buang ';' dan komentar '--' di ujung statement."""
    while True:
        q2 = re.sub(r"--[^\n']*$", "", q.rstrip()).rstrip().rstrip(";").rstrip()
        if q2 == q:
            return q
        q = q2

tab_query, tab_chart, tab_trace, tab_file, tab_docs = st.tabs(
    ["🧮 Query", "📊 Chart", "📡 Trace", "📁 File", "📖 Docs SQL"])

# ================= TAB QUERY =================
with tab_query:
    default_sql = st.session_state.get("load_sql", "SELECT *\nFROM data\nLIMIT 100;")
    sql = st.text_area("SQL", default_sql, height=220, label_visibility="collapsed")
    st.session_state.cur_sql = sql

    c1, c2, c3 = st.columns([1, 1, 4])
    run = c1.button("▶ Run", type="primary", width="stretch")
    preview_n = c2.number_input("Max baris preview", 100, 1_000_000, 10_000, step=1000)

    if run and sql.strip():
        t0 = time.time()
        st.session_state.chart_cache = {}
        try:
          with st.spinner("Menjalankan query..."):
              # parser DuckDB: aman untuk ';' di dalam string/komentar
              stmts = [st_.query.strip() for st_ in con.extract_statements(sql)]
              if not stmts:
                  raise ValueError("Tidak ada statement SQL.")
              # jalankan semua statement kecuali terakhir (DDL/SET dll)
              for s in stmts[:-1]:
                  con.execute(s)
              last = clean_sql(stmts[-1])
              if re.match(r"^\s*(select|with|from|describe|show|summarize|pivot|unpivot|values|table)\b", last, re.I):
                  df = con.execute(f"SELECT * FROM ({last}) LIMIT {int(preview_n)}").df()
                  st.session_state.result = df
              elif re.match(r"^\s*explain\b", last, re.I):
                  st.session_state.result = con.execute(last).df()
              else:
                  con.execute(last)
                  scache.clear()  # tabel/view mungkin berubah
                  st.session_state.result = None
                  st.success("Statement berhasil dijalankan.")
              st.session_state.elapsed = time.time() - t0
              st.session_state.last_sql = last
              st.session_state.history.insert(0, sql)
              st.session_state.history = st.session_state.history[:30]
          res = st.session_state.result
          st.toast(f"Selesai dalam {st.session_state.elapsed:.2f} detik"
                   + (f" • {len(res):,} baris" if res is not None else ""), icon="⚡")
        except Exception as e:
            st.session_state.result = None
            st.error(str(e))
            if "Conversion Error" in str(e) and "CSV" in str(e):
                st.info("💡 Ada nilai di CSV yang tidak cocok dengan tipe kolom hasil tebakan. Cara cepat: "
                        "register ulang file ini dengan **Import jadi tabel** aktif (tipe otomatis dideteksi "
                        "ulang dari seluruh file), atau tambahkan nilai itu ke field **Anggap kosong / NULL**.")

    # ---------- Hasil ----------
    df = st.session_state.get("result")
    if df is not None:
        st.caption(
            f"{len(df):,} baris ditampilkan (maks {int(preview_n):,}) • "
            f"{st.session_state.get('elapsed', 0):.2f} detik"
        )
        st.dataframe(df, width="stretch", height=450)

        with st.expander("💾 Export hasil PENUH (tanpa limit preview)"):
            e1, e2 = st.columns([3, 1])
            out = e1.text_input("Simpan ke", r"D:\output\hasil.csv")
            fmt = e2.selectbox("Format", ["csv", "parquet", "xlsx"])
            if st.button("Export"):
                folder = os.path.dirname(os.path.abspath(out.strip().strip('"')))
                if folder and not os.path.isdir(folder):
                    try:
                        os.makedirs(folder, exist_ok=True)  # buat folder tujuan kalau belum ada
                    except Exception as ex:
                        st.error(f"Tidak bisa membuat folder {folder}: {ex}")
                o = out.strip().strip('"').replace("\\", "/").replace("'", "''")
                q = st.session_state.last_sql
                t0 = time.time()
                try:
                    if fmt == "csv":
                        con.execute(f"COPY ({q}) TO '{o}' (HEADER, DELIMITER ',')")
                    elif fmt == "parquet":
                        con.execute(f"COPY ({q}) TO '{o}' (FORMAT parquet, COMPRESSION zstd)")
                    else:
                        con.execute("INSTALL excel; LOAD excel;")
                        con.execute(f"COPY ({q}) TO '{o}' WITH (FORMAT xlsx, HEADER true)")
                    st.success(f"Tersimpan: {out} ({time.time()-t0:.1f} detik)")
                except Exception as e:
                    st.error(str(e))

    # ---------- History ----------
    if st.session_state.history:
        with st.expander("🕘 Riwayat query"):
            for i, h in enumerate(st.session_state.history):
                hc1, hc2 = st.columns([6, 1])
                hc1.code(h, language="sql")
                if hc2.button("Load", key=f"h{i}"):
                    st.session_state.load_sql = h
                    st.rerun()


# ================= TAB CHART =================
def qi(c) -> str:
    """Quote nama kolom untuk SQL."""
    return '"' + str(c).replace('"', '""') + '"'


NUM_RE = re.compile(r"^(TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT|U\w*INT|FLOAT|DOUBLE|REAL|DECIMAL)", re.I)
TIME_RE = re.compile(r"^(DATE|TIMESTAMP)", re.I)
BUCKETS = {"Otomatis": "auto", "Asli": None, "Menit": "minute", "Jam": "hour", "Hari": "day", "Minggu": "week", "Bulan": "month"}
BUCKET_FMT = {None: "%Y-%m-%d %H:%M", "minute": "%Y-%m-%d %H:%M", "hour": "%Y-%m-%d %H:00",
              "day": "%Y-%m-%d", "week": "%Y-%m-%d", "month": "%Y-%m"}
AGGS = ["SUM", "AVG", "MEDIAN", "MIN", "MAX", "COUNT", "Tanpa agregasi"]


def chart_query(sql: str) -> pd.DataFrame:
    """Jalankan query chart di hasil PENUH, dengan cache supaya ganti opsi tampilan tidak query ulang."""
    cache = st.session_state.setdefault("chart_cache", {})
    if sql not in cache:
        cache[sql] = con.execute(sql).df()
    return cache[sql]


def series_expr(src: str, scol: str, rank: str, topn: int) -> str:
    """Kolom seri; kalau jumlah seri > topn, sisanya digabung jadi 'Lainnya' (palet maks 8 warna)."""
    s = f"COALESCE(CAST({qi(scol)} AS VARCHAR), '(kosong)')"
    return (
        f"CASE WHEN {s} IN (SELECT {s} FROM {src} GROUP BY 1 ORDER BY {rank} DESC LIMIT {topn - 1}) "
        f"OR (SELECT COUNT(DISTINCT {s}) FROM {src}) <= {topn} THEN {s} ELSE 'Lainnya' END"
    )


def color_enc(df: pd.DataFrame, title: str | None):
    doms = sorted(df["__s"].astype(str).unique(), key=lambda v: (v == "Lainnya", v))
    if len(doms) <= 1:
        return alt.value(PALETTE[0]), doms
    rng, k = [], 0
    for d in doms:  # 'Lainnya' selalu abu-abu netral, seri lain ikut urutan palet
        if d == "Lainnya":
            rng.append("#9a9a96")
        else:
            rng.append(PALETTE[k % len(PALETTE)])
            k += 1
    return alt.Color("__s:N", title=title, scale=alt.Scale(domain=doms, range=rng),
                     legend=alt.Legend(orient="top", symbolType="circle")), doms


with tab_chart:
    q = st.session_state.get("last_sql")
    preview = st.session_state.get("result")
    if not q or preview is None or re.match(r"^\s*explain\b", q, re.I):
        st.info("Jalankan query SELECT dulu di tab **Query**. Chart dibuat dari hasil query terakhir "
                "(dihitung dari hasil penuh, bukan cuma preview).")
    else:
        try:
            types = {r[0]: r[1] for r in con.execute(f"DESCRIBE ({q})").fetchall()}
        except Exception:
            # cadangan: tebak tipe dari preview pandas
            types = {}
            for c_ in preview.columns:
                dt = preview[c_].dtype
                types[c_] = ("DOUBLE" if pd.api.types.is_numeric_dtype(dt) else
                             "TIMESTAMP" if pd.api.types.is_datetime64_any_dtype(dt) else "VARCHAR")
        cols = list(types)
        num_cols = [c for c in cols if NUM_RE.match(types[c])]
        time_cols = [c for c in cols if TIME_RE.match(types[c])]
        src = f"({q})"
        # kolom teks yang isinya angka (mis. CSV "Semua kolom teks" atau desimal koma) tetap bisa jadi Y
        text_cols = [c for c in cols if c not in num_cols and c not in time_cols]
        text_num, text_time = [], []
        if text_cols:
            parts = ", ".join(
                f"COUNT(TRY_CAST(REPLACE(TRIM(CAST({qi(c)} AS VARCHAR)), ',', '.') AS DOUBLE)) = COUNT({qi(c)}) "
                f"AND COUNT({qi(c)}) > 0 AS {qi('n_' + c)}, "
                f"COUNT(TRY_CAST({qi(c)} AS TIMESTAMP)) = COUNT({qi(c)}) AND COUNT({qi(c)}) > 0 AS {qi('t_' + c)}"
                for c in text_cols)
            try:
                chk = chart_query(f"SELECT {parts} FROM (SELECT * FROM {src} LIMIT 1000)")
                text_num = [c for c in text_cols if bool(chk["n_" + c][0])]
                text_time = [c for c in text_cols if bool(chk["t_" + c][0]) and c not in text_num]
            except Exception:
                text_num, text_time = [], []
        y_opts = num_cols + text_num + [c for c in text_cols if c not in text_num]

        def fmt_y(c):
            if c in num_cols:
                return c
            return f"{c} (teks→angka)" if c in text_num else f"{c} (teks)"

        def num_expr(c):
            if c in num_cols:
                return qi(c)
            return f"TRY_CAST(REPLACE(TRIM(CAST({qi(c)} AS VARCHAR)), ',', '.') AS DOUBLE)"

        def time_expr(c):
            """Kolom waktu; kolom teks berisi tanggal juga dikenali. None kalau bukan waktu."""
            if c in time_cols:
                return qi(c)
            if c in num_cols:
                return None
            chk = chart_query(f"SELECT COUNT(TRY_CAST({qi(c)} AS TIMESTAMP)) AS ok, COUNT({qi(c)}) AS n "
                              f"FROM (SELECT {qi(c)} FROM {src} LIMIT 1000)")
            ok, n = int(chk["ok"][0]), int(chk["n"][0])
            return f"TRY_CAST({qi(c)} AS TIMESTAMP)" if n and ok == n else None

        ctype = st.selectbox("Tipe chart", ["Line", "Bar", "Area", "Scatter", "Histogram", "Pie / Donut"])

        with st.form("chart_form", border=True):
            r = st.columns([2, 1])
            title = r[0].text_input("Judul chart (opsional)", "")
            height = r[1].slider("Tinggi (px)", 250, 800, 420, 10)
            ydef = (num_cols + text_num)[:1]
            if ctype in ("Line", "Bar", "Area"):
                c = st.columns(4)
                xdef = (time_cols + text_time)[:1]
                x = c[0].selectbox("Sumbu X", cols, index=cols.index(xdef[0]) if xdef else 0)
                agg = c[1].selectbox("Agregasi", AGGS, index=0 if ctype != "Line" else 1)
                ys = c[2].multiselect("Nilai Y", y_opts, default=ydef, format_func=fmt_y,
                                      help="Tidak dipakai kalau Agregasi = COUNT. Kolom teks berisi angka "
                                           "otomatis dikonversi (desimal koma juga).")
                scol = c[3].selectbox("Seri / warna (opsional)", ["—"] + cols,
                                      help="Dipakai kalau Nilai Y cuma satu kolom")
                c = st.columns(4)
                bucket_lbl = c[0].selectbox("Kelompokkan waktu", list(BUCKETS),
                                            help="Berlaku kalau Sumbu X berupa tanggal/waktu")
                mode, horizontal = "", False
                if ctype == "Bar":
                    mode = c[1].radio("Mode bar", ["Stacked", "Grouped", "100%"], horizontal=True)
                    horizontal = c[2].toggle("Bar horizontal")
                elif ctype == "Area":
                    mode = c[1].radio("Mode area", ["Stacked", "100%", "Overlap"], horizontal=True)
                topn = c[3].slider("Maks seri (sisanya → Lainnya)", 2, 8, 8)
                xcat_n = st.slider("Maks kategori X (kalau X teks, urut dari nilai terbesar)", 5, 100, 30) \
                    if ctype == "Bar" else None
            elif ctype == "Scatter":
                c = st.columns(4)
                x = c[0].selectbox("Sumbu X", y_opts, index=0, format_func=fmt_y)
                y = c[1].selectbox("Sumbu Y", y_opts, index=min(1, len(y_opts) - 1), format_func=fmt_y)
                scol = c[2].selectbox("Seri / warna (opsional)", ["—"] + cols)
                nmax = c[3].slider("Maks titik (sampel acak)", 1000, 50000, 5000, 1000)
            elif ctype == "Histogram":
                c = st.columns(4)
                x = c[0].selectbox("Kolom", y_opts, format_func=fmt_y)
                nb = c[1].slider("Jumlah bin", 5, 100, 30)
            else:
                c = st.columns(4)
                cat = c[0].selectbox("Kategori", cols)
                agg = c[1].selectbox("Agregasi", ["COUNT", "SUM", "AVG"])
                val = c[2].selectbox("Nilai (untuk SUM/AVG)", y_opts, format_func=fmt_y) if y_opts else None
                donut = c[3].toggle("Donut", value=True)
                topn = st.slider("Maks irisan (sisanya → Lainnya)", 2, 8, 6)
            go = st.form_submit_button("▶ Buat chart", type="primary")

        if go:
            st.session_state.chart_on = (q, ctype)
        if st.session_state.get("chart_on") != (q, ctype):
            st.caption("Atur opsi lalu klik **▶ Buat chart**. Chart tidak dihitung ulang sebelum tombol diklik.")
        else:
          with st.spinner("Menghitung chart..."):
            try:
                df = None
                # ---------- Line / Bar / Area ----------
                if ctype in ("Line", "Bar", "Area"):
                    if len(ys) > 1 or scol == x:
                        scol = "—"
                    scol = None if scol == "—" else scol
                    tx = time_expr(x)
                    is_time = tx is not None
                    bucket = BUCKETS[bucket_lbl] if is_time else None
                    if bucket == "auto":
                        # bucket terkecil yang menghasilkan <= target titik (bar 60, line/area 400)
                        target = 60 if ctype == "Bar" else 400
                        span = chart_query(f"SELECT epoch(MAX({tx})) - epoch(MIN({tx})) AS s FROM {src}")["s"][0]
                        span = float(span) if pd.notna(span) else 0.0
                        bucket = "month"
                        for b_, sec in (("minute", 60), ("hour", 3600), ("day", 86400), ("week", 604800)):
                            if span / sec <= target:
                                bucket = b_
                                break
                        st.caption(f"Waktu dikelompokkan otomatis per "
                                   f"{dict(minute='menit', hour='jam', day='hari', week='minggu', month='bulan')[bucket]}.")

                    if agg != "COUNT" and not ys:
                        st.warning("Pilih minimal satu kolom **Nilai Y** (atau pakai agregasi COUNT).")
                    else:
                        if is_time:
                            xexpr = f"date_trunc('{bucket}', {tx})" if bucket else tx
                        else:
                            xexpr = qi(x)
                        if agg == "COUNT":
                            meas = [("Jumlah", "COUNT(*)")]
                        elif agg == "Tanpa agregasi":
                            meas = [(y, num_expr(y)) for y in ys]
                        else:
                            meas = [(y, f"{agg}({num_expr(y)})") for y in ys]
                        rank = f"SUM(ABS({num_expr(ys[0])}))" if ys and agg != "COUNT" else "COUNT(*)"
                        sexp = series_expr(src, scol, rank, topn) if scol else "'__one'"
                        sel = ", ".join(f"{e} AS {qi(n)}" for n, e in meas)
                        if agg == "Tanpa agregasi":
                            sql_c = f"SELECT {xexpr} AS __x, {sexp} AS __s, {sel} FROM {src} ORDER BY 1 LIMIT 20000"
                        else:
                            sql_c = f"SELECT {xexpr} AS __x, {sexp} AS __s, {sel} FROM {src} GROUP BY 1, 2 ORDER BY 1"
                        wide = chart_query(sql_c)
                        df = wide.melt(id_vars=["__x", "__s"], var_name="__m", value_name="__v")
                        if not scol:
                            df["__s"] = df["__m"]
                        df = df[df["__x"].notna()]
                        nn = df["__v"].notna().sum()
                        if agg != "COUNT" and len(df) and nn == 0:
                            st.warning("Semua nilai Y kosong setelah dikonversi ke angka. "
                                       "Cek isi kolomnya di tab Query (mungkin bukan angka).")
                        xt = "T" if is_time and ctype != "Bar" else ("Q" if x in num_cols and ctype != "Bar" else
                                                                    ("O" if x in num_cols or is_time else "N"))
                        if xcat_n and xt == "N":
                            keep = df.groupby("__x")["__v"].sum().abs().nlargest(xcat_n).index
                            df = df[df["__x"].isin(keep)]
                        ytitle = (meas[0][0] if agg == "COUNT" else
                                  (f"{agg} {ys[0]}" if len(ys) == 1 and agg != "Tanpa agregasi" else ", ".join(ys)))
                        stitle = scol if scol else ("Kolom" if len(ys) > 1 else None)

                        color, doms = color_enc(df, stitle)
                        if ctype == "Bar" and is_time:
                            df["__x"] = pd.to_datetime(df["__x"]).dt.strftime(BUCKET_FMT[bucket])
                        xsort = None
                        if xt == "N" and ctype == "Bar":
                            xsort = list(df.groupby("__x")["__v"].sum().sort_values(ascending=False).index)
                        xaxis = alt.Axis(labelOverlap="greedy",
                                         labelAngle=0 if xt in ("T", "Q") or horizontal else -40, labelLimit=140)
                        tooltip = [alt.Tooltip("__x:" + xt, title=x)]
                        if len(doms) > 1:
                            tooltip.append(alt.Tooltip("__s:N", title=stitle))
                        tooltip.append(alt.Tooltip("__v:Q", title=ytitle, format=",.2f"))

                        base = alt.Chart(df)
                        if ctype == "Line":
                            X = alt.X(f"__x:{xt}", title=x, sort=xsort, axis=xaxis)
                            Y = alt.Y("__v:Q", title=ytitle, scale=alt.Scale(zero=False))
                            line = base.mark_line(strokeWidth=2, interpolate="monotone").encode(X, Y, color=color)
                            hover = alt.selection_point(nearest=True, on="pointerover", fields=["__x"], empty=False)
                            pts = base.mark_point(size=70, filled=True).encode(
                                X, Y, color=color, opacity=alt.condition(hover, alt.value(1), alt.value(0)),
                                tooltip=tooltip,
                            ).add_params(hover)
                            rule = base.mark_rule(color="#888", strokeDash=[4, 4]).encode(X).transform_filter(hover)
                            chart = alt.layer(line, rule, pts)
                        elif ctype == "Area":
                            stack = {"Stacked": "zero", "100%": "normalize", "Overlap": None}[mode]
                            X = alt.X(f"__x:{xt}", title=x, sort=xsort, axis=xaxis)
                            Y = alt.Y("__v:Q", title=ytitle + (" (%)" if stack == "normalize" else ""), stack=stack,
                                      axis=alt.Axis(format="%") if stack == "normalize" else alt.Axis())
                            area = base.mark_area(opacity=0.85 if stack else 0.35, interpolate="monotone",
                                                  line={"strokeWidth": 1.5}).encode(X, Y, color=color, order="__s:N")
                            hover = alt.selection_point(nearest=True, on="pointerover", fields=["__x"], empty=False)
                            sel_pts = base.mark_point(opacity=0, size=200).encode(X, tooltip=tooltip).add_params(hover)
                            rule = base.mark_rule(color="#888", strokeDash=[4, 4]).encode(X).transform_filter(hover)
                            chart = alt.layer(area, rule, sel_pts)
                        else:  # Bar
                            stack = {"Stacked": "zero", "Grouped": None, "100%": "normalize"}[mode]
                            cat_ = alt.X(f"__x:{xt}", title=x, sort=xsort, axis=xaxis) if not horizontal else \
                                alt.Y(f"__x:{xt}", title=x, sort=xsort, axis=xaxis)
                            valax = alt.Axis(format="%") if stack == "normalize" else alt.Axis()
                            vtitle = ytitle + (" (%)" if stack == "normalize" else "")
                            val_ = alt.Y("__v:Q", title=vtitle, stack=stack, axis=valax) if not horizontal else \
                                alt.X("__v:Q", title=vtitle, stack=stack, axis=valax)
                            rounded = stack is None or len(doms) <= 1
                            mark = base.mark_bar(cornerRadiusEnd=4 if rounded else 0, opacity=0.95)
                            enc = dict(color=color, tooltip=tooltip, order=alt.Order("__s:N"))
                            if stack is None and len(doms) > 1:
                                if horizontal:
                                    enc["yOffset"] = alt.YOffset("__s:N")
                                else:
                                    enc["xOffset"] = alt.XOffset("__s:N")
                            chart = mark.encode(cat_, val_, **enc)
                            if horizontal:
                                height = max(height, 22 * df["__x"].nunique())

                # ---------- Scatter ----------
                elif ctype == "Scatter":
                    scol = None if scol == "—" else scol
                    sexp = series_expr(src, scol, "COUNT(*)", 8) if scol else "'__one'"
                    ex, ey = num_expr(x), num_expr(y)
                    df = chart_query(
                        f"SELECT * FROM (SELECT {ex} AS __x, {ey} AS __y, {sexp} AS __s FROM {src}) "
                        f"WHERE __x IS NOT NULL AND __y IS NOT NULL USING SAMPLE {nmax} ROWS"
                    )
                    color, doms = color_enc(df, scol)
                    tooltip = [alt.Tooltip("__x:Q", title=x, format=",.2f"),
                               alt.Tooltip("__y:Q", title=y, format=",.2f")]
                    if len(doms) > 1:
                        tooltip.append(alt.Tooltip("__s:N", title=scol))
                    chart = alt.Chart(df).mark_circle(size=64, opacity=0.65, stroke="white", strokeWidth=0.5).encode(
                        alt.X("__x:Q", title=x, scale=alt.Scale(zero=False)),
                        alt.Y("__y:Q", title=y, scale=alt.Scale(zero=False)),
                        color=color, tooltip=tooltip,
                    ).interactive()

                # ---------- Histogram ----------
                elif ctype == "Histogram":
                    col = num_expr(x)
                    df = chart_query(f"""
                        WITH d AS (SELECT {col} AS v FROM {src}),
                             b AS (SELECT MIN(v) AS mn, MAX(v) AS mx FROM d),
                             w AS (SELECT mn, CASE WHEN mx > mn THEN (mx - mn) / {nb} ELSE 1 END AS w FROM b)
                        SELECT mn + k * w AS __a, mn + (k + 1) * w AS __b, COUNT(*) AS __v
                        FROM (SELECT LEAST(FLOOR((v - mn) / w), {nb} - 1) AS k, mn, w
                              FROM d, w WHERE v IS NOT NULL)
                        GROUP BY k, mn, w ORDER BY k""")
                    chart = alt.Chart(df).mark_bar(color=PALETTE[0], binSpacing=1).encode(
                        alt.X("__a:Q", title=x, bin="binned"), alt.X2("__b:Q"), alt.Y("__v:Q", title="Jumlah baris"),
                        tooltip=[alt.Tooltip("__a:Q", title="Dari", format=",.2f"),
                                 alt.Tooltip("__b:Q", title="Sampai", format=",.2f"),
                                 alt.Tooltip("__v:Q", title="Jumlah", format=",")],
                    )

                # ---------- Pie / Donut ----------
                else:
                    use_val = agg != "COUNT" and val
                    meas = f"{agg}({num_expr(val)})" if use_val else "COUNT(*)"
                    rank = f"SUM(ABS({num_expr(val)}))" if use_val else "COUNT(*)"
                    df = chart_query(f"SELECT {series_expr(src, cat, rank, topn)} AS __s, {meas} AS __v "
                                     f"FROM {src} GROUP BY 1 ORDER BY 2 DESC")
                    color, doms = color_enc(df, cat)
                    if len(doms) <= 1:
                        color = alt.Color("__s:N", scale=alt.Scale(range=PALETTE[:1]), title=cat)
                    chart = alt.Chart(df).transform_joinaggregate(__t="sum(__v)").transform_calculate(
                        __p="datum.__v / datum.__t").mark_arc(innerRadius=70 if donut else 0, cornerRadius=3,
                                                              padAngle=0.01).encode(
                        theta=alt.Theta("__v:Q", stack=True), color=color, order=alt.Order("__v:Q", sort="descending"),
                        tooltip=[alt.Tooltip("__s:N", title=cat), alt.Tooltip("__v:Q", title=agg, format=",.2f"),
                                 alt.Tooltip("__p:Q", title="Porsi", format=".1%")],
                    )

                if df is not None and len(df):
                    props = {"height": height}
                    if title:
                        props["title"] = title
                    chart = chart.properties(**props).configure_axis(
                        gridOpacity=0.35, domainOpacity=0.4, labelFontSize=12, titleFontSize=12
                    ).configure_view(strokeWidth=0)
                    st.altair_chart(chart, width="stretch")
                    st.caption("Dihitung dari hasil query penuh. Arahkan kursor untuk detail nilai; "
                               "menu ⋯ di pojok chart untuk simpan PNG/SVG.")
                    with st.expander("📋 Lihat data chart"):
                        show = df.drop(columns=[k for k in ("__m",) if k in df.columns]).rename(
                            columns={"__x": "x", "__s": "seri", "__v": "nilai", "__y": "y",
                                     "__a": "dari", "__b": "sampai"})
                        st.dataframe(show, width="stretch", height=300)
                        st.download_button("⬇ Download CSV", show.to_csv(index=False).encode(),
                                           "chart_data.csv", "text/csv")
                elif df is not None:
                    st.info("Tidak ada data untuk digambar (hasil kosong atau nilainya bukan angka).")
            except Exception as e:
                st.error(f"Gagal membuat chart: {e}")

# ================= TAB FILE (browser folder + text editor) =================
TEXT_EXT = (".sql", ".txt", ".md", ".py", ".json", ".jsonl", ".csv", ".tsv", ".log", ".ini", ".cfg", ".yaml",
            ".yml", ".toml", ".bat", ".cmd", ".ps1", ".sh", ".xml", ".html", ".js", ".css", ".env")
EDIT_MAX = 2 * 1024 * 1024  # file > 2 MB tidak dibuka di editor (browser berat); pakai Preview
LIST_MAX = 3000


def human_size(n) -> str:
    if n is None:
        return ""
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def file_icon(name: str, is_dir: bool) -> str:
    if is_dir:
        return "📁"
    ext = os.path.splitext(name.lower())[1]
    if ext in (".csv", ".tsv"):
        return "📄"
    if ext == ".parquet":
        return "🧱"
    if ext in EXCEL_DUCK + EXCEL_PD:
        return "📗"
    if ext == ".sql":
        return "🧮"
    if ext in (".udt", ".stm"):
        return "📡"
    if ext in (".json", ".jsonl"):
        return "🔣"
    if ext in (".duckdb", ".db"):
        return "🦆"
    return "📝" if ext in TEXT_EXT else "▫️"


def fb_go(path: str):
    path = os.path.abspath(os.path.expanduser(path.strip().strip('"')))
    if os.path.isdir(path):
        st.session_state.fb_cwd, st.session_state.fb_sel = path, None
        st.session_state.fb_input, st.session_state.fb_preview = path, None
        st.session_state.fb_msg = None
    elif os.path.isfile(path):
        fb_go(os.path.dirname(path))
        st.session_state.fb_sel = path
    else:
        st.session_state.fb_msg = f"Folder/file tidak ditemukan: {path}"


def fb_on_select():
    ev = st.session_state.get(st.session_state.get("fb_table_key", ""))
    rows = (ev or {}).get("selection", {}).get("rows", []) if isinstance(ev, dict) else \
        (ev.selection.rows if ev is not None else [])
    if not rows:
        return
    ent = st.session_state.fb_entries[rows[0]]
    if ent["dir"]:
        fb_go(ent["path"])
    else:
        st.session_state.fb_sel, st.session_state.fb_preview = ent["path"], None


def read_text(path: str):
    raw = open(path, "rb").read()
    encs = ("utf-8-sig",) if raw.startswith(b"\xef\xbb\xbf") else ("utf-8", "cp1252", "latin-1")
    for enc in encs:
        try:
            return raw.decode(enc).replace("\r\n", "\n"), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1"


def ed_open(path: str):
    try:
        txt, enc = read_text(path)
    except Exception as e:
        st.session_state.ed_msg = ("error", f"Gagal membuka: {e}")
        return
    st.session_state.update(ed_area=txt, ed_orig=txt, ed_path=path, ed_enc=enc, ed_saveas=path,
                            ed_overwrite=False, ed_msg=None)


def ed_new():
    folder = st.session_state.get("fb_cwd", os.getcwd())
    st.session_state.update(ed_area="", ed_orig="", ed_path=None, ed_enc="utf-8",
                            ed_saveas=os.path.join(folder, "query_baru.sql"), ed_overwrite=False, ed_msg=None)


def ed_from_query():
    ed_new()
    st.session_state.ed_area = st.session_state.get("cur_sql", "")


def ed_revert():
    st.session_state.ed_area = st.session_state.get("ed_orig", "")
    st.session_state.ed_msg = None


def ed_to_query():
    st.session_state.load_sql = st.session_state.get("ed_area", "")
    st.session_state.ed_msg = ("success", "Dimuat ke tab Query. Buka tab 🧮 Query lalu klik Run.")


def ed_save():
    path = os.path.abspath(os.path.expanduser(st.session_state.get("ed_saveas", "").strip().strip('"')))
    text = st.session_state.get("ed_area", "")
    if not path or os.path.isdir(path):
        st.session_state.ed_msg = ("error", "Isi path file tujuan, misalnya D:\\tools\\query.sql")
        return
    if not os.path.isdir(os.path.dirname(path)):
        st.session_state.ed_msg = ("error", f"Folder tidak ada: {os.path.dirname(path)}")
        return
    same = st.session_state.get("ed_path") and os.path.abspath(st.session_state.ed_path) == path
    if os.path.exists(path) and not same and not st.session_state.get("ed_overwrite"):
        st.session_state.ed_msg = ("warning", "File tujuan sudah ada. Centang **Timpa file yang sudah ada** "
                                              "kalau memang mau ditimpa.")
        return
    try:
        if os.path.exists(path) and st.session_state.get("ed_backup", True):
            shutil.copy2(path, path + ".bak")
        enc = st.session_state.get("ed_enc", "utf-8")
        with open(path, "w", encoding=enc if enc != "latin-1" else "utf-8") as f:
            f.write(text)
    except Exception as e:
        st.session_state.ed_msg = ("error", f"Gagal menyimpan: {e}")
        return
    st.session_state.update(ed_path=path, ed_orig=text, ed_saveas=path, ed_overwrite=False,
                            ed_msg=("success", f"Tersimpan: {path}"))


st.session_state.setdefault("fb_cwd", os.getcwd())
st.session_state.setdefault("fb_input", st.session_state.fb_cwd)
st.session_state.setdefault("ed_area", "")
st.session_state.setdefault("ed_saveas", os.path.join(st.session_state.fb_cwd, "query_baru.sql"))

with tab_file:
    cwd = st.session_state.fb_cwd

    # ---------- Navigasi ----------
    n1, n2, n3, n4 = st.columns([6, 1, 1, 2])
    n1.text_input("Folder", key="fb_input", label_visibility="collapsed",
                  placeholder=r"Ketik path folder/file, mis. D:\data")
    n2.button("Buka", width="stretch", on_click=lambda: fb_go(st.session_state.fb_input))
    n3.button("⬆ Naik", width="stretch", on_click=lambda: fb_go(os.path.dirname(cwd)),
              disabled=os.path.dirname(cwd) == cwd)
    roots = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")] if os.name == "nt" else ["/"]
    quick = {"Lompat ke...": None, "🏠 Folder app": os.getcwd(), "👤 Home": os.path.expanduser("~")}
    quick.update({f"💽 {r}": r for r in roots})
    qkey = f"fb_quick_{cwd}"

    def _jump(k=qkey, q_=quick):
        if q_.get(st.session_state.get(k)):
            fb_go(q_[st.session_state[k]])

    n4.selectbox("Lompat", list(quick), label_visibility="collapsed", key=qkey, on_change=_jump)
    if st.session_state.get("fb_msg"):
        st.error(st.session_state.fb_msg)

    f1, f2, f3 = st.columns([2, 3, 1])
    ftype = f1.radio("Tampilkan", ["Semua", "Data", "Teks & SQL"], horizontal=True, label_visibility="collapsed")
    fsearch = f2.text_input("Cari nama", placeholder="🔍 cari nama file...", label_visibility="collapsed")
    show_hidden = f3.toggle("Tersembunyi")

    entries, err = [], None
    try:
        with os.scandir(cwd) as it:
            for e in it:
                if not show_hidden and (e.name.startswith(".") or e.name.startswith("~$")):
                    continue
                try:
                    is_dir = e.is_dir()
                    stt = e.stat()
                except OSError:
                    continue
                ext = os.path.splitext(e.name.lower())[1]
                if not is_dir:
                    if ftype == "Data" and ext not in DATA_EXT:
                        continue
                    if ftype == "Teks & SQL" and ext not in TEXT_EXT:
                        continue
                if fsearch and fsearch.lower() not in e.name.lower():
                    continue
                entries.append({"dir": is_dir, "path": e.path, "name": e.name,
                                "size": None if is_dir else stt.st_size, "mtime": stt.st_mtime})
    except Exception as ex:
        err = str(ex)
    entries.sort(key=lambda r: (not r["dir"], r["name"].lower()))
    truncated = len(entries) > LIST_MAX
    entries = entries[:LIST_MAX]
    st.session_state.fb_entries = entries

    left, right = st.columns([3, 2], gap="large")
    with left:
        if err:
            st.error(f"Tidak bisa membuka folder: {err}")
        n_dir = sum(r["dir"] for r in entries)
        st.caption(f"📂 {cwd}  •  {n_dir} folder, {len(entries) - n_dir} file"
                   + (f" (dibatasi {LIST_MAX})" if truncated else "") + "  •  klik baris untuk membuka")
        table = pd.DataFrame({
            " ": [file_icon(r["name"], r["dir"]) for r in entries],
            "Nama": [r["name"] for r in entries],
            "Ukuran": [human_size(r["size"]) for r in entries],
            "Diubah": [datetime.datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d %H:%M") for r in entries],
        })
        tkey = f"fb_table_{abs(hash((cwd, ftype, fsearch, show_hidden)))}"
        st.session_state.fb_table_key = tkey
        st.dataframe(table, key=tkey, on_select=fb_on_select, selection_mode="single-row", hide_index=True,
                     width="stretch", height=430,
                     column_config={" ": st.column_config.TextColumn(width=40)})

        # register banyak file sekaligus (glob)
        exts = sorted({os.path.splitext(r["name"].lower())[1] for r in entries if not r["dir"]} &
                      {".csv", ".tsv", ".parquet", ".json", ".jsonl"})
        if exts or n_dir:
            with st.expander("➕ Register banyak file sekaligus (glob)"):
                g1, g2, g3 = st.columns([2, 2, 1])
                gext = g1.selectbox("Jenis file", exts or [".csv", ".parquet"])
                gname = g2.text_input("Nama view/tabel", safe_name(os.path.basename(cwd)), key="fb_gname")
                grec = g3.toggle("Subfolder", help="Ikut baca semua subfolder (**)")
                pattern = os.path.join(cwd, "**", "*" + gext) if grec else os.path.join(cwd, "*" + gext)
                st.caption(f"Pola: `{pattern}`")
                gtab = st.checkbox("Import jadi tabel", value=gext != ".parquet", key="fb_gtab")
                if st.button("Register glob", type="primary", key="fb_glob_btn"):
                    try:
                        t0 = time.time()
                        with st.spinner("Membaca file..."):
                            kind = register_file(con, pattern, gname, as_table=gtab)
                        scache.clear()
                        st.toast(f"{kind} '{gname}' siap ({time.time()-t0:.1f} detik)", icon="✅")
                    except Exception as ex:
                        st.error(str(ex))

    # ---------- Detail & aksi file terpilih ----------
    with right:
        sel = st.session_state.get("fb_sel")
        if not sel or not os.path.isfile(sel):
            st.info("Pilih file di daftar sebelah kiri untuk melihat aksi: preview, register, "
                    "buka di editor, atau muat ke Query.")
        else:
            name = os.path.basename(sel)
            ext = os.path.splitext(name.lower())[1]
            size = os.path.getsize(sel)
            st.markdown(f"#### {file_icon(name, False)} {name}")
            st.caption(f"{human_size(size)}  •  diubah "
                       f"{datetime.datetime.fromtimestamp(os.path.getmtime(sel)).strftime('%Y-%m-%d %H:%M')}")
            st.code(sel, language=None)

            if ext in DATA_EXT:
                sheet_sel = ""
                if ext in EXCEL_DUCK + EXCEL_PD:
                    sheets = excel_sheets(sel)
                    sheet_sel = st.selectbox("Sheet", sheets) if sheets else \
                        st.text_input("Sheet (kosong = pertama)", "", key="fb_sheet")
                r1, r2 = st.columns(2)
                rname = r1.text_input("Nama view/tabel", safe_name(os.path.splitext(name)[0]), key=f"fb_rn_{sel}")
                rtab = r2.checkbox("Import jadi tabel", value=ext != ".parquet", key=f"fb_rt_{sel}",
                                   help="File Excel selalu di-import jadi tabel")
                rvar = r2.checkbox("Semua kolom teks", key=f"fb_rv_{sel}")
                b1, b2, b3 = st.columns(3)
                if b1.button("👁 Preview", width="stretch", key="fb_prev_btn"):
                    try:
                        st.session_state.fb_preview = (sel, preview_file(con, sel, sheet_sel))
                    except Exception as ex:
                        st.error(str(ex))
                if b2.button("➕ Register", type="primary", width="stretch", key="fb_reg_btn"):
                    try:
                        t0 = time.time()
                        with st.spinner("Membaca file..."):
                            kind = register_file(con, sel, rname, all_varchar=rvar, as_table=rtab, sheet=sheet_sel)
                        scache.clear()
                        st.toast(f"{kind} '{rname}' siap ({time.time()-t0:.1f} detik). "
                                 f"Pakai: SELECT * FROM {rname}", icon="✅")
                    except Exception as ex:
                        st.error(str(ex))
                src_q = source_sql(sel, all_varchar=rvar, sheet=sheet_sel)
                if b3.button("🧮 Ke Query", width="stretch", disabled=src_q is None, key="fb_q_btn",
                             help="Tulis SELECT dari file ini di editor Query" if src_q else
                             "File .xlsb/.xls perlu di-Register dulu"):
                    st.session_state.load_sql = f"SELECT *\nFROM {src_q}\nLIMIT 100;"
                    st.toast("Query dimuat. Buka tab 🧮 Query lalu klik Run.", icon="🧮")

            if ext in TEXT_EXT:
                too_big = size > EDIT_MAX
                e1, e2 = st.columns(2)
                e1.button("✏️ Buka di editor", width="stretch", disabled=too_big,
                          on_click=ed_open, args=(sel,),
                          help="File > 2 MB terlalu besar untuk editor; pakai Preview" if too_big else None)
                if ext == ".sql":
                    def _sql_to_query(p=sel):
                        st.session_state.load_sql = read_text(p)[0]
                        st.session_state.ed_msg = ("success", "SQL dimuat ke tab Query.")
                    e2.button("🧮 Muat ke Query", width="stretch", on_click=_sql_to_query,
                              disabled=too_big, key="fb_sql_to_query")

            if ext in (".udt", ".stm"):
                def _to_trace(p=sel):
                    st.session_state.tr_path = p
                st.button("📡 Buka di tab Trace", type="primary", width="stretch", on_click=_to_trace,
                          key="fb_trace_btn", help="Isi path ini di tab Trace, lalu klik Buka trace di sana")
            if ext not in DATA_EXT and ext not in TEXT_EXT and ext not in (".udt", ".stm"):
                st.caption("Jenis file ini tidak bisa dibuka di app.")

    pv = st.session_state.get("fb_preview")
    if pv and pv[0] == st.session_state.get("fb_sel"):
        st.markdown(f"**Preview 100 baris pertama — {os.path.basename(pv[0])}**")
        st.dataframe(pv[1], width="stretch", height=320)

    # ---------- Text editor ----------
    st.divider()
    ed_path = st.session_state.get("ed_path")
    dirty = st.session_state.get("ed_area", "") != st.session_state.get("ed_orig", "")
    h1, h2, h3 = st.columns([5, 1.3, 1.6])
    h1.markdown(f"#### ✏️ Text editor  \n"
                f"<span style='opacity:.65'>{ed_path or 'File baru (belum disimpan)'}"
                f"{'  •  ● belum disimpan' if dirty else ''}</span>", unsafe_allow_html=True)
    h2.button("📄 Baru", width="stretch", on_click=ed_new)
    h3.button("📥 Dari query", width="stretch", on_click=ed_from_query,
              help="Salin SQL yang sedang ada di tab Query ke editor")
    st.text_area("Isi file", key="ed_area", height=420, label_visibility="collapsed",
                 placeholder="Buka file dari daftar di atas, atau tulis langsung di sini...")
    s1, s2, s3 = st.columns([5, 1.4, 1.4])
    s1.text_input("Simpan ke", key="ed_saveas")
    s2.checkbox("Backup .bak", value=True, key="ed_backup",
                help="Salin versi lama ke nama_file.bak sebelum ditimpa")
    s3.checkbox("Timpa file yang sudah ada", key="ed_overwrite")
    a1, a2, a3, _ = st.columns([1.2, 1.4, 1.6, 3])
    a1.button("💾 Simpan", type="primary", width="stretch", on_click=ed_save)
    a2.button("↩ Batalkan", width="stretch", on_click=ed_revert, disabled=not dirty,
              help="Kembalikan isi ke versi terakhir yang disimpan/dibuka")
    a3.button("🧮 Muat ke Query", width="stretch", on_click=ed_to_query, key="ed_to_query_btn",
              disabled=not st.session_state.get("ed_area"))
    msg = st.session_state.get("ed_msg")
    if msg:
        getattr(st, msg[0])(msg[1])

# ================= TAB TRACE (signaling ZTE .udt) =================
with tab_trace:
    st.caption("Buka hasil UE trace ZTE (.udt dari NMS, atau folder/file .stm). Pesan RRC LTE, S1AP, X2AP "
               "(EN-DC), dan measurement NR di-decode dengan ASN.1 3GPP. Hasilnya jadi tabel **trace** "
               "yang juga bisa di-query di tab Query.")
    l1, l2, l3, l4 = st.columns([6, 1.6, 1.6, 1.4])
    tpath = l1.text_input("File trace", key="tr_path", label_visibility="collapsed",
                          placeholder=r"Path file .udt / folder .stm, mis. D:\trace\STA-CPE_628170104891.udt")
    tname = l2.text_input("Nama tabel", "trace", key="tr_table", label_visibility="collapsed")
    t_int = l3.toggle("Internal ZTE", key="tr_internal",
                      help="Ikut simpan record internal ZTE (tidak ter-decode, jarang dibutuhkan)")
    if l4.button("📡 Buka trace", type="primary", width="stretch") and tpath:
        bar = st.progress(0.0, "Menyiapkan...")
        try:
            t0 = time.time()
            info_t = load_trace(con, tpath, tname or "trace", t_int,
                                progress=lambda f, txt: bar.progress(min(max(f, 0.0), 1.0), txt))
            bar.empty()
            scache.clear()
            st.session_state.tr_loaded = dict(info_t, table=tname or "trace", path=tpath, secs=time.time() - t0)
            st.session_state.tr_sel = None
            st.toast(f"Trace siap: {info_t['rows']:,} pesan ({time.time() - t0:.1f} detik"
                     + (", dari cache" if info_t["from_cache"] else "") + ")", icon="📡")
        except Exception as ex:
            bar.empty()
            st.error(str(ex))

    TL = st.session_state.get("tr_loaded")
    tl_ok = False
    if TL:
        try:
            con.execute(f'SELECT 1 FROM "{TL["table"]}" LIMIT 1')
            tl_ok = True
        except Exception:
            st.info("Tabel trace sudah tidak ada (mungkin app di-restart). Klik **Buka trace** lagi; "
                    "kalau sudah pernah dibuka, hasilnya diambil dari cache dan langsung jadi.")
    if not TL:
        st.info("Masukkan path file `.udt` lalu klik **📡 Buka trace**. Decode pertama butuh waktu "
                "(sekitar beberapa menit untuk trace berhari-hari). Hasilnya di-cache, jadi pembukaan "
                "berikutnya instan.")
    if TL and tl_ok:
        tb = f'"{TL["table"]}"'
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Pesan", f"{TL['rows']:,}")
        m2.metric("Mulai", TL["start"].strftime("%d %b %H:%M:%S"))
        m3.metric("Selesai", TL["end"].strftime("%d %b %H:%M:%S"))
        m4.metric("eNB/cell", TL["cells"])
        if TL["errors"]:
            st.warning(f"{TL['errors']:,} pesan gagal di-decode (ditandai DECODE_ERR di kolom info).")

        with st.expander("📋 Ringkasan event", expanded=False):
            ev = con.execute(f"""
                SELECT proto, msg, COUNT(*) AS jumlah, MIN(time) AS pertama, MAX(time) AS terakhir
                FROM {tb} WHERE {KEY_EVENT_SQL} GROUP BY ALL ORDER BY jumlah DESC""").df()
            st.dataframe(ev, hide_index=True, width="stretch")
            cells = con.execute(f"""SELECT enb, cell, COUNT(*) AS pesan, MIN(time) AS dari, MAX(time) AS sampai
                                    FROM {tb} WHERE proto='RRC' GROUP BY ALL ORDER BY pesan DESC""").df()
            st.markdown("**Cell yang melayani (dari header RRC)**")
            st.dataframe(cells, hide_index=True, width="stretch")

        # ---------- filter ----------
        f1, f2, f3 = st.columns([2.2, 2, 3])
        preset = f1.radio("Tampilkan", ["Semua", "Event penting", "Measurement", "Event A3/A5/B1", "Mobility"],
                          horizontal=True, key="tr_preset")
        protos = f2.multiselect("Protokol", ["RRC", "S1AP", "X2AP", "ZTE-internal", "RRC?"],
                                default=["RRC", "S1AP", "X2AP"], key="tr_proto")
        q_txt = f3.text_input("Cari (nama pesan / info)", key="tr_q", placeholder="mis. A3, A5, reestablishment, PCI230, Cause")
        g1, g2, g3 = st.columns([3, 1.4, 1.4])
        all_msgs = [r[0] for r in con.execute(f"SELECT msg FROM {tb} GROUP BY 1 ORDER BY COUNT(*) DESC").fetchall()]
        msgs = g1.multiselect("Pesan tertentu (kosong = semua)", all_msgs, key="tr_msgs")
        tmin, tmax = TL["start"], TL["end"]
        t_from = g2.text_input("Dari (YYYY-MM-DD HH:MM:SS)", tmin.strftime("%Y-%m-%d %H:%M:%S"), key="tr_from")
        t_to = g3.text_input("Sampai", tmax.strftime("%Y-%m-%d %H:%M:%S"), key="tr_to")

        conds = []
        if preset == "Event penting":
            conds.append(KEY_EVENT_SQL)
        elif preset == "Measurement":
            conds.append(MEAS_SQL)
        elif preset == "Mobility":
            conds.append(MOBILITY_SQL)
        elif preset == "Event A3/A5/B1":
            conds.append("(meas_event IS NOT NULL AND meas_event NOT LIKE 'Periodic%')")
        if protos:
            conds.append("proto IN (" + ",".join("'" + p + "'" for p in protos) + ")")
        if msgs:
            conds.append("msg IN (" + ",".join("'" + m.replace("'", "''") + "'" for m in msgs) + ")")
        if q_txt:
            qq = q_txt.replace("'", "''")
            conds.append(f"(msg ILIKE '%{qq}%' OR info ILIKE '%{qq}%')")
        conds.append(f"time BETWEEN TRY_CAST('{t_from.replace(chr(39), '')}' AS TIMESTAMP) "
                     f"AND TRY_CAST('{t_to.replace(chr(39), '')}' AS TIMESTAMP) + INTERVAL 999 MILLISECOND")
        where = " AND ".join(conds)
        where_time = conds[-1]
        st.session_state.tr_where = where
        n_f = con.execute(f"SELECT COUNT(*) FROM {tb} WHERE {where}").fetchone()[0]
        lim = 20000
        view = con.execute(f"""SELECT no, time, proto, dir, msg, meas_event AS event, info, enb, cell, rnti, gnb,
                                      {KEY_EVENT_SQL} AS penting
                               FROM {tb} WHERE {where} ORDER BY no LIMIT {lim}""").df()
        st.caption(f"{n_f:,} pesan cocok" + (f" (ditampilkan {lim:,} pertama; persempit waktu/filter)" if n_f > lim else "")
                   + "  •  klik baris untuk detail & diagram ladder")
        ev_sel = st.dataframe(
            view, key=f"tr_tbl_{abs(hash(where))}", on_select="rerun", selection_mode="single-row",
            hide_index=True, width="stretch", height=420,
            column_config={"time": st.column_config.DatetimeColumn("time", format="YYYY-MM-DD HH:mm:ss.SSS"),
                           "info": st.column_config.TextColumn("info", width="large"),
                           "penting": st.column_config.CheckboxColumn("⚑", width=40)})
        rows_sel = ev_sel.selection.rows if ev_sel is not None else []
        if rows_sel:
            st.session_state.tr_sel = int(view.iloc[rows_sel[0]]["no"])
        sel_no = st.session_state.get("tr_sel")

        d_tab, l_tab, c_tab = st.tabs(["🔎 Detail pesan", "🪜 Diagram ladder", "📈 RSRP / RSRQ"])

        # ---------- detail ----------
        with d_tab:
            if not sel_no:
                st.info("Pilih satu baris di timeline untuk melihat decode lengkap dan raw bytes-nya.")
            else:
                r = con.execute(f"SELECT * FROM {tb} WHERE no = ?", [sel_no]).df().iloc[0]
                st.markdown(f"**#{r['no']} · {r['msg']}** · {r['proto']} {r['dir'] or ''} · "
                            f"{pd.Timestamp(r['time']).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("eNB / cell", f"{r['enb']} / {r['cell']}")
                k2.metric("RNTI", int(r["rnti"]))
                k3.metric("gNB", int(r["gnb"]) if r["gnb"] else "-")
                k4.metric("Kelas / ID", f"{r['cls']} / 0x{int(r['mid']):04x}")
                if r["info"]:
                    st.code(r["info"], language=None)
                st.caption(f"Lokasi: {r['file']} @ byte {int(r['offset']):,} (bisa dicocokkan di LteSignalAnalysis)")
                try:
                    full_txt = decode_record(int(r["cls"]), int(r["mid"]), bytes.fromhex(r["payload_hex"]),
                                             asn1_types(), full=True)[5]
                except Exception as ex:
                    full_txt = f"(gagal decode: {ex})"
                with st.expander("Decode ASN.1 lengkap", expanded=True):
                    st.code(full_txt or "(pesan internal ZTE, tidak ada decode 3GPP)", language=None)
                with st.expander("Raw bytes"):
                    ph = r["payload_hex"]
                    st.code("header  " + r["hdr"] + "\n\npayload " +
                            "\n        ".join(" ".join(ph[i:i + 2] for i in range(j, min(j + 64, len(ph)), 2))
                                              for j in range(0, len(ph), 64)), language=None)

        # ---------- ladder ----------
        with l_tab:
            lc1, lc2, lc3 = st.columns([2, 2, 3])
            n_lad = lc1.slider("Jumlah pesan", 10, 150, 40, 5, key="tr_lad_n")
            lad_meas = lc2.toggle("Sertakan measurement report", value=False, key="tr_lad_meas")
            lc3.caption("Mulai dari baris yang dipilih di timeline (atau awal filter). "
                        "Merah tebal = event penting.")
            lw = where + ("" if lad_meas else f" AND NOT {MEAS_SQL}")
            start_no = sel_no or 0
            lad = con.execute(f"""SELECT time, proto, dir, msg, info, {KEY_EVENT_SQL} AS key
                                  FROM {tb} WHERE {lw} AND no >= ? ORDER BY no LIMIT {int(n_lad)}""",
                              [start_no]).df()
            if lad.empty:
                st.info("Tidak ada pesan untuk digambar dengan filter ini.")
            else:
                svg = ladder_svg(lad.to_dict("records"))
                import base64
                b64 = base64.b64encode(svg.encode()).decode()
                st.markdown(f'<div style="overflow:auto;max-height:900px;border:1px solid #e3e6eb;border-radius:10px">'
                            f'<img alt="ladder" src="data:image/svg+xml;base64,{b64}" style="display:block"></div>',
                            unsafe_allow_html=True)
                st.download_button("⬇ Download diagram (SVG)", svg.encode(), "ladder.svg", "image/svg+xml")

        # ---------- grafik ----------
        with c_tab:
            mdf = con.execute(f"""SELECT time, pcell_rsrp AS "LTE RSRP (dBm)", pcell_rsrq AS "LTE RSRQ (dB)",
                                         nr_rsrp AS "NR RSRP (dBm)", nr_sinr AS "NR SINR (dB)"
                                  FROM {tb} WHERE {where_time}
                                    AND (pcell_rsrp IS NOT NULL OR nr_rsrp IS NOT NULL)
                                  ORDER BY time LIMIT 50000""").df()
            evs = con.execute(f"""SELECT time, msg FROM {tb} WHERE {where_time}
                                  AND (msg IN ('rrcConnectionReestablishmentRequest','scgFailureInformationNR-r15',
                                       'rrcConnectionRelease') OR info LIKE 'HO cmd%')""").df()
            if mdf.empty:
                st.info("Tidak ada measurement report di rentang/filter ini.")
            else:
                long = mdf.melt("time", var_name="metrik", value_name="nilai").dropna()
                metrics = st.multiselect("Metrik", list(mdf.columns[1:]), default=["LTE RSRP (dBm)", "LTE RSRQ (dB)"],
                                         key="tr_metrics")
                long = long[long["metrik"].isin(metrics)]
                charts = []
                for k, mname in enumerate(metrics):
                    part = long[long["metrik"] == mname]
                    if part.empty:
                        continue
                    col_k = PALETTE[k % len(PALETTE)]
                    base_c = alt.Chart(part).mark_line(point=alt.OverlayMarkDef(size=18, color=col_k), strokeWidth=1.5,
                                                       color=col_k).encode(
                        alt.X("time:T", title=None, axis=alt.Axis(format="%d %b %H:%M", labelOverlap="greedy")),
                        alt.Y("nilai:Q", title=mname, scale=alt.Scale(zero=False)),
                        tooltip=[alt.Tooltip("time:T", format="%Y-%m-%d %H:%M:%S"), alt.Tooltip("nilai:Q", title=mname)])
                    layer = base_c
                    if not evs.empty:
                        rules = alt.Chart(evs).mark_rule(strokeDash=[4, 3], opacity=0.55).encode(
                            x="time:T", color=alt.Color("msg:N", title="Event",
                                                        scale=alt.Scale(range=["#e34948", "#6250d6", "#9a9a96", "#eda100"])),
                            tooltip=[alt.Tooltip("time:T", format="%Y-%m-%d %H:%M:%S"), "msg:N"])
                        layer = alt.layer(rules, base_c)
                    charts.append(layer.properties(height=220))
                for ch_k in charts:
                    st.altair_chart(ch_k.configure_view(strokeWidth=0).configure_axis(gridOpacity=0.35)
                                    .configure_legend(orient="top"), width="stretch")
                if charts:
                    st.caption("Garis putus-putus = event (RLF/reestablishment, SCG failure, release, HO). "
                               "Nilai dari measurement report UE yang sudah dikonversi ke dBm/dB.")

        st.caption(f"Tabel **{TL['table']}** bisa di-query di tab Query, mis. "
                   f"`SELECT time, msg, info FROM {TL['table']} WHERE msg = 'rrcConnectionReestablishmentRequest'`.")

# ================= TAB DOCS =================
with tab_docs:
    st.markdown(
        "Contoh memakai view **`data`** dengan kolom contoh "
        "`site_id, cell_id, msisdn, ts, rsrp, sinr, dl_thp, ul_thp`. "
        "Ganti nama kolom sesuai file kamu. Klik **Pakai** untuk memuat ke editor, "
        "lalu buka tab Query."
    )
    st.info(
        "Urutan penulisan: SELECT → FROM → JOIN → WHERE → GROUP BY → HAVING → "
        "QUALIFY → ORDER BY → LIMIT.  Teks pakai 'kutip tunggal', nama kolom aneh/berspasi "
        "pakai \"kutip ganda\". Komentar: -- satu baris."
    )
    cari = st.text_input("🔍 Cari contoh", placeholder="mis. tanggal, top, median, join, parquet")
    kw = cari.lower().strip()

    for section, items in DOCS.items():
        shown = [it for it in items if not kw or kw in (section + it[0] + it[1] + it[2]).lower()]
        if not shown:
            continue
        with st.expander(section, expanded=bool(kw)):
            for j, (judul, ket, contoh) in enumerate(shown):
                d1, d2 = st.columns([6, 1])
                d1.markdown(f"**{judul}**" + (f" — {ket}" if ket else ""))
                if d2.button("Pakai", key=f"doc_{section}_{j}"):
                    st.session_state.load_sql = contoh
                    st.rerun()
                st.code(contoh, language="sql")

    st.markdown("Referensi lengkap: https://duckdb.org/docs/stable/sql/introduction")
