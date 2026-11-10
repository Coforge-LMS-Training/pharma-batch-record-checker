# Generator catalog

Extra **synthetic** products for the data generator, described as TOML files and not as code:

```
uv run generate-batch-records --batches 11 --catalog catalog --out data/
```

Without `--catalog`, the generator makes only the two approved products, MBR-SYN500 (B) and MBR-PLC250 (P), which are defined in code. With `--catalog`, the catalog's products are added after them.

To generate **only** the catalog's products, add `--no-builtin`:

```
uv run generate-batch-records --batches 6 --catalog catalog --no-builtin --out data/
```

The letters B and P and the masters MBR-SYN500 v3 and MBR-PLC250 v1 are then free for catalog products.

## Layout

| Folder | One file per… | Refers to |
|---|---|---|
| `templates/` | process template: the ordered `[[step]]`s of a dosage form | nothing |
| `defect_plans/` (optional) | defect plan: the `[[defect]]`s to plant, plus inert `[comments]` | a template |
| `products/` | product: identity, `id_letter`, `[limits]` | a template, and optionally a plan |

Each item's name is its file stem, so `templates/oral_liquid.toml` is the template `oral_liquid`. Names use lower-case letters, digits and underscores. Files are read in name order, so product lines come after B and P in file-name order.

## Rules

- **Synthetic only.** Product names, IDs and processes are invented. Never use real product, batch or people data.
- **Text, not numbers.** Every value is quoted text, written exactly as it should appear in the CSV (`"29.85"`, not `29.85`). Bare numbers are refused, never converted. The only exception is `critical`, which is a TOML boolean (`true` or `false`).
- **No unknown keys.** A misspelt key is refused, so it can't be silently ignored.
- **Templates:**
  - A measured step has both `parameter` and `unit`. A sign-off step has neither.
  - `step_id`s are unique within a template.
  - Units are exact strings, compared case-sensitively.
  - `{excipient}` in step text is replaced with the product's excipient.
- **Defect plans:**
  - Each defect is on a step of the plan's template.
  - `side` (`"low"` or `"high"`) applies to `out_of_range` only.
  - `unit` (`"case"`, `"blank"` or a unit) applies to `unit_mismatch` only.
  - Comments go only on defective steps. They're data and never change a finding.
- **Products:**
  - `[limits]` gives `step_id = ["min", "max"]` for every measured step of the template and nothing else. `""` means no limit on that side.
  - A `defect_plan` must be for the product's template, and the product's limits must allow each defect. For example, an `out_of_range` defect with `side = "low"` needs a min. This is checked when the generator starts.
  - `id_letter` (one capital letter) and the master id and version must not clash with B, P or another product.
- With k product lines in total (counting B and P unless `--no-builtin` is set), `--batches` k + 1 or more includes the second, defective batch of the first line. A line's defects start at its 2nd batch.

## Copies of the approved products

`templates/tablet.toml`, `templates/capsule.toml`, `defect_plans/tablet_standard.toml` and `defect_plans/capsule_standard.toml` are copies of the code constants for MBR-SYN500 and MBR-PLC250. Tests fail if a copy and its constant ever differ, so change both together.

## Adding a dosage form

No code is needed. Add a template, optionally a defect plan for it, and a product that uses them. `oral_liquid.toml`, `oral_liquid_standard.toml` and `products/examplol_oral_solution.toml` are a complete example.
