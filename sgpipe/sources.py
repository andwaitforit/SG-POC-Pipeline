"""Mocked data sources.

Everything here stands in for something behind Safe-Guard's firewall:
  fetch_rates  -> the real getRates API call
  vin_lookup   -> the Salesforce / Postgres / SQL Server lookups

The SHAPE is real. Column names, product codes, coverage codes, form numbers,
program ids and the 18-option fan-out per seed row were all taken from the
actual AMZ_Rates_Response table in the Safe Guard POC workspace. Only the
transport is faked, so replacing these two functions with live HTTP and a DB
driver is the entire "go live" change.
"""
import csv
import hashlib
import os
import random

from . import config

# Product catalogue, verbatim from the real response.
PROGRAM = {
    "programType": "Standard",
    "programId": "009000",
    "programName": "BMW US GROUP FINANCIAL SERVICES",
    "sellerId": "G1796611",
    "regulatedRate": "NON-REG",
    "retailPrice": "0",
    "deductibleAmount": "0",
    "termMileage": "0",
}

PRODUCTS = {
    "BMMC": {
        "productCategory": "P", "productType": "PROTECTION",
        "formNumber": "BMPDWLKSA-CA 4/22",
        "productDescription": "BMW MULTICOVERAGE PROTECTION",
        "coverageCode": "BMCPPLAT-B2",
        "coverageDescription": "PLATINUM PROTECTION WITH COSMETIC",
        "financeType": "",
    },
    "BMGP": {
        "productCategory": "G", "productType": "GAP",
        "formNumber": "BMGPCA0823",
        "productDescription": "BMW GAP PROTECTION",
        "coverageCode": "BMGPGAP-ICE",
        "coverageDescription": "BMW GAP PROTECTION",
        "financeType": "FINANCE",
    },
    "BMLS": {
        "productCategory": "L", "productType": "LWT",
        "formNumber": "BMLS3/25",
        "productDescription": "BMW EXTENDED LEASE PROTECT",
        "coverageCode": "BMLSWS12",
        "coverageDescription": "BMW LEASE PROTECTION",
        "financeType": "LEASE",
    },
}

# (productCode, termMonthsMin, termMonthsMax, baseSellerCost) - the real ladder,
# 14 PROTECTION + 3 GAP + 1 LWT = the 18 options one seed row returns.
RATE_LADDER = [
    ("BMMC", 12, 12, 1052), ("BMMC", 13, 18, 1560), ("BMMC", 19, 24, 1560),
    ("BMMC", 25, 30, 1808), ("BMMC", 31, 36, 1808), ("BMMC", 37, 39, 1936),
    ("BMMC", 40, 42, 1936), ("BMMC", 43, 48, 1936), ("BMMC", 49, 54, 2153),
    ("BMMC", 55, 60, 2153), ("BMMC", 61, 66, 2379), ("BMMC", 67, 72, 2379),
    ("BMMC", 73, 75, 2579), ("BMMC", 76, 84, 2579),
    ("BMGP", 1, 60, 275), ("BMGP", 61, 72, 375), ("BMGP", 73, 84, 440),
    ("BMLS", 12, 72, 1710),
]

# World Manufacturer Identifier prefixes, for VINs not in the registry.
WMI = {"WMW": ("MINI", "COOPER"), "3MW": ("BMW", "3SERIE"), "4US": ("BMW", "Z4"),
       "5UX": ("BMW", "X5"), "WBA": ("BMW", "5SERIE")}


def _csv_rows(name):
    path = os.path.join(config.ROOT, "config", name)
    with open(path) as fh:
        return list(csv.DictReader(fh))


def vin_lookup(vin):
    """Mocks the database pull. Registry first, then decode the WMI prefix."""
    for row in _csv_rows("vin_registry.csv"):
        if row["vin"] == vin:
            return {"make": row["make"], "model": row["model"],
                    "modelYear": row["modelYear"]}
    make, model = WMI.get(vin[:3], ("UNKNOWN", "UNKNOWN"))
    year = 2000 + (sum(ord(c) for c in vin) % 26)
    return {"make": make, "model": model, "modelYear": str(year)}


def dealer_lookup():
    """Mocks the static-attribute join source."""
    return {r["companyId"]: r for r in _csv_rows("static_dealers.csv")}


def _transaction_id(run_id, vin):
    """Shaped like the real one: 32 lowercase hex chars."""
    return hashlib.sha1(("%s|%s" % (run_id, vin)).encode()).hexdigest()[:32]


def fetch_rates(seed_row, run_id, jitter_pct=6, duplicate_options=0):
    """Mocks one getRates call. Returns the rate options for one seed row.

    Prices vary per run - seeded on run_id, so a run is reproducible but two
    runs push genuinely different data. `duplicate_options` repeats that many
    options, the way a real response returns overlapping ones, so the dedupe
    stage downstream has something real to collapse.
    """
    vin = seed_row.get("vin", "")
    rng = random.Random("%s|%s" % (run_id, vin))
    vehicle = vin_lookup(vin)
    txn = _transaction_id(run_id, vin)

    records = []
    for idx, (code, tmin, tmax, base) in enumerate(RATE_LADDER, start=1):
        product = PRODUCTS[code]
        swing = rng.uniform(-jitter_pct, jitter_pct) / 100.0
        cost = max(1, int(round(base * (1 + swing))))
        rec = {
            "optionId": "1.%d" % idx,
            "productCode": code,
            "sku": "%d-%s-%s-ALL-ALL-0-99-0-0-0-0" % (
                1100000 + rng.randint(0, 199999), PROGRAM["programId"], code),
            "externalSku": "%012d" % (10000 + rng.randint(0, 3999)),
            "productDescriptionEn": product["productDescription"],
            "coverageDescriptionEn": product["coverageDescription"],
            "termMonthsMin": tmin,
            "termMonthsMax": tmax,
            "sellerCost": cost,
            "vehicleCondition": "",
            "companyId": seed_row.get("companyId", ""),
            "vin": vin,
            "odometer": seed_row.get("odometer", ""),
            "inServiceDate": seed_row.get("inServiceDate", ""),
            "saleDate": seed_row.get("saleDate") or run_id[:10],
            "transactionId": txn,
            "vendorName": seed_row.get("vendorName", ""),
            "channel": seed_row.get("channel", ""),
            "seedTcId": seed_row.get("Tc_id", ""),
            "runId": run_id,
            "reqFinanceType": seed_row.get("financeType", ""),
            "reqFinanceAmount": seed_row.get("financeAmount", ""),
            "reqLanguageCode": seed_row.get("languageCode") or "en_US",
            "reqVehicleCondition": seed_row.get("vehicleCondition") or "NEW",
        }
        rec.update(PROGRAM)
        rec.update(product)
        rec.update(vehicle)
        records.append(rec)

    for n in range(duplicate_options):
        dup = dict(records[n % len(records)])
        dup["optionId"] = "1.%d" % (len(RATE_LADDER) + n + 1)
        records.append(dup)

    return records
