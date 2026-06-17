#!/usr/bin/env python3
"""
eval.py — IRIS Pipeline Evaluation Script
========================================
Discovers and runs test cases through the IRIS pipeline, compares outputs
against the expected answer key, and reports classifications in a table.
"""

import os
import sys
import glob
import json
import subprocess
import textwrap
import argparse
import threading
import time
from pathlib import Path
from datetime import datetime

# Force UTF-8 encoding for stdout and stderr to handle unicode table drawing characters
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Global cache for procedure names
_procedure_name_cache = {}

def lookup_procedure_name(code: str, project_root: str) -> str:
    """
    Builds path: os.path.join(project_root, "data", "stg", f"{code}.json")
    Opens and parses JSON, returning value of top-level key "procedure_name".
    On any exception (FileNotFoundError, KeyError, JSONDecodeError etc):
    returns "(name unavailable)".
    """
    if code in _procedure_name_cache:
        return _procedure_name_cache[code]
        
    try:
        path = os.path.join(project_root, "data", "stg", f"{code}.json")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        name = data.get("procedure_name", "(name unavailable)")
        _procedure_name_cache[code] = name
        return name
    except Exception:
        _procedure_name_cache[code] = "(name unavailable)"
        return "(name unavailable)"

def get_stderr_detail(stderr_content: str, fallback: str) -> str:
    """Extracts the last 3 non-empty lines of stderr joined by ' | '."""
    if not stderr_content:
        return fallback
    lines = [line.strip() for line in stderr_content.splitlines() if line.strip()]
    last_lines = lines[-3:]
    if not last_lines:
        return fallback
    return " | ".join(last_lines)

def extract_json(stdout: str) -> dict:
    """Attempts to find and parse the outer-most JSON object from stdout that is the IRISOutput."""
    # Locate boundaries of candidate JSON objects
    start = 0
    while True:
        start_idx = stdout.find('{', start)
        if start_idx == -1:
            break
        end = len(stdout)
        while end > start_idx:
            end_idx = stdout.rfind('}', start_idx, end)
            if end_idx == -1:
                break
            try:
                candidate = stdout[start_idx:end_idx+1]
                obj = json.loads(candidate)
                if isinstance(obj, dict) and "readiness_status" in obj:
                    return obj
            except json.JSONDecodeError:
                pass
            end = end_idx  # Try earlier end boundary
        start = start_idx + 1
    raise ValueError("Could not find valid IRISOutput JSON in stdout")

def main():
    # Capture local time at script start for consistent timestamping
    start_time_dt = datetime.now()
    
    # Setup argument parser
    parser = argparse.ArgumentParser(description="IRIS Pipeline Evaluation Script")
    parser.add_argument("--tc", type=str, help="Run a single test case by ID (e.g. TC17)")
    args = parser.parse_args()

    # Define paths relative to the script location (project root)
    project_root = Path(__file__).resolve().parent
    expected_output_path = project_root / "tests" / "output" / "expected_output.json"

    # Constraint: If tests/output/expected_output.json does not exist, exit immediately
    if not expected_output_path.exists():
        print(f"Error: Answer key not found at {expected_output_path}", file=sys.stderr)
        sys.exit(1)

    # Load the answer key once at script start as a dict
    try:
        with open(expected_output_path, "r", encoding="utf-8") as f:
            expected_data = json.load(f)
    except Exception as e:
        print(f"Error loading expected_output.json: {e}", file=sys.stderr)
        sys.exit(1)

    # Discover input files with glob: tests/inputs/TC*.json
    input_pattern = str(project_root / "tests" / "inputs" / "TC*.json")
    tc_files = sorted(glob.glob(input_pattern))

    if not tc_files:
        print("Error: No test cases found in tests/inputs/ matching TC*.json", file=sys.stderr)
        sys.exit(1)

    # Filter by optional argument if provided
    if args.tc:
        tc_files = [f for f in tc_files if Path(f).stem == args.tc]
        if not tc_files:
            print(f"Error: Test case '{args.tc}' not found in tests/inputs/.", file=sys.stderr)
            sys.exit(1)

    results = []

    # Counters for final summary
    fully_correct = 0
    partially_correct = 0
    incorrect = 0
    skipped = 0
    errors = 0

    for file_path in tc_files:
        tc_id = Path(file_path).stem
        
        # Look up expected output by TC id key (never iterate sequentially)
        expected_info = expected_data.get(tc_id)
        
        if expected_info is None:
            # SKIP: TC id has no entry in expected_output.json
            results.append({
                "tc_id": tc_id,
                "result": "SKIP",
                "detail": "not in expected_output.json",
                "expected_codes": [],
                "actual_packages": []
            })
            skipped += 1
            continue

        expect_selected_codes = expected_info.get("expect_selected_codes", [])

        # Print separator line before starting each TC's subprocess
        separator = f"── Running {tc_id} "
        dash_count = max(0, 60 - len(separator))
        print(separator + "─" * dash_count, flush=True)

        # Run the pipeline as a subprocess
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        
        try:
            proc = subprocess.Popen(
                [sys.executable, "main.py", file_path],
                cwd=str(project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                env=env
            )
        except Exception as e:
            results.append({
                "tc_id": tc_id,
                "result": "ERROR",
                "detail": f"failed to spawn subprocess: {e}",
                "expected_codes": expect_selected_codes,
                "actual_packages": []
            })
            errors += 1
            continue

        stdout_chunks = []
        stderr_lines = []

        def read_stdout(pipe, chunks):
            try:
                while True:
                    data = pipe.read(4096)
                    if not data:
                        break
                    chunks.append(data)
            except Exception:
                pass

        def read_stderr(pipe, tc_id_val, lines):
            try:
                for line in iter(pipe.readline, ''):
                    if not line:
                        break
                    stripped = line.rstrip('\r\n')
                    print(f"[{tc_id_val}] {stripped}", flush=True)
                    lines.append(stripped)
            except Exception:
                pass

        t_stdout = threading.Thread(target=read_stdout, args=(proc.stdout, stdout_chunks))
        t_stderr = threading.Thread(target=read_stderr, args=(proc.stderr, tc_id, stderr_lines))
        
        t_stdout.daemon = True
        t_stderr.daemon = True
        
        t_stdout.start()
        t_stderr.start()

        # Wait for the process with a 120s timeout
        start_time = time.time()
        timeout_occurred = False
        
        while proc.poll() is None:
            if time.time() - start_time > 120:
                proc.kill()
                timeout_occurred = True
                break
            time.sleep(0.1)

        # Join the threads to ensure all output is read
        t_stdout.join(timeout=5)
        t_stderr.join(timeout=5)

        if timeout_occurred:
            results.append({
                "tc_id": tc_id,
                "result": "ERROR",
                "detail": "timed out after 120s",
                "expected_codes": expect_selected_codes,
                "actual_packages": []
            })
            errors += 1
            continue

        # Check exit code
        if proc.returncode != 0:
            err_detail = get_stderr_detail("\n".join(stderr_lines), f"exit code {proc.returncode}")
            results.append({
                "tc_id": tc_id,
                "result": "ERROR",
                "detail": err_detail,
                "expected_codes": expect_selected_codes,
                "actual_packages": []
            })
            errors += 1
            continue

        # Parse output
        stdout_str = "".join(stdout_chunks)
        try:
            output_json = extract_json(stdout_str)
        except Exception as e:
            err_detail = get_stderr_detail("\n".join(stderr_lines), "failed to parse stdout JSON")
            results.append({
                "tc_id": tc_id,
                "result": "ERROR",
                "detail": err_detail,
                "expected_codes": expect_selected_codes,
                "actual_packages": []
            })
            errors += 1
            continue

        # Extract actual codes
        try:
            selected_packages = output_json.get("selected_packages", [])
            actual_packages = []
            for item in selected_packages:
                code = item["validated"]["procedure_code"]
                pkg_name = item["validated"].get("package_name") or "(name unavailable)"
                proc_name = item["validated"].get("procedure_name") or "(name unavailable)"
                name = f"{pkg_name} — {proc_name}"
                actual_packages.append({"code": code, "name": name})
            actual_codes = [pkg["code"] for pkg in actual_packages]
        except Exception as e:
            results.append({
                "tc_id": tc_id,
                "result": "ERROR",
                "detail": f"output JSON structure invalid: {e}",
                "expected_codes": expect_selected_codes,
                "actual_packages": []
            })
            errors += 1
            continue

        # Classification Logic
        expected_set = set(expect_selected_codes)
        actual_set = set(actual_codes)
        
        missing = expected_set - actual_set
        extra = actual_set - expected_set

        # Determine classification and detail
        if not actual_codes and expected_set:
            # override: actual_codes is completely empty and expected is non-empty
            result_type = "INCORRECT"
            missing_str = ", ".join(sorted(missing))
            detail = f"wrong packages — missing: {missing_str}"
            incorrect += 1
        elif not missing and not extra:
            result_type = "FULLY CORRECT"
            expected_len = len(expected_set)
            detail = f"{expected_len}/{expected_len} packages matched"
            fully_correct += 1
        elif not missing and extra:
            result_type = "PARTIALLY CORRECT"
            extra_str = ", ".join(sorted(extra))
            detail = f"all expected present; unexpected extra: {extra_str}"
            partially_correct += 1
        elif missing and not extra:
            result_type = "PARTIALLY CORRECT"
            missing_str = ", ".join(sorted(missing))
            detail = f"missing: {missing_str}"
            partially_correct += 1
        else:
            result_type = "INCORRECT"
            missing_str = ", ".join(sorted(missing))
            extra_str = ", ".join(sorted(extra))
            detail = f"wrong packages — missing: {missing_str} | unexpected: {extra_str}"
            incorrect += 1

        results.append({
            "tc_id": tc_id,
            "result": result_type,
            "detail": detail,
            "expected_codes": expect_selected_codes,
            "actual_packages": actual_packages
        })

    # Build Output Table Sorted Alphanumerically
    sorted_results = sorted(results, key=lambda r: r["tc_id"])
    
    table_lines = []
    table_lines.append("  ┌──────────┬──────────────────┬──────────────────────────────────────────┐")
    table_lines.append("  │ TC       │ Result           │ Detail                                   │")
    table_lines.append("  ├──────────┼──────────────────┼──────────────────────────────────────────┤")
    
    file_content = []
    file_content.append(start_time_dt.strftime("Eval run: %d-%m-%Y %H:%M"))
    file_content.append("")
    file_content.append("  ┌──────────┬──────────────────┬──────────────────────────────────────────┐")
    file_content.append("  │ TC       │ Result           │ Detail                                   │")
    file_content.append("  ├──────────┼──────────────────┼──────────────────────────────────────────┤")

    for i, r in enumerate(sorted_results):
        tc = r["tc_id"]
        result = r["result"]
        detail = r["detail"]
        
        lines = textwrap.wrap(detail, width=40) if detail else [""]
        if not lines:
            lines = [""]
            
        # Terminal row format
        table_row_first = f"  │ {tc:<8} │ {result:<16} │ {lines[0]:<40} │"
        table_lines.append(table_row_first)
        for line in lines[1:]:
            table_lines.append(f"  │ {'':<8} │ {'':<16} │ {line:<40} │")
            
        # File row format
        file_content.append(table_row_first)
        for line in lines[1:]:
            file_content.append(f"  │ {'':<8} │ {'':<16} │ {line:<40} │")
            
        # Detail block (only in file_content)
        expected_codes = r.get("expected_codes", [])
        if not expected_codes:
            file_content.append("    Expected : (none expected)")
        else:
            first_code = expected_codes[0]
            first_name = lookup_procedure_name(first_code, str(project_root))
            file_content.append(f"    Expected : {first_code} — {first_name}")
            for code in expected_codes[1:]:
                name = lookup_procedure_name(code, str(project_root))
                file_content.append(f"             : {code} — {name}")
                
        if result == "ERROR":
            file_content.append("    Chosen   : (pipeline did not complete)")
        elif result == "SKIP":
            file_content.append("    Chosen   : (not evaluated)")
        else:
            actual_packages = r.get("actual_packages", [])
            if not actual_packages:
                file_content.append("    Chosen   : (none selected)")
            else:
                first_pkg = actual_packages[0]
                file_content.append(f"    Chosen   : {first_pkg['code']} — {first_pkg['name']}")
                for pkg in actual_packages[1:]:
                    file_content.append(f"             : {pkg['code']} — {pkg['name']}")
                    
        # Blank line after detail block in file_content if not the last TC row
        if i < len(sorted_results) - 1:
            file_content.append("")
            
    # Table footers
    table_lines.append("  └──────────┴──────────────────┴──────────────────────────────────────────┘")
    file_content.append("  └──────────┴──────────────────┴──────────────────────────────────────────┘")

    summary_lines = []
    summary_lines.append(f"  TOTAL: {fully_correct} fully correct, {partially_correct} partially correct, {incorrect} incorrect, {skipped} skipped, ")
    summary_lines.append(f"         {errors} errors")

    final_line = "  ALL PASS" if (incorrect == 0 and errors == 0) else "  ISSUES DETECTED"

    # Print results to terminal
    for line in table_lines:
        print(line)
    print()  # blank line after table
    for line in summary_lines:
        print(line)
    print()  # blank line after summary
    print(final_line)

    # Save to timestamped file
    filename = start_time_dt.strftime("eval_%d_%m_%H_%M.txt")
    output_dir = project_root / "tests" / "output"
    
    file_content.append("")
    file_content.extend(summary_lines)
    file_content.append("")
    file_content.append(final_line)

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        dest_file = output_dir / filename
        with open(dest_file, "w", encoding="utf-8") as f:
            f.write("\n".join(file_content) + "\n")
    except Exception as e:
        print(f"\nWarning: Could not write evaluation results to {filename}: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
