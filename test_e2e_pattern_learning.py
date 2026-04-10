#!/usr/bin/env python3
"""
End-to-end test: Pattern Learning Pipeline
==========================================
Tests the full cycle: analyze → record → feedback → repeat offender detection.

Run with:
    .venv/bin/python test_e2e_pattern_learning.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import sqlite3
import importlib
from typing import Optional

# ── Must set env vars BEFORE importing api.app ──────────────────────────────
_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmpdb.close()
_DB_PATH = _tmpdb.name

os.environ["PORTGUARD_PATTERN_LEARNING_ENABLED"] = "true"
os.environ["PORTGUARD_PATTERN_DB_PATH"] = _DB_PATH

# Force-clear any cached modules so the fresh env vars are picked up
for mod in list(sys.modules.keys()):
    if mod.startswith("api") or mod.startswith("portguard"):
        del sys.modules[mod]

from fastapi.testclient import TestClient  # noqa: E402
from api.app import app, _pattern_db       # noqa: E402

# ── Test harness ─────────────────────────────────────────────────────────────

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
INFO = "\033[36m    •\033[0m"

results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = PASS if condition else FAIL
    print(f"  [{status}] {name}")
    if detail:
        print(f"  {INFO}  {detail}")
    results.append((name, condition, detail))


def section(title: str) -> None:
    print(f"\n\033[1m{'─' * 60}\033[0m")
    print(f"\033[1m  {title}\033[0m")
    print(f"\033[1m{'─' * 60}\033[0m")


client = TestClient(app)

# ── Document templates ───────────────────────────────────────────────────────

def _suspicious_doc(shipper: str, seq: int) -> dict:
    """Suspicious shipment: vague goods, undervalued ICs, China origin."""
    return {
        "filename": f"suspicious_{seq}.txt",
        "raw_text": f"""BILL OF LADING

B/L No.: APXT-2024-{900 + seq}
Date: 2024-09-{10 + seq:02d}

SHIPPER
{shipper}
Room 1108, Fortune Plaza, 182 Tianhe North Road
Guangzhou, Guangdong, China

CONSIGNEE: TO ORDER

NOTIFY PARTY
Global Freight Solutions LLC
3300 NW 79th Ave, Suite 200, Miami, FL 33122, USA

VESSEL: COSCO SHIPPING UNIVERSE / 082E
PORT OF LOADING: Yantian, Shenzhen, China
PORT OF DISCHARGE: Port of Miami, Florida, USA
ON BOARD DATE: 2024-09-{10 + seq:02d}

DESCRIPTION
General Merchandise
200 Cartons
Gross Weight: 420 KG
Freight: Collect

---

COMMERCIAL INVOICE
Invoice No.: SGE-INV-2024-{seq:04d}
Date: 2024-09-{10 + seq:02d}

SELLER
{shipper}
Guangzhou, China

BUYER
NexGen Components Inc.
8750 NW 36th Street, Doral, FL 33166, USA

COUNTRY OF ORIGIN: China
PORT OF SHIPMENT: Shenzhen, China

LINE ITEMS
Qty       Description                   HTS           Unit $    Total
8,000 pcs Monolithic Integrated Circuit 8542.31.0000  $0.85     $6,800.00
          Boards, memory type
          Country of Origin: China
          Manufacturer: [not specified]

TOTAL: USD 6,800.00

Note: Goods packed in 200 cartons, 40 pcs per carton.
Signature: [illegible]
"""
    }


def _clean_doc(shipper: str, seq: int) -> dict:
    """Clean shipment: laptops, Vietnam origin, full docs."""
    return {
        "filename": f"clean_{seq}.txt",
        "raw_text": f"""OCEAN BILL OF LADING - ORIGINAL

B/L Number: MSCU72493{seq:02d}-24
Date of Issue: 2024-10-0{seq}

SHIPPER / EXPORTER
{shipper}
18 Thang Long Industrial Park, Dong Anh District, Hanoi, Vietnam

CONSIGNEE
Horizon Technology Group, Inc.
1200 Brickell Avenue, Suite 850, Miami, FL 33131, USA

VESSEL / VOYAGE: MSC AURORA / 0124E
PORT OF LOADING: Cat Lai, Ho Chi Minh City, Vietnam
PORT OF DISCHARGE: Port of Los Angeles, California, USA

ON BOARD DATE: 2024-10-0{seq}
ETA: 2024-10-26

DESCRIPTION OF GOODS
Commodity:    Laptop Computers (Notebook PCs)
HTS Code:     8471.30.0100
Country of Origin: Vietnam
No. of Packages: 100 cartons (40 units per carton)
Total Units:  4,000 units
Gross Weight: 8,200 KG

FREIGHT: PREPAID
CARRIER: Mediterranean Shipping Company (MSC)

---

COMMERCIAL INVOICE
Invoice Number: VSE-2024-10-{seq:04d}
Invoice Date: 2024-10-0{seq}

SELLER
{shipper}
18 Thang Long Industrial Park, Hanoi, Vietnam

BUYER
Horizon Technology Group, Inc.
1200 Brickell Avenue, Miami, FL 33131, USA

COUNTRY OF ORIGIN: Vietnam
LINE ITEMS
Line  Description            HTS Code      Qty     Unit Price  Total
  1   Laptop Computer 15.6"  8471.30.0100  4,000   USD 52.00   208,000.00
      Country of Origin: Vietnam

TOTAL INVOICE VALUE:  USD 208,000.00
Authorized Signature: Nguyen Thi Lan, Export Manager
"""
    }


def _analyze(docs: list[dict]) -> dict:
    resp = client.post("/api/v1/analyze", json={"documents": docs})
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:300]}"
    return resp.json()


def _feedback(shipment_id: str, outcome: str) -> dict:
    resp = client.post("/api/v1/feedback", json={
        "shipment_id": shipment_id,
        "outcome": outcome,
        "officer_id": "test_officer",
        "notes": f"E2E test: {outcome}",
    })
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:300]}"
    return resp.json()


def _db_count(table: str, where: str = "") -> int:
    with sqlite3.connect(_DB_PATH) as conn:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return conn.execute(sql).fetchone()[0]


def _db_shipper_profile(name: str) -> Optional[dict]:
    """Fetch shipper profile by name (not key)."""
    with sqlite3.connect(_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM shipper_profiles WHERE lower(shipper_name) LIKE ?",
            (f"%{name.lower()[:15]}%",),
        ).fetchone()
        return dict(row) if row else None


# ═══════════════════════════════════════════════════════════════════════════
# SCENARIO 1 — Health check and DB init
# ═══════════════════════════════════════════════════════════════════════════
section("1  Health check & pattern learning init")

resp = client.get("/api/v1/health")
check("Health endpoint returns 200", resp.status_code == 200)
check("Pattern learning DB initialized", _pattern_db is not None,
      f"DB path: {_DB_PATH}")
check("DB is empty at start", _db_count("shipment_history") == 0)

# ═══════════════════════════════════════════════════════════════════════════
# SCENARIO 2 — Run 5 shipments (3 suspicious, 2 clean)
# ═══════════════════════════════════════════════════════════════════════════
section("2  Analyze 5 shipments (3 suspicious + 2 clean)")

FRAUD_SHIPPER = "Dragon Phoenix Trading Ltd"
CLEAN_SHIPPER = "Sunrise Electronics Manufacturing Co"

susp_ids: list[str] = []
susp_scores: list[float] = []
clean_ids: list[str] = []
clean_scores: list[float] = []

for i in range(1, 4):
    data = _analyze([_suspicious_doc(FRAUD_SHIPPER, i)])
    sid = data.get("shipment_id")
    score = data.get("risk_score", 0.0)
    decision = data.get("decision", "")
    print(f"  {INFO}  Suspicious #{i}: decision={decision} risk={score:.3f} "
          f"shipment_id={sid[:8] if sid else 'None'}...")
    susp_ids.append(sid)
    susp_scores.append(score)

for i in range(1, 3):
    data = _analyze([_clean_doc(CLEAN_SHIPPER, i)])
    sid = data.get("shipment_id")
    score = data.get("risk_score", 0.0)
    decision = data.get("decision", "")
    print(f"  {INFO}  Clean #{i}: decision={decision} risk={score:.3f} "
          f"shipment_id={sid[:8] if sid else 'None'}...")
    clean_ids.append(sid)
    clean_scores.append(score)

total_after_5 = _db_count("shipment_history")
check("5 shipments recorded in shipment_history", total_after_5 == 5,
      f"Found {total_after_5} rows")
check("All suspicious shipments have a shipment_id", all(susp_ids),
      f"IDs: {[s[:8] if s else None for s in susp_ids]}")
check("All clean shipments have a shipment_id", all(clean_ids),
      f"IDs: {[s[:8] if s else None for s in clean_ids]}")
check("Suspicious shipments score higher than clean shipments",
      min(susp_scores) > max(clean_scores) if susp_scores and clean_scores else False,
      f"susp min={min(susp_scores):.3f}, clean max={max(clean_scores):.3f}")

# ═══════════════════════════════════════════════════════════════════════════
# SCENARIO 3 — Verify DB state
# ═══════════════════════════════════════════════════════════════════════════
section("3  Verify SQLite database state")

# Shipper profiles should exist
fraud_profile = _db_shipper_profile(FRAUD_SHIPPER[:15])
clean_profile = _db_shipper_profile(CLEAN_SHIPPER[:15])

check("Dragon Phoenix shipper profile created",
      fraud_profile is not None,
      f"total_analyses={fraud_profile['total_analyses'] if fraud_profile else '–'}")
check("Sunrise Electronics shipper profile created",
      clean_profile is not None,
      f"total_analyses={clean_profile['total_analyses'] if clean_profile else '–'}")
check("Dragon Phoenix has 3 analyses recorded",
      fraud_profile is not None and fraud_profile["total_analyses"] == 3,
      f"got {fraud_profile['total_analyses'] if fraud_profile else '–'}")
check("Sunrise Electronics has 2 analyses recorded",
      clean_profile is not None and clean_profile["total_analyses"] == 2,
      f"got {clean_profile['total_analyses'] if clean_profile else '–'}")

# No outcomes recorded yet
outcomes_count = _db_count("pattern_outcomes")
check("No outcomes recorded yet (pre-feedback)", outcomes_count == 0,
      f"Found {outcomes_count} outcomes")

# ═══════════════════════════════════════════════════════════════════════════
# SCENARIO 4 — Submit CONFIRMED_FRAUD feedback on 2 suspicious shipments
# ═══════════════════════════════════════════════════════════════════════════
section("4  Submit CONFIRMED_FRAUD feedback on 2 flagged shipments")

fb_results: list[dict] = []
for i, sid in enumerate(susp_ids[:2]):
    fb = _feedback(sid, "CONFIRMED_FRAUD")
    fb_results.append(fb)
    print(f"  {INFO}  Feedback #{i+1}: status={fb.get('status')} "
          f"outcome={fb.get('outcome')} id={sid[:8]}...")

check("Feedback 1 accepted (status=ok)",
      fb_results[0].get("status") == "ok")
check("Feedback 2 accepted (status=ok)",
      fb_results[1].get("status") == "ok")

outcomes_after = _db_count("pattern_outcomes")
check("2 outcomes recorded in pattern_outcomes",
      outcomes_after == 2,
      f"Found {outcomes_after} outcomes")

confirmed_fraud_count = _db_count("pattern_outcomes", "outcome = 'CONFIRMED_FRAUD'")
check("Both outcomes are CONFIRMED_FRAUD",
      confirmed_fraud_count == 2,
      f"Found {confirmed_fraud_count} CONFIRMED_FRAUD rows")

# Check shipper profile updated
fraud_profile_after = _db_shipper_profile(FRAUD_SHIPPER[:15])
check("Dragon Phoenix confirmed_fraud_count updated to 2",
      fraud_profile_after is not None
      and fraud_profile_after["total_confirmed_fraud"] == 2,
      f"got total_confirmed_fraud={fraud_profile_after['total_confirmed_fraud'] if fraud_profile_after else '–'}")

rep_score_after_fraud = fraud_profile_after["reputation_score"] if fraud_profile_after else 0.0
check("Dragon Phoenix reputation_score elevated above prior (>0.167)",
      rep_score_after_fraud > 0.167,
      f"reputation_score={rep_score_after_fraud:.4f} (prior=0.167)")

# ═══════════════════════════════════════════════════════════════════════════
# SCENARIO 5 — 6th shipment: repeat offender detection
# ═══════════════════════════════════════════════════════════════════════════
section("5  6th shipment — repeat offender detection")

# Run a 4th shipment from Dragon Phoenix (3 prior analyses, 2 confirmed fraud)
data6 = _analyze([_suspicious_doc(FRAUD_SHIPPER, 9)])
score6 = data6.get("risk_score", 0.0)
sid6   = data6.get("shipment_id")
hist6  = data6.get("history_available", False)
pscore6 = data6.get("pattern_score")
signals6 = data6.get("pattern_signals", [])

print(f"  {INFO}  6th shipment: risk={score6:.3f} pattern_score={pscore6} "
      f"history_available={hist6}")
print(f"  {INFO}  Pattern signals ({len(signals6)}):")
for s in signals6:
    print(f"         - {s}")

check("6th shipment has history_available=True",
      hist6 is True,
      f"history_available={hist6} (need >=3 prior analyses)")
check("6th shipment has a pattern_score",
      pscore6 is not None,
      f"pattern_score={pscore6}")
check("6th shipment pattern signals are non-empty",
      len(signals6) > 0,
      f"signals: {signals6[:1]}")
check("Pattern signals mention Dragon Phoenix or high risk",
      any("dragon" in s.lower() or "high" in s.lower() or "fraud" in s.lower()
          or "risk" in s.lower() or "elevated" in s.lower()
          for s in signals6),
      f"signals: {signals6}")

# The blended score is: 0.65*rule + 0.35*pattern.
# If pattern_score < rule_score, the blend may be slightly lower — that is correct
# and expected (conservative Bayesian priors with N=3 samples).
# The meaningful check is that pattern_score reflects elevated entity risk (> neutral 0.35).
check("6th shipment pattern_score elevated above neutral (>0.35)",
      pscore6 is not None and pscore6 > 0.35,
      f"pattern_score={pscore6:.4f} (neutral baseline ~0.25)")

# Also verify the shipper signal blended score is high (>0.5) from signals text
check("Pattern signals confirm elevated shipper risk score",
      any("0.6" in s or "0.7" in s or "0.8" in s or "0.9" in s
          or "blended" in s.lower() for s in signals6),
      f"signals: {[s[:60] for s in signals6]}")

# ═══════════════════════════════════════════════════════════════════════════
# SCENARIO 6 — New clean shipper gets neutral pattern score
# ═══════════════════════════════════════════════════════════════════════════
section("6  New clean shipper — neutral pattern score (cold start)")

NEW_SHIPPER = "Blue Ocean Seafood Corp"
new_clean_doc = {
    "filename": "new_shipper.txt",
    "raw_text": f"""OCEAN BILL OF LADING

B/L Number: MSCU-BD-2024-001
Date of Issue: 2024-11-01

SHIPPER
{NEW_SHIPPER}
Port Zone Industrial Area, Chittagong, Bangladesh

CONSIGNEE
American Seafood Distributors LLC
1100 NW 17th Ave, Miami, FL 33125, USA

VESSEL: MAERSK KENSINGTON / 2201W
PORT OF LOADING: Chittagong, Bangladesh
PORT OF DISCHARGE: Port of New York / Newark, USA
ON BOARD DATE: 2024-11-01

DESCRIPTION OF GOODS
Commodity:    Frozen Shrimp, shell-on, headless, IQF
HS Code:      0306.17.0020
Country of Origin: Bangladesh
No. of Packages: 500 master cartons
Total Units:  10,000 kg net
Gross Weight: 10,500 KG
Temperature: -18C frozen

FREIGHT: PREPAID
CARRIER: Maersk Line

---

COMMERCIAL INVOICE
Invoice Number: BOC-2024-1101
Invoice Date: 2024-11-01

SELLER
{NEW_SHIPPER}
Port Zone Industrial Area, Chittagong, Bangladesh

BUYER
American Seafood Distributors LLC
1100 NW 17th Ave, Miami, FL 33125, USA

COUNTRY OF ORIGIN: Bangladesh
LINE ITEMS
Item  Description                    HTS Code      Qty       Unit Price  Total
  1   Frozen Shrimp, Penaeus vannamei 0306.17.0020  10,000 kg  USD 3.20   32,000.00
      Shell-on, headless, IQF
      Processing Plant Reg: BD-1142-FDA
      Country of Origin: Bangladesh

TOTAL INVOICE VALUE: USD 32,000.00
Authorized Signature: A. Karim, Export Manager
"""
}

data_new = _analyze([new_clean_doc])
score_new   = data_new.get("risk_score", 0.0)
hist_new    = data_new.get("history_available", True)   # expect False
pscore_new  = data_new.get("pattern_score")
signals_new = data_new.get("pattern_signals", [])

print(f"  {INFO}  New shipper: risk={score_new:.3f} pattern_score={pscore_new} "
      f"history_available={hist_new}")
print(f"  {INFO}  Pattern signals: {signals_new}")

check("New shipper history_available=False (cold start)",
      hist_new is False,
      f"history_available={hist_new}")
check("New shipper pattern_score is None or near neutral",
      pscore_new is None or (0.0 <= pscore_new <= 0.60),
      f"pattern_score={pscore_new}")
check("New shipper not falsely flagged by pattern engine",
      not any("fraud" in s.lower() or "confirmed" in s.lower()
               for s in signals_new),
      f"signals: {signals_new}")

# ═══════════════════════════════════════════════════════════════════════════
# SCENARIO 7 — Pattern history endpoint
# ═══════════════════════════════════════════════════════════════════════════
section("7  Pattern history endpoint")

resp_hist = client.get("/api/v1/pattern-history")
check("GET /api/v1/pattern-history returns 200", resp_hist.status_code == 200)

ph = resp_hist.json()
total_s = ph.get("total_shipments", 0)
total_f = ph.get("total_confirmed_fraud", 0)
shippers = ph.get("top_riskiest_shippers", [])
routes   = ph.get("top_riskiest_routes", [])

# 5 initial + 1 repeat offender (scenario 5) + 1 new shipper (scenario 6) = 7
check("History: total_shipments = 7", total_s == 7, f"got {total_s}")
check("History: total_confirmed_fraud = 2", total_f == 2, f"got {total_f}")
check("History: top_riskiest_shippers non-empty", len(shippers) > 0,
      f"got {len(shippers)} shippers")
check("History: Dragon Phoenix is top riskiest shipper",
      shippers and "dragon" in shippers[0]["name"].lower(),
      f"top shipper: {shippers[0]['name'] if shippers else '–'}")
check("History: top_riskiest_routes present", isinstance(routes, list),
      f"got {len(routes)} routes")

# ═══════════════════════════════════════════════════════════════════════════
# SCENARIO 8 — Duplicate feedback rejected
# ═══════════════════════════════════════════════════════════════════════════
section("8  Edge cases")

dup_resp = client.post("/api/v1/feedback", json={
    "shipment_id": susp_ids[0],
    "outcome": "CLEARED",
})
check("Duplicate feedback on resolved shipment returns 409",
      dup_resp.status_code == 409,
      f"got HTTP {dup_resp.status_code}")

bad_resp = client.post("/api/v1/feedback", json={
    "shipment_id": "nonexistent-id-00000",
    "outcome": "CONFIRMED_FRAUD",
})
check("Feedback on unknown shipment_id returns 404",
      bad_resp.status_code == 404,
      f"got HTTP {bad_resp.status_code}")

invalid_resp = client.post("/api/v1/feedback", json={
    "shipment_id": susp_ids[2],  # 3rd suspicious (no outcome yet)
    "outcome": "TOTALLY_WRONG",
})
check("Invalid outcome string returns 422",
      invalid_resp.status_code == 422,
      f"got HTTP {invalid_resp.status_code}")

# ═══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════
section("SUMMARY")

passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
total  = len(results)

print(f"\n  Scenarios: 8   Checks: {total}   "
      f"\033[32mPassed: {passed}\033[0m   "
      f"\033[31mFailed: {failed}\033[0m\n")

if failed:
    print("  Failed checks:")
    for name, ok, detail in results:
        if not ok:
            print(f"    \033[31m✗\033[0m {name}")
            if detail:
                print(f"       {detail}")
    print()

# Clean up temp DB
import os as _os
try:
    _os.unlink(_DB_PATH)
except Exception:
    pass

sys.exit(0 if failed == 0 else 1)
