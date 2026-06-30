#!/usr/bin/env python3
"""
cluster_keys.py
===============
Script 2 of a 3-script system. Performs LLM-assisted semantic clustering of
document keys for a user-specified specialty (or specialties) and merges the
results additively into a persistent, growing canonical map file.
"""

import sys
import os
import json
import argparse
import datetime
import re
from pathlib import Path
from collections import defaultdict
from dotenv import load_dotenv

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables
load_dotenv()

# Import project-specific LLM config
from config import LLM_MODEL, QUERY_PREDICTOR_TIMEOUT_SECONDS, LLM_MAX_RETRIES

# Import genai SDK
try:
    from google import genai
except ImportError:
    genai = None

# Set up paths
TOOLING_DIR = Path(__file__).resolve().parent
JSON_INPUT = TOOLING_DIR / "output" / "flat_key_list.json"
MAP_OUT = TOOLING_DIR / "output" / "canonical_map.json"

SYSTEM_PROMPT = """You are a medical document metadata engineer for IRIS, an AI-powered PM-JAY claims verification engine.
Your task is to analyze a list of document keys and their associated labels, and cluster them semantically.

Each cluster must represent the SAME real-world physical document, clinical report, or administrative artifact, even if named or keyed differently in different Standard Treatment Guidelines (STGs).

For each cluster you produce:
1. canonical_key: A single, clean, standardized snake_case key representing the document (e.g., 'discharge_summary', 'ct_scan_report', 'intra_operative_photograph').
2. canonical_label: A clean, human-readable label for the document.
3. aliases: A list of every original key from the input list that belongs to this cluster.
4. reasoning: A one-sentence explanation of why these keys represent the same physical document or test.

CRITICAL RULES:
- BE CONSERVATIVE. If you are not confident that two keys represent the exact same document, or if the meaning of a key is ambiguous, DO NOT merge them. It is far safer to leave them as separate single-member clusters than to incorrectly group different documents together (which can cause silent validation failures later). When in doubt, do not merge.

- SAME ARTIFACT VS. DIFFERENT DIAGNOSTIC TESTS:
  - Correct to merge: Documents that are the exact same physical artifact, just named or described with different wording (e.g., 'discharge_summary' and 'detailed_discharge_summary').
  - Incorrect to merge: Documents that result from different diagnostic tests, different procedures, or different equipment, even if they serve a similar clinical purpose or belong to the same category. For example, an echocardiography report (an ultrasound-based test) and an angiography report (an X-ray/dye-based test) are two completely separate and distinct documents and MUST NOT be merged.
  
- THE HOSPITAL PRODUCTION TEST:
  Before merging two keys, ask yourself: "Would a hospital physically produce these as the same document from the same single test/procedure, just described differently—or would producing one require an entirely separate test, scan, procedure, or piece of equipment from producing the other?" If a different test, scan, procedure, or piece of equipment is required to produce the second document versus the first, they are NOT the same artifact and must NOT be merged, regardless of how thematically or clinically related they are.

- AVOID IMAGING MODALITY AND DEVICE RECORD CONFLATIONS:
  - Do NOT merge different imaging modalities. Keep ECHO/echocardiography, angiography/angiogram, Doppler ultrasound, CT/CTA (Computed Tomography/Angiography), and MRA (Magnetic Resonance Angiography) as separate, distinct canonical keys. They are different tests using different technologies, and STGs frequently require both separately (e.g., an STG requiring both "2d_echo" and "cag_report").
  - Do NOT merge device invoice/purchase documents with device barcode/serial-number labels (e.g., keep 'invoice_of_stent_used' and 'barcode_of_stent_used' in separate clusters). A barcode sticker from an implant box is a separate physical document from a commercial invoice/receipt.

- NO BRIDGING VIA BUNDLED/OR KEYS:
  Some keys in the corpus explicitly bundle multiple options or modalities together (e.g., a key representing "Doppler ultrasound/ Digital subtraction angiography/ Computed tomography angiography/ magnetic resonance angiography report"). These bundled/compound keys MUST remain in their own separate, single-member clusters. Do NOT use a bundled key as a "bridge" to merge individual, single-modality keys (like a standalone Doppler report or CT report) into it.

- Every input key MUST be accounted for. Every key must appear in exactly one cluster's aliases list. If a key is not merged, it should be in a single-member cluster where aliases is [key].
- You MUST respond with valid JSON only in the specified format. No explanations or markdown formatting outside the JSON object.

Output Schema:
{
  "clusters": [
    {
      "canonical_key": "string",
      "canonical_label": "string",
      "aliases": ["string"],
      "reasoning": "string"
    }
  ]
}
"""


def parse_llm_json(raw_text: str) -> dict | None:
    """Parse JSON response from the LLM using the 3 extraction strategies matching the repo pattern."""
    parsed = None

    # STRATEGY 1 — Direct parse
    text_s1 = raw_text.strip()
    try:
        res = json.loads(text_s1)
        if isinstance(res, dict):
            parsed = res
    except json.JSONDecodeError:
        pass

    # STRATEGY 2 — Strip markdown fences
    if parsed is None:
        lines = raw_text.splitlines()
        clean_lines = [line for line in lines if not line.strip().startswith("```")]
        text_s2 = "\n".join(clean_lines).strip()
        try:
            res = json.loads(text_s2)
            if isinstance(res, dict):
                parsed = res
        except json.JSONDecodeError:
            pass

    # STRATEGY 3 — Extract JSON object by brace scanning
    if parsed is None:
        first_brace = raw_text.find("{")
        last_brace = raw_text.rfind("}")
        if first_brace != -1 and last_brace != -1 and first_brace < last_brace:
            text_s3 = raw_text[first_brace : last_brace + 1]
            try:
                res = json.loads(text_s3)
                if isinstance(res, dict):
                    parsed = res
            except json.JSONDecodeError:
                pass

    return parsed


def deduplicate_proposal_aliases(proposal: dict) -> dict:
    """Deduplicate aliases within each cluster of the proposal while preserving order."""
    if not proposal or "clusters" not in proposal or not isinstance(proposal["clusters"], list):
        return proposal
    for cluster in proposal["clusters"]:
        if not isinstance(cluster, dict) or "aliases" not in cluster:
            continue
        aliases = cluster.get("aliases", [])
        if isinstance(aliases, list):
            seen = set()
            deduped = []
            for alias in aliases:
                if alias not in seen:
                    seen.add(alias)
                    deduped.append(alias)
            cluster["aliases"] = deduped
    return proposal


def main():
    parser = argparse.ArgumentParser(description="LLM-assisted semantic clustering of STG document keys.")
    parser.add_argument("specialty", nargs="*", help="Specialty search term(s) to cluster (e.g. 'Cardiology')")
    parser.add_argument("--resume-from", help="Resume clustering from a previously saved review JSON file.")
    args = parser.parse_args()

    # Validate Modes
    if args.resume_from:
        if args.specialty:
            parser.error("Cannot specify both specialty terms and --resume-from.")
        mode = "B"
    else:
        if not args.specialty:
            parser.error("Must specify either one or more specialty search terms, or --resume-from.")
        mode = "A"

    # Load flat key list
    if not JSON_INPUT.exists():
        print(f"Error: Input file not found: {JSON_INPUT}. Please run Script 1 first.")
        sys.exit(1)

    try:
        with open(JSON_INPUT, "r", encoding="utf-8") as f:
            flat_data = json.load(f)
    except Exception as e:
        print(f"Error: Failed to parse input file {JSON_INPUT}: {e}")
        sys.exit(1)

    # Build global key metadata for displaying contributing files & specialties
    global_key_metadata = defaultdict(lambda: {"files": set(), "specialties": set(), "labels": defaultdict(int)})
    for row in flat_data:
        k = row["key"]
        f = row["source_file"]
        sp = row.get("specialty", "")
        l = row.get("label", "")
        global_key_metadata[k]["files"].add(f)
        global_key_metadata[k]["specialties"].add(sp)
        if l:
            global_key_metadata[k]["labels"][l] += 1

    if mode == "A":
        # Mode A: Fresh clustering run
        # Build raw specialty mappings for validation & suggestions
        raw_specialty_files = defaultdict(set)
        raw_specialty_keys = defaultdict(set)
        for row in flat_data:
            raw_spec = row.get("specialty", "Unknown Specialty")
            raw_specialty_files[raw_spec].add(row.get("source_file"))
            raw_spec_key = row.get("key")
            if raw_spec_key:
                raw_specialty_keys[raw_spec].add(raw_spec_key)

        # Specialty matching logic (case-insensitive substring)
        matched_raw_specialties = set()
        print("=" * 60)
        print("SPECIALTY MATCHING CHECKPOINT")
        print("=" * 60)
        
        all_matched_files = set()
        all_matched_keys = set()
        
        for term in args.specialty:
            term_lower = term.lower()
            print(f"Matched specialty labels for search term '{term}':")
            term_matches = []
            for raw_spec in sorted(raw_specialty_files.keys()):
                if term_lower in raw_spec.lower():
                    matched_raw_specialties.add(raw_spec)
                    files = raw_specialty_files[raw_spec]
                    keys = raw_specialty_keys[raw_spec]
                    term_matches.append((raw_spec, len(files), len(keys)))
                    all_matched_files.update(files)
                    all_matched_keys.update(keys)
            if not term_matches:
                print("  - None")
            else:
                for raw_spec, f_count, k_count in term_matches:
                    print(f"  - '{raw_spec}' ({f_count} files, {k_count} unique keys)")
        
        print("-" * 60)
        print(f"Total matched: {len(all_matched_files)} files, {len(all_matched_keys)} unique keys")
        print("=" * 60)

        # Handle zero matches
        if not matched_raw_specialties:
            print(f"Error: No rows matched any of the provided search terms: {args.specialty}")
            # Suggest closest matches using rapidfuzz or difflib
            all_raw_specialties = list(raw_specialty_files.keys())
            suggestions = []
            try:
                from rapidfuzz import process, fuzz
                for term in args.specialty:
                    matches = process.extract(term, all_raw_specialties, scorer=fuzz.WRatio, limit=5)
                    for match in matches:
                        suggestions.append(match[0])
            except ImportError:
                import difflib
                for term in args.specialty:
                    matches = difflib.get_close_matches(term, all_raw_specialties, n=5, cutoff=0.3)
                    suggestions.extend(matches)
            
            suggestions = sorted(list(set(suggestions)))
            if suggestions:
                print("Did you mean one of these existing specialties?")
                for sug in suggestions:
                    print(f"  - {sug}")
            sys.exit(1)

        # Collect working set rows
        working_set = []
        for row in flat_data:
            if row.get("specialty") in matched_raw_specialties:
                working_set.append(row)

        # Deduplicate to unique keys, selecting the most common label for each key
        # to fix Bug 1 at the root cause.
        key_label_counts = defaultdict(lambda: defaultdict(int))
        for row in working_set:
            key_label_counts[row["key"]][row.get("label", "")] += 1

        unique_pairs = []
        for k, label_counts in key_label_counts.items():
            best_label = max(label_counts.keys(), key=lambda l: label_counts[l])
            unique_pairs.append({"key": k, "label": best_label})

        print(f"Deduplication summary:")
        print(f"  - Before deduplication: {len(working_set)} total entries")
        print(f"  - After deduplication: {len(unique_pairs)} unique (key, label) pairs")
        print("-" * 60)

        # LLM Clustering Call
        if not genai:
            print("Error: The google-genai library is not installed or import failed.")
            sys.exit(1)

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("Error: GEMINI_API_KEY environment variable is not set. Please check your .env file.")
            sys.exit(1)

        client = genai.Client(api_key=api_key)
        input_payload = {"keys_to_cluster": unique_pairs}
        user_prompt = f"Please cluster the following document keys according to the system rules:\n\n{json.dumps(input_payload, indent=2)}"

        print(f"Calling Gemini ({LLM_MODEL}) for semantic clustering...")
        raw_response = None
        for attempt in range(1, LLM_MAX_RETRIES + 1):
            try:
                response = client.models.generate_content(
                    model=LLM_MODEL,
                    contents=user_prompt,
                    config=genai.types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0,
                        max_output_tokens=65536,
                        response_mime_type="application/json",
                        http_options=genai.types.HttpOptions(
                            timeout=QUERY_PREDICTOR_TIMEOUT_SECONDS * 1000,
                        ),
                    ),
                )
                raw_response = response.text
                break
            except Exception as e:
                print(f"  Attempt {attempt} failed: {e}")
                if attempt == LLM_MAX_RETRIES:
                    print("Error: All LLM call attempts failed.")
                    sys.exit(1)

        # Parse LLM response
        proposal = parse_llm_json(raw_response)
        proposal = deduplicate_proposal_aliases(proposal)
        if not proposal or "clusters" not in proposal or not isinstance(proposal["clusters"], list):
            print("Error: Failed to parse a valid clusters JSON structure from the LLM response.")
            if raw_response:
                print("\nRaw response received:")
                print(raw_response)
            sys.exit(1)

        # Save raw proposal
        specialty_slug = "_".join(re.sub(r'[^a-zA-Z0-9]+', '_', term.lower()).strip('_') for term in args.specialty)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        review_dir = TOOLING_DIR / "output" / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        review_file_path = review_dir / f"cluster_proposal_{specialty_slug}_{timestamp}.json"

        try:
            with open(review_file_path, "w", encoding="utf-8") as rf:
                json.dump(proposal, rf, indent=2)
            print(f"Saved raw proposal to: {review_file_path}")
        except Exception as e:
            print(f"Warning: Failed to save review proposal file: {e}")

    else:
        # Mode B: Resume from manual-edited review file
        resume_path = Path(args.resume_from)
        if not resume_path.exists():
            print(f"Error: The specified review file does not exist: {resume_path}")
            sys.exit(1)

        try:
            with open(resume_path, "r", encoding="utf-8") as f:
                proposal = json.load(f)
            proposal = deduplicate_proposal_aliases(proposal)
        except Exception as e:
            print(f"Error: Failed to parse review file as JSON: {e}")
            sys.exit(1)

        # Validate proposal structure
        if not isinstance(proposal, dict) or "clusters" not in proposal or not isinstance(proposal["clusters"], list):
            print("Error: Invalid review file structure. Must contain a 'clusters' array at the top level.")
            sys.exit(1)

        for idx, c in enumerate(proposal["clusters"]):
            if not isinstance(c, dict):
                print(f"Error: Cluster entry at index {idx} is not an object.")
                sys.exit(1)
            req_keys = ["canonical_key", "canonical_label", "aliases", "reasoning"]
            missing = [rk for rk in req_keys if rk not in c]
            if missing:
                print(f"Error: Cluster entry at index {idx} is missing required fields: {', '.join(missing)}")
                sys.exit(1)
            if not isinstance(c["aliases"], list):
                print(f"Error: Cluster entry at index {idx} has a non-list 'aliases' field.")
                sys.exit(1)

        print(f"Successfully loaded and validated proposal from resume file: {resume_path}")

    # Print proposal in a readable format (Mode A and Mode B)
    print("\n" + "=" * 60)
    print("PROPOSED CLUSTERING PLAN")
    print("=" * 60)
    for idx, cluster in enumerate(proposal["clusters"], 1):
        c_key = cluster.get("canonical_key")
        c_label = cluster.get("canonical_label")
        aliases = cluster.get("aliases", [])
        reasoning = cluster.get("reasoning", "No reasoning provided.")
        
        print(f"Cluster #{idx}: {c_key} ({c_label})")
        print(f"  Reasoning: {reasoning}")
        print("  Aliases:")
        for alias in aliases:
            meta = global_key_metadata.get(alias, {"files": set(), "specialties": set(), "labels": {}})
            labels_dict = meta.get("labels", {})
            alias_label = max(labels_dict.keys(), key=lambda x: labels_dict[x]) if labels_dict else "Unknown Label"
            
            file_list = sorted(list(meta["files"]))
            spec_list = sorted(list(meta["specialties"]))
            
            # Up to 3 example source filenames
            examples = file_list[:3]
            examples_str = ", ".join(examples)
            if len(file_list) > 3:
                examples_str += "..."
                
            print(f"    - '{alias}' (label: \"{alias_label}\")")
            print(f"      appears in {len(file_list)} files")
            print(f"      across {len(spec_list)} distinct specialty labels")
            print(f"      examples: {examples_str}")
        print("-" * 60)

    # Interactive confirmation to apply to canonical map
    confirm = input("\nDo you want to apply this clustering proposal to canonical_map.json? (y/N): ").strip().lower()
    if confirm not in ("y", "yes"):
        print("Merge cancelled by user. Exiting.")
        sys.exit(0)

    # Load existing canonical map or start fresh
    current_map = {}
    if MAP_OUT.exists():
        try:
            with open(MAP_OUT, "r", encoding="utf-8") as f:
                current_map = json.load(f)
            if not isinstance(current_map, dict):
                print(f"Warning: Existing canonical map {MAP_OUT} is not a dictionary. Starting fresh.")
                current_map = {}
        except Exception as e:
            print(f"Warning: Failed to load existing canonical map: {e}. Starting fresh.")
            current_map = {}

    # Merge logic with conflict handling
    new_entries_added = 0
    conflicts_found = 0
    conflicts_resolved_merge = 0
    conflicts_resolved_keep = 0
    conflicts_resolved_skip = 0

    for cluster in proposal["clusters"]:
        prop_key = cluster["canonical_key"]
        prop_label = cluster["canonical_label"]
        prop_aliases = set(cluster["aliases"])
        prop_reasoning = cluster.get("reasoning", "")
        
        # Check for conflicts
        conflicting_existing_keys = []
        for existing_key, existing_val in current_map.items():
            existing_aliases = set(existing_val.get("aliases", []))
            existing_aliases.add(existing_key)
            
            # Check intersection
            if prop_aliases.intersection(existing_aliases) or prop_key == existing_key:
                conflicting_existing_keys.append(existing_key)
                
        if not conflicting_existing_keys:
            # No conflict - add as a brand new entry
            current_map[prop_key] = {
                "canonical_label": prop_label,
                "aliases": sorted(list(prop_aliases)),
                "reasoning": prop_reasoning
            }
            new_entries_added += 1
        else:
            conflicts_found += 1
            print("\n" + "!" * 60)
            print("CONFLICT DETECTED")
            print("!" * 60)
            print("Newly proposed cluster:")
            print(f"  Canonical Key:   {prop_key}")
            print(f"  Canonical Label: {prop_label}")
            print(f"  Aliases:         {sorted(list(prop_aliases))}")
            print(f"  Reasoning:       {prop_reasoning}")
            print("\nConflicting existing entry/entries in canonical_map.json:")
            for idx, ex_key in enumerate(conflicting_existing_keys, 1):
                ex_val = current_map[ex_key]
                print(f"  {idx}. Existing Key:   {ex_key}")
                print(f"     Existing Label: {ex_val.get('canonical_label')}")
                print(f"     Aliases:        {sorted(ex_val.get('aliases', []))}")
                print(f"     Reasoning:      {ex_val.get('reasoning', '')}")
                
            print("\nHow would you like to resolve this conflict?")
            print("  [M] Merge: Add proposed aliases into the existing entry")
            print("  [K] Keep separate: Add proposed cluster under a new canonical key")
            print("  [S] Skip: Do not modify the map for this proposed cluster")
            
            choice = ""
            while choice not in ("m", "k", "s"):
                choice = input("Enter choice [M/K/S]: ").strip().lower()
                
            if choice == "m":
                target_key = conflicting_existing_keys[0]
                existing_aliases = set(current_map[target_key].get("aliases", []))
                merged_aliases = existing_aliases.union(prop_aliases)
                current_map[target_key]["aliases"] = sorted(list(merged_aliases))
                
                existing_reasoning = current_map[target_key].get("reasoning", "")
                if prop_reasoning and prop_reasoning not in existing_reasoning:
                    current_map[target_key]["reasoning"] = f"{existing_reasoning} | Propose merge: {prop_reasoning}"
                print(f"Merged aliases into existing entry '{target_key}'.")
                conflicts_resolved_merge += 1
            elif choice == "k":
                final_key = prop_key
                if final_key in current_map:
                    suffix = 1
                    while f"{final_key}_{suffix}" in current_map:
                        suffix += 1
                    final_key = f"{final_key}_{suffix}"
                    print(f"Canonical key '{prop_key}' already exists. Using unique key: '{final_key}'")
                current_map[final_key] = {
                    "canonical_label": prop_label,
                    "aliases": sorted(list(prop_aliases)),
                    "reasoning": prop_reasoning
                }
                print(f"Added as separate entry under key '{final_key}'.")
                conflicts_resolved_keep += 1
            elif choice == "s":
                print("Skipped proposed cluster.")
                conflicts_resolved_skip += 1

    # Print final merge stats
    print("\n" + "=" * 60)
    print("MERGE EXECUTION SUMMARY")
    print("=" * 60)
    print(f"Brand new entries added:             {new_entries_added}")
    print(f"Conflicts encountered:               {conflicts_found}")
    print(f"  - Resolved via Merge:              {conflicts_resolved_merge}")
    print(f"  - Resolved via Keep Separate:      {conflicts_resolved_keep}")
    print(f"  - Resolved via Skip:               {conflicts_resolved_skip}")
    print(f"Final total canonical entry count:   {len(current_map)}")
    print("=" * 60)

    # Write updated map to disk with stable sort_keys for git diffs
    MAP_OUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(MAP_OUT, "w", encoding="utf-8") as f:
            json.dump(current_map, f, indent=2, sort_keys=True)
        print(f"Successfully updated canonical map at: {MAP_OUT}")
    except Exception as e:
        print(f"Error: Failed to write canonical map to {MAP_OUT}: {e}")


if __name__ == "__main__":
    main()
