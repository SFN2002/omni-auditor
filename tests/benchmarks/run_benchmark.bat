@echo off
REM One-command benchmark runner for Windows.
REM Executes: fetch_dataset -> compare -> analyze_results

cd /d "%~dp0"
python fetch_dataset.py && python compare.py && python analyze_results.py
