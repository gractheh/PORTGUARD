"""
PORTGUARD — run all 3 test scenarios and print results.
Usage: python main.py
"""

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import app

# ---------------------------------------------------------------------------
# Terminal colours
# ---------------------------------------------------------------------------

BOLD  = "\033[1m"
DIM   = "\033[2m"
RESET = "\033[0m"

_DECISION_COLOUR = {
    "APPROVE":                  "\033[92m",   # green
    "REVIEW_RECOMMENDED":       "\033[93m",   # yellow
    "FLAG_FOR_INSPECTION":      "\033[33m",   # orange/dark yellow
    "REQUEST_MORE_INFORMATION": "\033[94m",   # blue
    "REJECT":                   "\033[91m",   # red
}

_RISK_COLOUR = {
    "LOW":      "\033[92m",
    "MEDIUM":   "\033[93m",
    "HIGH":     "\033[33m",
    "CRITICAL": "\033[91m",
}

def _colour(text: str, code: str) -> str:
    """Wrap text in an ANSI colour code if stdout is a real terminal."""
    if not sys.stdout.isatty():
        return text
    return f"{code}{text}{RESET}"

# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------

SCENARIOS = [
    {
        "number": 1,
        "label": "Clean Shipment",
        "description": "Vietnamese laptop manufacturer — all documents consistent",
        "file": "tests/sample_documents/01_clean_shipment.json",
        "expected": "APPROVE",
    },
    {
        "number": 2,
        "label": "Suspicious Shipment",
        "description": "Semiconductor ICs — shipper mismatch, transshipment indicators, undervaluation",
        "file": "tests/sample_documents/02_suspicious_shipment.json",
        "expected": "FLAG_FOR_INSPECTION",
    },
    {
        "number": 3,
        "label": "Incomplete Shipment",
        "description": "Frozen shrimp — missing FDA Prior Notice, no origin, absent ISF elements",
        "file": "tests/sample_documents/03_incomplete_shipment.json",
        "expected": "REQUEST_MORE_INFORMATION",
    },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_scenario(path: str) -> dict:
    full = Path(__file__).parent / path
    with open(full, encoding="utf-8") as f:
        data = json.load(f)
    # Strip internal _meta keys before sending to API
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _hr(char: str = "-", width: int = 70) -> str:
    return char * width


def _print_result(scenario: dict, response: dict) -> None:
    decision   = response["decision"]
    risk_level = response["risk_level"]
    risk_score = response["risk_score"]
    confidence = response["confidence"]
    n_docs     = response["documents_analyzed"]
    n_issues   = response["inconsistencies_found"]
    elapsed    = response["processing_time_seconds"]

    d_col = _DECISION_COLOUR.get(decision, "")
    r_col = _RISK_COLOUR.get(risk_level, "")

    print(_hr("="))
    print(
        f"{BOLD}Scenario {scenario['number']}:{RESET} {scenario['label']}"
        f"  {DIM}({scenario['description']}){RESET}"
    )
    print(_hr())

    # Decision — the headline
    print(f"  Decision   : {_colour(BOLD + decision + RESET, d_col)}")
    print(f"  Risk level : {_colour(risk_level, r_col)}  (score {risk_score:.2f})")
    print(f"  Confidence : {confidence}")
    print(f"  Docs read  : {n_docs}   Inconsistencies: {n_issues}   "
          f"Time: {elapsed:.2f}s")

    # Shipment data snapshot
    sd = response.get("shipment_data", {})
    origin  = sd.get("origin_country") or sd.get("origin_country_iso2") or "—"
    importer = sd.get("importer") or "—"
    commodity = sd.get("commodity_description") or "—"
    value = sd.get("declared_value") or "—"
    print()
    print(f"  Importer  : {importer}")
    print(f"  Origin    : {origin}")
    print(f"  Commodity : {commodity}")
    print(f"  Value     : {value}")

    # Explanations
    explanations = response.get("explanations", [])
    if explanations:
        print()
        print(f"  {BOLD}Findings:{RESET}")
        for line in explanations:
            print(f"    - {line}")

    # Next steps
    steps = response.get("recommended_next_steps", [])
    if steps:
        print()
        print(f"  {BOLD}Recommended next steps:{RESET}")
        for i, step in enumerate(steps, 1):
            print(f"    {i}. {step}")

    # Expected vs actual
    expected = scenario["expected"]
    match = decision == expected
    status_icon = "[PASS]" if match else "[FAIL]"
    status_text = "matches expected" if match else f"expected {expected}"
    status_col  = "\033[92m" if match else "\033[91m"
    print()
    print(f"  {_colour(status_icon + ' ' + status_text, status_col)}")
    print()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    client = TestClient(app, raise_server_exceptions=True)

    print()
    print(_hr("="))
    print(f"{BOLD}  PORTGUARD - Trade Compliance Screening{RESET}")
    print(f"  Running {len(SCENARIOS)} scenarios against POST /api/v1/analyze")
    print(_hr("="))
    print()

    passed = 0
    failed = 0
    errors = 0

    for scenario in SCENARIOS:
        try:
            payload = _load_scenario(scenario["file"])
        except FileNotFoundError:
            print(f"[ERROR] Scenario {scenario['number']}: file not found — {scenario['file']}")
            errors += 1
            continue

        print(f"  Running scenario {scenario['number']}: {scenario['label']}…", end="", flush=True)

        response = client.post("/api/v1/analyze", json=payload)

        if response.status_code != 200:
            print(f" HTTP {response.status_code}")
            print(f"  [ERROR] {response.text[:300]}")
            errors += 1
            continue

        print()  # newline after the ellipsis

        data = response.json()
        _print_result(scenario, data)

        if data["decision"] == scenario["expected"]:
            passed += 1
        else:
            failed += 1

    # Summary
    print(_hr("="))
    total = passed + failed + errors
    print(f"{BOLD}  Results: {passed}/{total} matched expected decisions{RESET}")
    if failed:
        print(f"  {_colour(f'{failed} unexpected decision(s)', _RISK_COLOUR['HIGH'])}")
    if errors:
        print(f"  {_colour(f'{errors} error(s)', _RISK_COLOUR['CRITICAL'])}")
    print(_hr("="))
    print()

    return 0 if (failed == 0 and errors == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
