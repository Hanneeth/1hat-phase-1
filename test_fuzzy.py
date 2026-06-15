# test_fuzzy.py — run from project root, doesn't touch any phase files
import json
# pyrefly: ignore [missing-import]
from rapidfuzz import fuzz

# Load index
with open("data/hbp/_index.json", encoding="utf-8") as f:
    index = json.load(f)

# Test inputs — change this string to test different cases
search_string = "thermal burns 25 percent TBSA LPG explosion flame burns bilateral upper limb"

print(f"Search: {search_string}\n")
print(f"Total index rows: {len(index)}\n")

# Score every row
results = []
for row in index:
    aliases = row.get("aliases", [])
    name = row.get("procedure_name", "")
    package_name = row.get("package_name", "")

    all_texts = aliases + [name, package_name]
    best_score = max(
        (fuzz.WRatio(search_string, text) for text in all_texts if text),
        default=0.0
    )
    results.append((best_score, row))

# Sort by score descending
results.sort(key=lambda x: x[0], reverse=True)

# Show top 20
print("TOP 20 MATCHES:")
print(f"{'Score':>6}  {'Code':<10}  {'Specialty':<6}  {'Package Name':<40}  {'Procedure Name'[:40]}")
print("-" * 110)
for score, row in results[:20]:
    print(
        f"{score:>6.1f}  "
        f"{row.get('procedure_code',''):<10}  "
        f"{row.get('specialty_code',''):<6}  "
        f"{row.get('package_name','')[:40]:<40}  "
        f"{row.get('procedure_name','')[:40]}"
    )

# Also show all BM rows specifically
print("\n\nALL BM ROWS AND THEIR SCORES:")
print(f"{'Score':>6}  {'Code':<10}  {'Package Name':<40}  {'Procedure Name'[:50]}")
print("-" * 110)
bm_rows = [(score, row) for score, row in results if row.get("specialty_code") == "BM"]
for score, row in sorted(bm_rows, key=lambda x: x[0], reverse=True):
    print(
        f"{score:>6.1f}  "
        f"{row.get('procedure_code',''):<10}  "
        f"{row.get('package_name','')[:40]:<40}  "
        f"{row.get('procedure_name','')[:50]}"
    )

# Show what score threshold cuts off
print(f"\n\nRows scoring >= 60: {sum(1 for s,_ in results if s >= 60)}")
print(f"Rows scoring >= 50: {sum(1 for s,_ in results if s >= 50)}")
print(f"Rows scoring >= 40: {sum(1 for s,_ in results if s >= 40)}")