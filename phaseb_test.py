from kb.loader import load_index, load_stg
from stubs.bis_stub import verify_bis
from stubs.hem_stub import check_empanelment
index = load_index()
print(f"Index loaded: {len(index)} entries")
patient = verify_bis("P001")
print(f"Patient: {patient.name if patient else 'NOT FOUND'}")
hospital = check_empanelment("H001")
print(f"Hospital: {hospital.name}")
stg = load_stg("BM001A")
print(f"STG BM001A: {'found' if stg else 'missing'}")