---
version: 1
slug: "batch-review"
primary_target: "batch-review"
related_targets: ["batch-detail"]
---

Mode: Operate.

A reviewer scans stored synthetic batches, opens one, reads findings in process order, adds a note, and downloads the CSV. Success is that Critical and Needs Review are visible, two findings on one step stay separate, and nothing on the page edits a row or releases a batch.

## Direction contract

THESIS: The screen is a specification index: a running head, a filter, and a hairline table. It refuses the admin sidebar and the status pill.

OWN-WORLD: Indigo running head (#12315c) on cool paper (#f7f8f6), ink (#1a1c19), hairline rules (#c5c9c2). Source Serif 4 carries the batch id. Source Sans 3 carries the rest, with tabular figures. Critical is the word in #9e2a2b. Needs Review is the word in #6f3d09. No Findings stays ink.

STORY: The reviewer sees which batches need a look, opens one, reads every finding under its procedure, and leaves a note that does not change the finding.

FIRST VIEWPORT: A full-width indigo running head, "Batch review" at the left and "Specification index · synthetic" at the right. Under it, a checkbox and a UTC date on one line. Then a dense table: batch id in serif, master, product, latest result. The primary action is the batch id. No sidebar.

FORM: Monograph index, position 1 of the grounded list, chosen from the pick. seed:8374613d5f01ab85

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

Signature: the running head stays pinned. On a batch, it carries that batch id in Source Serif, and the findings stay in the same hairline table.
