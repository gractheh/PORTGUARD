#!/usr/bin/env python3
"""
Test: Pattern History Reset
============================
Verifies the full reset flow end-to-end via FastAPI TestClient.

Run with:
    .venv/bin/python test_pattern_reset.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from typing import Optional

# ── Set env vars before any import of api.app ───────────────────────────────
_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmpdb.close()
_DB_PATH = _tmpdb.name

os.environ["PORTGUARD_PATTERN_LEARNING_ENABLED"] = "true"
os.environ["PORTGUARD_PATTERN_DB_PATH"] = _DB_PATH

for mod in list(sys.modules.keys()):
    if mod.startswith("api") or mod.startswith("portguard"):
        del sys.modules[mod]

from fastapi.testclient import TestClient  # noqa: E402
from api.app import app, _pattern_db       # noqa: E402

client = TestClient(app)

# ── Harness ──────────────────────────────────────────────────────────────────

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
    print(f"\n\033[1m{'─' * 62}\033[0m")
    print(f"\033[1m  {title}\033[0m")
    print(f"\033[1m{'─' * 62}\033[0m")


def _suspicious_doc(n: int) -> dict:
    return {
        "filename": f"reset_test_{n}.txt",
        "raw_text": f"""BILL OF LADING  B/L No.: RESET-TEST-{n}
SHIPPER
Dragon Phoenix Trading Ltd
Room 1108, Fortune Plaza, Guangzhou, China

CONSIGNEE: TO ORDER
NOTIFY PARTY: Test Receiver LLC, Miami FL

PORT OF LOADING: Yantian, Shenzhen, China
PORT OF DISCHARGE: Port of Miami, Florida, USA

DESCRIPTION
8,000 pcs Monolithic Integrated Circuit  HTS 8542.31.0000
Country of Origin: China
Manufacturer: [not specified]
TOTAL: USD 6,800.00  ($0.85/unit)
""",
    }


def _analyze(doc: dict) -> dict:
    r = client.post("/api/v1/analyze", json={"documents": [doc]})
    assert r.status_code == 200, f"analyze returned {r.status_code}: {r.text[:200]}"
    return r.json()


def _history() -> dict:
    r = client.get("/api/v1/pattern-history")
    assert r.status_code == 200, f"pattern-history returned {r.status_code}: {r.text[:200]}"
    return r.json()


def _reset(confirm: bool) -> tuple[int, dict]:
    r = client.request(
        "DELETE",
        "/api/v1/pattern-history/reset",
        json={"confirm": confirm},
    )
    return r.status_code, r.json()


# ════════════════════════════════════════════════════════════════════════════
section("1  Baseline — confirm DB starts empty")
# ════════════════════════════════════════════════════════════════════════════

h = _history()
check("History starts at 0 shipments", h["total_shipments"] == 0,
      f"got {h['total_shipments']}")

# ════════════════════════════════════════════════════════════════════════════
section("2  Analyze 3 shipments to build up history")
# ════════════════════════════════════════════════════════════════════════════

ids = []
for i in range(1, 4):
    d = _analyze(_suspicious_doc(i))
    sid = d.get("shipment_id")
    print(f"  {INFO}  Shipment #{i}: id={sid[:8] if sid else 'None'}...  "
          f"risk={d.get('risk_score', 0):.3f}  decision={d.get('decision')}")
    ids.append(sid)

h = _history()
check("History shows 3 shipments after analysis",
      h["total_shipments"] == 3,
      f"got {h['total_shipments']}")
check("All 3 shipment_ids returned", all(ids),
      f"ids: {[s[:8] if s else None for s in ids]}")

# ════════════════════════════════════════════════════════════════════════════
section("3  Safeguard — reset without confirm=true must return 400")
# ════════════════════════════════════════════════════════════════════════════

status, body = _reset(confirm=False)
check("Reset with confirm=false returns 400",
      status == 400,
      f"got HTTP {status}")
check("400 body has CONFIRMATION_REQUIRED code",
      body.get("detail", {}).get("code") == "CONFIRMATION_REQUIRED",
      f"detail: {body.get('detail')}")

# History must be untouched after a rejected reset attempt
h_after_reject = _history()
check("History still 3 after rejected reset",
      h_after_reject["total_shipments"] == 3,
      f"got {h_after_reject['total_shipments']}")

# ════════════════════════════════════════════════════════════════════════════
section("4  Successful reset with confirm=true")
# ════════════════════════════════════════════════════════════════════════════

status, body = _reset(confirm=True)
print(f"  {INFO}  Response: HTTP {status}  body={body}")

check("Reset returns HTTP 200",
      status == 200,
      f"got HTTP {status}")
check("Response success=true",
      body.get("success") is True,
      f"got success={body.get('success')}")
check("Response message is correct",
      body.get("message") == "Pattern history cleared",
      f"got message='{body.get('message')}'")
check("Response reports 3 shipments deleted",
      body.get("shipments_deleted") == 3,
      f"got shipments_deleted={body.get('shipments_deleted')}")

# ════════════════════════════════════════════════════════════════════════════
section("5  Verify DB is empty after reset")
# ════════════════════════════════════════════════════════════════════════════

h = _history()
check("History shows 0 shipments after reset",
      h["total_shipments"] == 0,
      f"got {h['total_shipments']}")
check("History shows 0 confirmed fraud after reset",
      h["total_confirmed_fraud"] == 0,
      f"got {h['total_confirmed_fraud']}")
check("Top riskiest shippers is empty after reset",
      h["top_riskiest_shippers"] == [],
      f"got {h['top_riskiest_shippers']}")
check("Top riskiest routes is empty after reset",
      h["top_riskiest_routes"] == [],
      f"got {h['top_riskiest_routes']}")

# ════════════════════════════════════════════════════════════════════════════
section("6  Post-reset: next shipment gets cold start behavior")
# ════════════════════════════════════════════════════════════════════════════

# Dragon Phoenix was in the DB before reset — after reset it is unknown again.
d = _analyze(_suspicious_doc(99))
sid_post = d.get("shipment_id")
hist_post = d.get("history_available", True)   # expect False
pscore_post = d.get("pattern_score")
signals_post = d.get("pattern_signals", [])

print(f"  {INFO}  Post-reset shipment: id={sid_post[:8] if sid_post else 'None'}... "
      f"risk={d.get('risk_score', 0):.3f}  history_available={hist_post}  "
      f"pattern_score={pscore_post}")
print(f"  {INFO}  Signals: {signals_post}")

check("Post-reset shipment history_available=False (cold start)",
      hist_post is False,
      f"history_available={hist_post}")
check("Post-reset shipment pattern_score is None or near neutral",
      pscore_post is None or pscore_post <= 0.50,
      f"pattern_score={pscore_post}")
check("Post-reset signals contain cold start / insufficient history message",
      any("insufficient" in s.lower() or "history" in s.lower()
          for s in signals_post),
      f"signals: {signals_post}")

# ════════════════════════════════════════════════════════════════════════════
section("7  History counter increments from zero after reset")
# ════════════════════════════════════════════════════════════════════════════

h = _history()
check("History shows exactly 1 shipment after post-reset analysis",
      h["total_shipments"] == 1,
      f"got {h['total_shipments']}")

# ════════════════════════════════════════════════════════════════════════════
section("8  Second consecutive reset (idempotency)")
# ════════════════════════════════════════════════════════════════════════════

status2, body2 = _reset(confirm=True)
check("Second reset returns HTTP 200",
      status2 == 200,
      f"got HTTP {status2}")
check("Second reset reports 1 shipment deleted",
      body2.get("shipments_deleted") == 1,
      f"got shipments_deleted={body2.get('shipments_deleted')}")

h_final = _history()
check("History is 0 after second reset",
      h_final["total_shipments"] == 0,
      f"got {h_final['total_shipments']}")

# ════════════════════════════════════════════════════════════════════════════
section("9  Core analysis still works — reset does not break the pipeline")
# ════════════════════════════════════════════════════════════════════════════

clean_doc = {
    "filename": "clean_post_reset.txt",
    "raw_text": """OCEAN BILL OF LADING  B/L Number: POST-RESET-01
SHIPPER
Viet Star Electronics Manufacturing Co., Ltd.
18 Thang Long Industrial Park, Hanoi, Vietnam

CONSIGNEE
Horizon Technology Group, Inc.
1200 Brickell Avenue, Miami, FL 33131, USA

PORT OF LOADING: Cat Lai, Ho Chi Minh City, Vietnam
PORT OF DISCHARGE: Port of Los Angeles, California, USA

DESCRIPTION OF GOODS
Laptop Computers  HTS Code: 8471.30.0100  Country of Origin: Vietnam
4,000 units  Unit Price: USD 52.00  TOTAL: USD 208,000.00
FREIGHT: PREPAID
""",
}

d_clean = _analyze(clean_doc)
check("Clean shipment post-reset still returns APPROVE",
      d_clean.get("decision") == "APPROVE",
      f"got decision={d_clean.get('decision')}  risk={d_clean.get('risk_score', 0):.3f}")
check("Clean shipment post-reset returns a shipment_id",
      bool(d_clean.get("shipment_id")),
      f"shipment_id={d_clean.get('shipment_id')}")

# ════════════════════════════════════════════════════════════════════════════
section("SUMMARY")
# ════════════════════════════════════════════════════════════════════════════

passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
total  = len(results)

print(f"\n  Scenarios: 9   Checks: {total}   "
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

# Clean up
try:
    os.unlink(_DB_PATH)
except Exception:
    pass

sys.exit(0 if failed == 0 else 1)
