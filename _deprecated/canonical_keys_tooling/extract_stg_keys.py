#!/usr/bin/env python3
"""
extract_stg_keys.py
===================
Performs pure, deterministic extraction of all claim and preauth document keys
defined in the PM-JAY STG files. Writes results to JSON, CSV, and logs.
"""

import sys
import os
import json
import csv
from pathlib import Path
from collections import Counter, defaultdict

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import STG_DIR

# Set up output paths
TOOLING_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = TOOLING_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

JSON_OUT = OUTPUT_DIR / "flat_key_list.json"
CSV_OUT = OUTPUT_DIR / "flat_key_list.csv"
LOG_OUT = OUTPUT_DIR / "run_log.txt"

# Console & file logger helper
log_lines = []

def log_print(msg=""):
    print(msg)
    log_lines.append(msg)

def save_log():
    with open(LOG_OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")

def main():
    log_print("=" * 60)
    log_print("IRIS STG Document Key Extractor (Script 1 of 2)")
    log_print("=" * 60)
    
    stg_path = Path(STG_DIR)
    if not stg_path.exists() or not stg_path.is_dir():
        log_print(f"Error: STG directory not found at {stg_path}")
        save_log()
        sys.exit(1)
        
    # Find all .json files in STG directory
    stg_files = sorted(list(stg_path.glob("*.json")))
    total_files = len(stg_files)
    log_print(f"Found {total_files} JSON guideline files in STG directory.")
    
    success_count = 0
    fail_count = 0
    failures = []
    
    preauth_non_empty = 0
    preauth_empty = 0
    claim_non_empty = 0
    claim_empty = 0
    
    flat_rows = []
    unique_keys = set()
    optional_keys = []  # list of tuples (key, source_file)
    
    # Trackers for specialty stats
    # specialty -> set of files
    specialty_files = defaultdict(set)
    # specialty -> set of keys
    specialty_keys = defaultdict(set)
    
    for f in stg_files:
        filename = f.name
        try:
            with open(f, "r", encoding="utf-8") as file_obj:
                data = json.load(file_obj)
            success_count += 1
        except Exception as e:
            fail_count += 1
            failures.append(filename)
            log_print(f"Error: Failed to parse {filename}: {e}")
            continue
            
        # Metadata extraction
        stg_id = data.get("stg_id")
        procedure_code = data.get("procedure_code")
        specialty = data.get("specialty", "Unknown Specialty")
        condition = data.get("condition")
        procedure_name = data.get("procedure_name")
        
        # Track specialty file count
        specialty_files[specialty].add(filename)
        
        mandatory_docs = data.get("mandatory_documents", {})
        
        # Preauth section
        preauth_list = mandatory_docs.get("preauth")
        if preauth_list and isinstance(preauth_list, list):
            preauth_non_empty += 1
            for entry in preauth_list:
                key = entry.get("key")
                label = entry.get("label", "")
                if key:
                    # Check if optional
                    is_optional = False
                    if "optional" in label.lower():
                        is_optional = True
                        optional_keys.append((key, filename))
                        
                    flat_rows.append({
                        "source_file": filename,
                        "stg_id": stg_id,
                        "specialty": specialty,
                        "procedure_code": procedure_code,
                        "section": "preauth",
                        "key": key,
                        "label": label,
                        "is_labeled_optional": is_optional
                    })
                    unique_keys.add(key)
                    specialty_keys[specialty].add(key)
        else:
            preauth_empty += 1
            log_print(f"Notable Event: '{filename}' has empty or missing preauth documents list.")
            
        # Claim section
        claim_list = mandatory_docs.get("claim")
        if claim_list and isinstance(claim_list, list):
            claim_non_empty += 1
            for entry in claim_list:
                key = entry.get("key")
                label = entry.get("label", "")
                if key:
                    # Check if optional
                    is_optional = False
                    if "optional" in label.lower():
                        is_optional = True
                        optional_keys.append((key, filename))
                        
                    flat_rows.append({
                        "source_file": filename,
                        "stg_id": stg_id,
                        "specialty": specialty,
                        "procedure_code": procedure_code,
                        "section": "claim",
                        "key": key,
                        "label": label,
                        "is_labeled_optional": is_optional
                    })
                    unique_keys.add(key)
                    specialty_keys[specialty].add(key)
        else:
            claim_empty += 1
            log_print(f"Notable Event: '{filename}' has empty or missing claim documents list.")

    # Write output/flat_key_list.json
    try:
        with open(JSON_OUT, "w", encoding="utf-8") as json_file:
            json.dump(flat_rows, json_file, indent=2)
    except Exception as e:
        log_print(f"Error: Failed to write JSON output: {e}")
        
    # Write output/flat_key_list.csv
    try:
        with open(CSV_OUT, "w", encoding="utf-8", newline="") as csv_file:
            fieldnames = [
                "source_file", "stg_id", "specialty", "procedure_code",
                "section", "key", "label", "is_labeled_optional"
            ]
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            for row in flat_rows:
                writer.writerow(row)
    except Exception as e:
        log_print(f"Error: Failed to write CSV output: {e}")

    # Compute key reuse counts for top 10
    key_file_counts = defaultdict(set)
    for row in flat_rows:
        key_file_counts[row["key"]].add(row["source_file"])
    
    top_keys = sorted(
        [(k, len(files)) for k, files in key_file_counts.items()],
        key=lambda x: x[1],
        reverse=True
    )[:10]

    # Printing Stats
    log_print("\n" + "=" * 60)
    log_print("EXTRACTION SUMMARY STATS")
    log_print("=" * 60)
    log_print(f"Total STG files found:                      {total_files}")
    log_print(f"Successfully parsed files:                  {success_count}")
    log_print(f"Failed to parse files:                      {fail_count}")
    if failures:
        log_print(f"  Failures: {', '.join(failures)}")
        
    log_print(f"STGs with non-empty preauth section:        {preauth_non_empty}")
    log_print(f"STGs with empty/missing preauth section:    {preauth_empty}")
    log_print(f"STGs with non-empty claim section:          {claim_non_empty}")
    log_print(f"STGs with empty/missing claim section:      {claim_empty}")
    log_print(f"Total individual document key entries:      {len(flat_rows)}")
    log_print(f"Total UNIQUE exact-match keys:              {len(unique_keys)}")
    
    log_print(f"Total keys labeled as optional:             {len(optional_keys)}")
    
    if optional_keys:
        log_print("\n" + "-" * 40)
        log_print("KEYS Labeled as Optional:")
        log_print("-" * 40)
        for key, src in sorted(optional_keys):
            log_print(f"  - Key: '{key}' in file: {src}")
            
    log_print("\n" + "=" * 60)
    log_print("TOP 10 MOST WIDELY REUSED KEYS")
    log_print("=" * 60)
    for rank, (key, count) in enumerate(top_keys, 1):
        log_print(f"  {rank:2d}. Key: '{key:<40}' -> in {count:3d} files")
        
    log_print("\n" + "=" * 60)
    log_print("BREAKDOWN BY SPECIALTY")
    log_print("=" * 60)
    for specialty in sorted(specialty_files.keys()):
        file_cnt = len(specialty_files[specialty])
        uniq_key_cnt = len(specialty_keys[specialty])
        log_print(f"  Specialty: '{specialty:<35}' -> {file_cnt:3d} files, {uniq_key_cnt:3d} unique keys")
        
    log_print("\n" + "=" * 60)
    log_print("OUTPUT FILE PATHS WRITTEN")
    log_print("=" * 60)
    log_print(f"JSON flat list: {JSON_OUT.relative_to(PROJECT_ROOT)}")
    log_print(f"CSV flat list:  {CSV_OUT.relative_to(PROJECT_ROOT)}")
    log_print(f"Run log text:   {LOG_OUT.relative_to(PROJECT_ROOT)}")
    log_print("=" * 60)
    
    save_log()

if __name__ == "__main__":
    main()
