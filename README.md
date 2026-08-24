# SG-POC-Pipeline

Data-prep pipeline for the Safe-Guard mabl POC. It generates rate data, filters and
merges it, and **reconciles it into a real mabl DataTable** so the chained API and
portal tests have fresh input on every run.

Built from Pradeep Rajaput's *Overall Automation Flow* (20 Aug working session).

```
 PIPELINE (this repo)                                    │  MABL
                                                         │
 seed rows ──> getRates ──> filter ──> merge ──> PUT ─────┼──> DataTable ──> plans ──> gate
   (real,      (mocked)     dedupe    static +   once     │    (real)        (real)
    from mabl)                        db lookup           │
                                      (mocked)            │
```

**Mocked:** the getRates HTTP call and the Salesforce / Postgres / SQL Server lookups.
Both live in `sgpipe/sources.py` — that file is the entire "go live" change.

**Real:** the seed rows (read live from mabl), the DataTable write, and the plan trigger.

Data is **dynamic**: prices carry a per-run jitter and each VIN gets a fresh
32-hex `transactionId`, so every run pushes genuinely different rows while
staying reproducible for a given `--run-id`.

---

## Quickstart

No dependencies. Stdlib only, Python 3.9+.

```bash
# 1. see it work without touching mabl
python3 -m sgpipe.cli run --offline --dry-run

# 2. supply the workspace API key
cp .env.example .env && $EDITOR .env

# 3. real run: reads seed rows from mabl, pushes to mabl
python3 -m sgpipe.cli run

# 4. ...and kick off the Amz_Flow plans, failing the shell if a test fails
python3 -m sgpipe.cli run --trigger
```

Every stage can also run on its own — `seed`, `generate`, `transform`, `push`,
`trigger` — each reading the previous stage's file out of `out/`.

| Flag | Effect |
|---|---|
| `--offline` | Use `fixtures/seed_rows.json` instead of reading seed rows from mabl |
| `--dry-run` | Run everything except the mabl writes |
| `--run-id X` | Stamp rows with `X` instead of a UTC timestamp (makes a run reproducible) |
| `--trigger` | After pushing, fire a deployment event and gate on the result |
| `--preview` | With `trigger`: resolve which plans *would* run, without running them |

Expected output:

```
[01] seed rows        4 rows read live from mabl
[02] getRates         4 seed rows in parallel -> 80 raw records in ONE list
[03] filter/dedupe    raw 80 -> filtered 76 (BMMC+BMGP) -> unique 68 -> valid 68
[04] push             SGPOC_Rates_API   <id>  reconciled to 68 rows (1 PUT, not 68 POSTs)
                      SGPOC_Rates_UI    <id>  reconciled to 68 rows
```

---

## What it produces

Two DataTables, both with **stable names and no timestamp**:

| Table | Columns | For |
|---|---|---|
| `SGPOC_Rates_API` | 40 | The API chain — every response field plus the merged and provenance columns |
| `SGPOC_Rates_UI` | 17 | Portal tests — the vehicle, product, price and dealer fields a journey needs |

Useful columns for chaining tests:

- `transactionId` — one per VIN per run; correlate the rate call with saveContract
- `runId` — which pipeline run produced this row
- `seedTcId` — which seed row it fans out from
- `dealerName` / `dealerState` — merged from the static source, not from the API

---

## Why one PUT and not many POSTs

`POST /dataTables` **always creates a new table** — it never matches on name and
never updates in place. Four parallel scenarios each posting their own results is
what left four tables named `AMZ_Rates_Response_2026-08-24_11-47-47` in the
workspace, with no combined view anywhere.

So the pipeline resolves each table's id **once**, then reconciles:

```
PUT /dataTables/{id}/scenarios
```

Run it twice and there is still one table, same id, same row count.

Three behaviours the client is built around:

1. **Reconcile deletes by omission.** The table is made to match your payload exactly — any row you leave out is removed. Always send the complete set.
2. **Columns are managed separately.** `ensure_columns` diffs the live column list and creates only what is missing, before any row is written.
3. **Every value is a string.** `transform.cell` coerces, and turns `None` into `""` rather than `"None"`.

Auth on every call is basic auth with the literal username `key`:

```bash
curl -u "key:$MABL_API_KEY" https://api.mabl.com/dataTables?workspace_id=...
```

One real API asymmetry, handled in `sgpipe/mabl.py`: reading scenarios is
`GET /dataTables/scenarios?data_table_id=…`, writing them is
`PUT /dataTables/{id}/scenarios`.

---

## CI

`.github/workflows/pipeline.yml` — manual dispatch with `offline` / `dry_run` /
`trigger` toggles, plus a 07:00 UTC nightly run. Stage outputs upload as an
artifact. One secret:

```bash
gh secret set MABL_API_KEY --repo andwaitforit/SG-POC-Pipeline
```

**A GitHub-hosted runner cannot reach Safe-Guard's UAT endpoints** — the same
reason mabl needs the link agent. That is why the data sources are mocked rather
than called. Going live needs a **self-hosted runner inside SG's network**; the
transform and push stages do not change.

---

## Tests

```bash
python3 -m unittest discover -s tests -v
```

17 tests over the transform stage and the DataTable payload contract, including a
regression guard for the dedupe key: `unique_on` **must** contain `vin`, or all
four seed rows collapse into a single set of 18 options.

---

## Layout

```
config/pipeline.json      workspace + table ids, filter rules, trigger targets
config/static_dealers.csv static attributes joined on companyId
config/vin_registry.csv   mock DB: vin -> make / model / year
fixtures/seed_rows.json   offline seed rows (mirrors Amazon_Rates_Test_Input)
sgpipe/sources.py         MOCKED getRates + DB lookup  <- swap this to go live
sgpipe/transform.py       filter / dedupe / merge / payload shaping (pure)
sgpipe/mabl.py            mabl API client, stdlib urllib
sgpipe/cli.py             stage runner
out/                      stage artifacts (gitignored)
```

### Going live

1. Replace `sources.fetch_rates` with a real Okta token call + POST to getRates.
2. Replace `sources.vin_lookup` with the real query.
3. Move the job to a self-hosted runner inside SG's network.

Nothing downstream of `sources.py` changes.
