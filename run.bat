@echo off
cd /d %~dp0
python -m streamlit run duckdb_gui.py
pause