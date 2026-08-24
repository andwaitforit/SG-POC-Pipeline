"""Filter, dedupe, merge, shape. Pure functions, no network - the testable core."""

API_COLUMNS = [
    "seedTcId", "optionId", "productCategory", "productType", "productCode",
    "programType", "programId", "programName", "formNumber", "sellerId",
    "productDescription", "productDescriptionEn", "sku", "externalSku",
    "coverageCode", "coverageDescription", "coverageDescriptionEn",
    "deductibleAmount", "termMileage", "termMonthsMin", "termMonthsMax",
    "sellerCost", "retailPrice", "regulatedRate", "vehicleCondition",
    "financeType", "companyId", "dealerName", "dealerState", "make", "model",
    "vin", "modelYear", "odometer", "inServiceDate", "saleDate",
    "transactionId", "vendorName", "channel", "runId",
]

UI_COLUMNS = [
    "seedTcId", "vin", "make", "model", "modelYear", "productCode",
    "coverageCode", "coverageDescription", "termMonthsMin", "termMonthsMax",
    "sellerCost", "financeType", "companyId", "dealerName", "dealerState",
    "transactionId", "runId",
]


def filter_products(records, include_product_codes):
    allowed = set(include_product_codes)
    return [r for r in records if r.get("productCode") in allowed]


def dedupe(records, unique_on):
    """Keep the first record per unique key, preserving order."""
    seen, kept = set(), []
    for rec in records:
        key = tuple(str(rec.get(k, "")) for k in unique_on)
        if key in seen:
            continue
        seen.add(key)
        kept.append(rec)
    return kept


def merge_static(records, dealers_by_company):
    """Left join the static attributes on companyId. Misses stay empty."""
    out = []
    for rec in records:
        merged = dict(rec)
        dealer = dealers_by_company.get(rec.get("companyId")) or {}
        merged["dealerName"] = dealer.get("dealerName", "")
        merged["dealerState"] = dealer.get("dealerState", "")
        out.append(merged)
    return out


def validate(records, required=("vin", "productCode", "sellerCost", "transactionId")):
    """Split into (good, rejects). A bad row must not kill the run."""
    good, rejects = [], []
    for rec in records:
        missing = [f for f in required if str(rec.get(f, "")).strip() == ""]
        if missing:
            rejects.append({"record": rec, "missing": missing})
        else:
            good.append(rec)
    return good, rejects


def cell(value):
    """Every DataTable value is a string. None becomes "", never "None"."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def to_scenarios(records, columns, name_prefix="Rate"):
    """Build the mabl reconcile payload. Row names follow the existing
    Rate_<n> convention already used in the workspace."""
    scenarios = []
    for idx, rec in enumerate(records, start=1):
        scenarios.append({
            "name": "%s_%d" % (name_prefix, idx),
            "variables": [{"name": c, "value": cell(rec.get(c))} for c in columns],
        })
    return scenarios


def prepare(raw_records, filter_cfg, dealers_by_company):
    """The whole transform stage. Returns a report dict."""
    filtered = filter_products(raw_records, filter_cfg["include_product_codes"])
    deduped = dedupe(filtered, filter_cfg["unique_on"])
    merged = merge_static(deduped, dealers_by_company)
    good, rejects = validate(merged)
    return {
        "raw": len(raw_records),
        "filtered": len(filtered),
        "deduped": len(deduped),
        "rejected": len(rejects),
        "rows": good,
        "rejects": rejects,
    }
