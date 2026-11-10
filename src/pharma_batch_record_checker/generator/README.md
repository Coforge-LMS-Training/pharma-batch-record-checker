# Synthetic data generator

`generate-batch-records` writes a reproducible, **synthetic** dataset of master records, executed batch records and the findings a reviewer should expect. Use it to test and demo the batch record checker. It needs no database and no rules engine, and nothing in it is real product, batch or people data.

Owner: Sarthak. The app never imports the generator, and the generator never imports the app; tests check both directions.

## Quick start

```bash
uv run generate-batch-records --out data/
```

```
wrote 4 batches, 20 expected findings to data
  products:
    B  MBR-SYN500 v3  Synthetol 500 mg film-coated tablets  2 batches, 1 defective
    P  MBR-PLC250 v1  Placeprol 250 mg hard capsules        2 batches, 1 defective
  findings: 10 Critical, 10 Needs Review, on 18 steps
  rules: out_of_range 5, missing_reading 1, missing_step 2, missing_performer 3, missing_second_check 2, same_person 2, verifier_before_performer 2, unit_mismatch 3 (all 8 planted)
  time span: 2026-10-12T07:00 to 2026-10-13T00:57
  files: master_record.csv, batch_record.csv, expected_findings.csv
```

To view the data in the review demo, start from an empty local database. The generated masters are the same as the approved sample's, so the demo would otherwise skip them as already loaded.

```bash
docker compose down -v
uv run pharma-review-demo --data data/
```

You can also run the generator as `python -m pharma_batch_record_checker.generator.cli` with the same options.

## Options

| Option | Default | What it does |
|---|---|---|
| `--out DIR` | **required** | Folder to write the three CSV files to. It's created if needed. The three files are replaced together, and other files in the folder are left alone. |
| `--batches N` | `4` | Total number of batches, from 1 to 9999. Product lines take turns (see [How batches are planned](#how-batches-are-planned)). |
| `--seed S` | `0` | Seed for the readings. A different seed changes readings only, never batch IDs, times or which batches are defective. |
| `--start YYYY-MM-DDTHH:MM` | `2026-10-12T07:00` | When each product line's first batch starts. It must be written exactly in this form (no seconds, no timezone, zero-padded), with a year from 2000 to 2099. |
| `--catalog DIR` | none | Adds the products described in a TOML catalog folder, after the two built-in products. See [`catalog/README.md`](../../../catalog/README.md). |
| `--no-builtin` | off | With `--catalog`: generate **only** the catalog's products and leave out MBR-SYN500 (B) and MBR-PLC250 (P). The letters B and P become free for catalog products, but the approved masters MBR-SYN500 v3 and MBR-PLC250 v1 are always reserved. Needs `--catalog`. |
| `-h`, `--help` | | Shows the options. |

The same `--seed`, `--batches`, `--start` and catalog always give **byte-identical** files.

## Examples

```bash
# The default: 4 batches of the two approved products
uv run generate-batch-records --out data/

# A bigger dataset with a different seed
uv run generate-batch-records --seed 7 --batches 200 --out data/

# Run across New Year: IDs go B26-…, then B27-0001
uv run generate-batch-records --batches 8 --start 2026-12-31T07:00 --out data/

# Add the catalog's products (an oral solution, a tablet, a clean-only capsule
# and a capsule whose defective batches are Needs Review, not Critical)
uv run generate-batch-records --batches 11 --catalog catalog --out data/

# Only the catalog's products
uv run generate-batch-records --batches 6 --catalog catalog --no-builtin --out data/
```

`data/` is gitignored. Regenerate it whenever you need it rather than committing it.

## What gets written

All three files are UTF-8 with no BOM and CRLF line endings, and follow the CSV contract in `schema.py`.

| File | Contents |
|---|---|
| `master_record.csv` | One row per master step, for each product used: limits, units, critical flag. |
| `batch_record.csv` | One row per executed step of every batch: reading, unit, performer and verifier with timestamps, comment. A planted `missing_step` leaves its row out. |
| `expected_findings.csv` | The answer key: `batch_id, step_id, rule_id, severity`, one row for every planted defect. It's built from the defects as they're planted, never by running a rules engine. The review demo doesn't load it. |

```
batch_id,master_record_id,master_version,step_id,reading,unit,performed_by,performed_at,verified_by,verified_at,comment
B26-0001,MBR-SYN500,3,S-DISP-01,149.57,kg,OP-115,2026-10-12T07:00,OP-131,2026-10-12T07:12,
B26-0001,MBR-SYN500,3,S-DISP-02,29.85,kg,OP-101,2026-10-12T07:30,,,
```

## How batches are planned

**Product lines.** Each product is a line with its own batch-ID letter:
- B = MBR-SYN500 v3 (tablets) and P = MBR-PLC250 v1 (capsules) are built in;
- catalog products follow, in file-name order;
- batch *i* goes to line *i* mod *k*, so lines take turns: `B26-0001, P26-0001, B26-0002, …`.

**Batch IDs.** An ID is the line's letter, the two-digit year of the batch's start date, and a 4-digit number that restarts at 0001 each year (`B26-0179`, then `B27-0001`). Every line needs a different letter, which allows up to 26 lines.

**Times.**
- Each line's first batch starts at `--start`.
- Steps are 30 minutes apart, and critical steps are verified 5–20 minutes after they're performed.
- The next batch on a line starts 3 hours after the previous one could have finished, so start times move around the clock and batches can run past midnight.
- A run that would start a batch after 2099 is refused.

**Defects.** A line's batches are clean and defective in turn, starting clean, so its **first defects are in its 2nd batch**. Defective batches carry that line's defect plan:
- the planned rules are broken on the planned steps;
- some defective steps get inert, instruction-like comments, as an operator might write them. They're data and never change a finding.

Lines without a defect plan only get clean batches.

**How many batches you need.** With *k* lines, line *j* (counting from 0) has its first defective batch at overall batch *k + j + 1*. With only B and P, 3 batches plant all 8 rules, because B26-0002 carries the full tablet plan. With a catalog, the summary works it out for you:

```
findings: none (a line's defects start at its 2nd batch; use --batches 4 or more)
rules: ... (not planted: missing_reading; --batches 5 plants every rule your plans cover)
findings: none (no product line has a defect plan)
```

**Clean readings** fall within the limits, at the limits' precision. Where the limits allow, each clean batch includes one reading exactly at a minimum and one exactly at a maximum (limits are inclusive, so these must pass). Each batch's readings come from its own seed, derived from `--seed` and the batch ID.

## The 8 rules and severity

Defect plans use these rule IDs (`rule_version` 1.0.0): `out_of_range`, `missing_reading`, `missing_step`, `missing_performer`, `missing_second_check` (critical steps only), `same_person` (critical steps only), `verifier_before_performer`, `unit_mismatch`.

A step's findings are **Critical** if:
- the step has 2 or more findings, or
- it's a critical step that is out of range, has no second check, or was checked by the same person.

Otherwise they're **Needs Review**. Combinations that couldn't all fire together are refused when the defects are planted.

## Exit codes and errors

| Code | When |
|---|---|
| `0` | Files written; the summary is printed. |
| `1` | The files couldn't be written (for example `--out` is a file, or there's no permission). The previous files are restored. One-line message. |
| `2` | Bad options: `--batches` out of range, a non-canonical `--start`, a run going past 2099, `--no-builtin` without `--catalog`, or any catalog problem (bad TOML, a clashing letter or master, a defect plan the limits can't take). One-line message, nothing written. |

## Adding products

- **Extra synthetic products or dosage forms:** add TOML files to a catalog folder. No code is needed. See [`catalog/README.md`](../../../catalog/README.md).
- **An approved product** (one that the fixtures and other owners' tests rely on): add a `ProductConfig` in `masters.py`. This changes a shared contract, so agree it with the other owners first.

## Approved sample fixtures

`fixtures/approved_sample/` (4 batches, 14 expected findings, matching the client sample) is built separately by `approved_sample.py`, not by this command. Never edit those files by hand. Regenerate them with:

```bash
uv run python -c "from pharma_batch_record_checker.generator.approved_sample import write_approved_sample; write_approved_sample('fixtures/approved_sample')"
```

## Code map

| Module | Role |
|---|---|
| `cli.py` | Options, product lines, batch planning (`plan_batches`), the summary |
| `masters.py` | Process templates and approved products → master rows (`build_master`) |
| `batches.py` | Clean executed batches (`generate_clean_batch`) |
| `defects.py` | Defect plans, planting (`plant_defects`), the dataset (`build_dataset`) |
| `expected_findings.py` | `DefectLog` and severity → `expected_findings.csv` |
| `catalog.py` | Loading `--catalog` TOML templates, defect plans and products |
| `dataset_io.py` | Writing the three files as a set, restoring the old ones on failure |
| `approved_sample.py` | The approved sample fixtures |
