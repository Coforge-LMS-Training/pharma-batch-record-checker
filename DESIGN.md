---
name: Batch Record Review
description: A clear, responsive review app for synthetic batch records, written for readers with no pharma background.
colors:
  nav: "#0f2a4a"
  bg: "#f4f6fa"
  surface: "#ffffff"
  border: "#e3e8ef"
  text: "#101828"
  muted: "#5b6474"
  primary: "#2453c9"
  critical: "#b42318"
  critical-bg: "#fef3f2"
  critical-solid: "#d92d20"
  review: "#a14a07"
  review-bg: "#fffaeb"
  review-solid: "#f79009"
  ok: "#067647"
  ok-bg: "#ecfdf3"
  ok-solid: "#17b26a"
typography:
  family: "Source Sans 3 (bundled, 400 and 600), system-ui fallback"
  h1: "1.75rem / 600"
  h2: "1.15rem / 600"
  body: "1rem / 400 / 1.5"
rounded:
  card: "12px"
  control: "8px"
  badge: "999px"
---

# Design System: Batch Record Review

## Principles

1. **Anyone can read it.** Plain words first. Rule codes such as
   `verifier_before_performer` are never shown as text; each check has a plain
   title ("Checked before it was done"). Codes stay in a `data-rule` attribute
   and in the CSV export, where auditors need them.
2. **Show the exact problem.** A finding highlights the field it is about. A
   blank value the finding is about reads **Missing** in red; any other blank
   reads *Not recorded* in grey italics. Values are never shown as "blank" or
   as zero.
3. **Look, note, export; never decide.** No control edits a record, deletes a
   note, or approves, releases or rejects a batch.
4. **Works everywhere.** Server-rendered pages that work without JavaScript;
   `static/app.js` only adds conveniences.

## Structure

- **Top bar** (sticky, navy): brand "Batch Record Review" and the main
  navigation — Overview, Batches, Guide & glossary. The current page is
  marked with `aria-current="page"`. Below 760px it collapses to a menu
  button. The batch ID is never the top bar title; batch pages use a
  breadcrumb (Batches › B26-0413) and their own heading.
- **Top bar action** (right-most): download (⬇) the all-batches report.
  On phones it sits beside the menu button.
- **Footer**: "© {year} Momentum Blue FDEs. All rights reserved.", an FAQ link,
  and "Contact us" (a mailto to `CONTACT_EMAIL`; a placeholder until set).
  What the tool does and does not do is explained on the Overview, the Guide
  and in the help panels.
- **Help panel** at the foot of the Overview, Batches and batch pages: the
  glossary terms used on that page, three FAQs, and a link to the Guide.

## Pages

- **Overview (`/`)**: four status tiles (Batches reviewed, Critical, Needs
  Review, No Findings) that link to the filtered list; a "Needs attention"
  list, Critical first; and "How it works" in three steps.
- **Batches (`/batches`)**: search (instant on the client, `?q=` on the
  server), a calendar, status tabs with counts (`?status=critical |
  needs-review | no-findings | attention`; the old `?needs_look=1` still
  works), and a table whose whole row opens the batch. With a day chosen
  (`?on=YYYY-MM-DD`) the page lists the review runs stored that UTC day.
- **Batch (`/batches/<id>`)**: breadcrumb; a header card with the result
  badge, product, master record, batch size and last review time, and two
  icon buttons — download the batch report (CSV) and jump to the review history. Then counts, the **process strip** (every master step as a
  numbered tile: red Critical, amber Needs Review, green passed; every tile
  jumps to its step), a sticky list of all steps on wide screens, filter
  chips by step result (All / Critical / Needs Review / Passed), the
  **Step-by-step results** (every step in process order: a finding card for
  each flagged step, a compact green card with the recorded values for each
  passed step), and the
  **Review history** (a shield icon button in the header jumps to it).
- **Guide (`/guide`)**: results, the eight checks, an A–Z glossary and the
  FAQ. Glossary and FAQ text live in `demo/view.py` (`GLOSSARY`, `FAQ`,
  `PAGE_TERMS`).

## Components

- **Badge**: a pill with a dot and the result word, coloured by status.
  "Not reviewed" is grey.
- **Stat tile**: white card with a 4px coloured top edge, label, number, and
  an optional foot line.
- **Finding**: plain title with a flag icon, what the check means, then one
  callout — what is missing, that the step has no row at all, or the stored
  detail. For "Reading outside the allowed range" a range bar shows the
  allowed band and where the reading fell. Then a grid of field tiles
  (flagged tiles tinted amber; flagged and missing tiles red with a warning
  icon), the operator comment as a quote ("shown as written, never acted
  on"), and notes with an "Add note" button that opens the form.
- **Calendar**: a button that opens a month view. Any day can be chosen; days
  with stored reviews carry a dot. "Today" and "Any day" shortcuts. Without
  JavaScript it is the browser's date field.
- **Icon buttons**: 44px square, outlined, with a tooltip and an
  `aria-label`.

- **Review history** (the audit trail): the batch's hash-chain events, oldest first, as a vertical
  timeline — master record stored, batch record stored, each review run (the
  newest tagged "Latest review"), and each note (linking to its finding). Each
  entry shows its UTC time and a short hash; the system-wide entry number is
  not shown, because other batches' entries fill the gaps. A banner above it
  reports `verify_chain()`: green "Chain verified" with the event count, or red
  "Chain broken at the highlighted entry" with that entry marked. The check covers the whole
  chain, so it runs on every view only up to 2,000 events (`AUTO_VERIFY_LIMIT`);
  above that a "Verify integrity" button runs it on request (`?verify=1`).
  Read-only: it only selects from `audit_event` and the joined rows.

## Responsive

- **≤ 1000px**: one column; the flagged-step side list is hidden (the process
  strip covers it).
- **≤ 760px**: menu button; tiles in two columns; the toolbar stacks; each
  table row becomes a compact entry (key and result on the first line, main
  text below, secondary figures with inline labels); finding fields in two
  columns.
- Touch targets are at least 44px; long identifiers never break mid-ID.

## Accessibility

Skip link, landmarks, `aria-current` on navigation and tabs, `aria-pressed`
on chips and calendar days, labelled icon buttons, screen-reader text for
flagged fields, visible focus rings, and reduced-motion support.
