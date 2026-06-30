# IRIS STG Document Key Tooling

This directory contains standalone tools to extract and semantically cluster mandatory pre-authorisation and claim document keys across all PM-JAY Standard Treatment Guideline (STG) JSON files in the repository.

---

## Script 1: Deterministic Key Extraction

`extract_stg_keys.py` is a deterministic standalone tool that extracts all document keys from the `data/stg/` directory (mapped from `config.py`) and compiles raw metadata lists for audit. It runs fully offline and makes no API calls.

### Running Script 1

Execute the script from the repository root:
```bash
python canonical_keys_tooling/extract_stg_keys.py
```

### Script 1 Outputs

Files are saved in `canonical_keys_tooling/output/`:
- **`flat_key_list.json`**: Primary JSON array of all extracted document key entries.
- **`flat_key_list.csv`**: Flat list in CSV format for spreadsheet analysis.
- **`run_log.txt`**: Console logs detailing parsing statistics, optional keys, top reused keys, and specialty breakdowns.

---

## Script 2: Semantic Clustering & Canonical Mapping

`cluster_keys.py` performs LLM-assisted semantic clustering of document keys for one or more user-specified specialties, merging the results additively into a growing canonical map file. It is designed to be run incrementally and repeatedly (e.g. specialty by specialty), controlled by command-line arguments.

### Running Script 2

Script 2 supports two execution modes:

#### Mode A: Fresh Clustering Run (Normal Use)
Specify one or more specialty search terms (case-insensitive substring matches).
```bash
python canonical_keys_tooling/cluster_keys.py "Cardiology"
python canonical_keys_tooling/cluster_keys.py "Cardiology" "ENT"
```
1. **Specialty Matching**: Scans `flat_key_list.json` and prints matched specialties with file counts. **Check this printed list against `run_log.txt` before approving the LLM cost.**
2. **Deduplication**: Collapses duplicate key-label pairs in the matched specialties.
3. **LLM Clustering**: Calls Gemini to group keys into semantic document clusters.
4. **Save Proposal**: Saves the raw proposal as a timestamped file in `output/review/` first.
5. **Interactive Confirmation**: Prints proposed clusters (with contributing files/specialties) and prompts `y/N` to apply.

#### Mode B: Resume from a Manually-Edited Proposal
If the LLM makes a bad proposal (e.g. wrong alias group or canonical key name), you can hand-edit the JSON proposal file in `output/review/` and re-run without re-calling the LLM:
```bash
python canonical_keys_tooling/cluster_keys.py --resume-from canonical_keys_tooling/output/review/cluster_proposal_cardiology_20260630_153012.json
```
This skips matching and LLM invocation, loads and validates the JSON structure (verifying it has a `clusters` array with `canonical_key`, `canonical_label`, `aliases`, and `reasoning`), and proceeds directly to interactive confirm-and-merge.

### Conflict Resolution

When merging proposed clusters into `canonical_map.json`, if any proposed key or alias already exists in the map under a different canonical key, you are prompted per-conflict:
- **`[M] Merge`**: Adds proposed aliases to the existing map entry's aliases.
- **`[K] Keep separate`**: Adds proposed cluster under a new canonical key (appending a unique suffix if there is a key collision).
- **`[S] Skip`**: Skips the proposed cluster, leaving the map unmodified.

### Script 2 Outputs

- **`output/canonical_map.json`**: The persistent, cumulative canonical map. Sorted alphabetically by canonical key for clean git diffs.
- **`output/review/cluster_proposal_<specialty_slug>_<timestamp>.json`**: The timestamped LLM proposal file.

---

## Next Steps: Script 3

A separate, future tool (Script 3) will perform a periodic cross-specialty consolidation pass over the full `canonical_map.json` to identify and resolve duplicates that arose from running different specialties independently.
