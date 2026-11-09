---
name: Batch review
description: A specification index for stored synthetic batch reviews.
colors:
  monograph-indigo: "#12315c"
  pressed-indigo: "#0c2244"
  index-mist: "#d5deea"
  cool-paper: "#f7f8f6"
  sheet: "#ffffff"
  record-ink: "#1a1c19"
  hairline: "#c5c9c2"
  critical-red: "#9e2a2b"
  review-brown: "#6f3d09"
typography:
  display:
    fontFamily: "Source Serif 4, Palatino, Palatino Linotype, serif"
    fontSize: "1.75rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Source Sans 3, Source Sans Pro, sans-serif"
    fontSize: "1.35rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Source Serif 4, Palatino, Palatino Linotype, serif"
    fontSize: "1.35rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Source Sans 3, Source Sans Pro, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "Source Sans 3, Source Sans Pro, sans-serif"
    fontSize: "0.95rem"
    fontWeight: 600
    lineHeight: 1.3
rounded:
  none: "0"
spacing:
  tight: "0.35rem"
  field: "0.75rem"
  group: "1.25rem"
  page: "1.75rem"
  section: "2.25rem"
components:
  button-primary:
    backgroundColor: "{colors.monograph-indigo}"
    textColor: "{colors.cool-paper}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "0.5rem 0.9rem"
    height: "2.75rem"
  button-primary-hover:
    backgroundColor: "{colors.pressed-indigo}"
    textColor: "{colors.cool-paper}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "0.5rem 0.9rem"
    height: "2.75rem"
  text-input:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.record-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.none}"
    padding: "0.45rem 0.5rem"
    height: "2.75rem"
  running-head:
    backgroundColor: "{colors.monograph-indigo}"
    textColor: "{colors.cool-paper}"
    typography: "{typography.display}"
    rounded: "{rounded.none}"
    padding: "0.9rem 1.75rem"
    height: "4.25rem"
---

# Design System: Batch review

## Overview

**Creative North Star: "The Specification Index"**

The screen is a specification index a reviewer can sit in. A pinned indigo running head names the page, a hairline table carries the records, and the batch id is set in Source Serif 4. Density is the density of a monograph: tight rows, tabular figures, and prose held to a readable measure. The page is light because the work happens at a desk in ordinary office light.

Color does three jobs. The indigo bar is the shell. Critical and Needs Review are words in their own colors, on the paper, never pills. No Findings stays in the same ink as the rest of the row.

**Key Characteristics:**

- A pinned indigo running head, with the batch id in Source Serif 4
- Hairline tables on cool paper, square corners, no sidebar
- Severity is the word itself: Critical, Needs Review, or No Findings
- One motion: a link underline that draws in from the left

## Colors

The palette is a cool paper page with one indigo field and two result colors that never decorate.

### Primary

- **Monograph indigo** (#12315c): The running head, labels, links, focus rings, and the primary button. It is the shell, not a scattered accent.

### Secondary

- **Critical red** (#9e2a2b): The word Critical only, on cool paper. It is not a background and not a stripe.

### Tertiary

- **Review brown** (#6f3d09): The words Needs Review, on cool paper, distinct from Critical.

### Neutral

- **Cool paper** (#f7f8f6): The page ground, and the type on the indigo head.
- **Sheet** (#ffffff): Table and field surfaces.
- **Record ink** (#1a1c19): Body text and No Findings.
- **Hairline** (#c5c9c2): Row rules and the filter divider.
- **Index mist** (#d5deea): Secondary type on the indigo head. It is a tint of the indigo, not a gray.
- **Pressed indigo** (#0c2244): The button hover.

### Named Rules

**The Word Carries the Result Rule.** Critical, Needs Review, and No Findings are colored words. They are not pills, badges, or row fills.

## Typography

**Display Font:** Source Serif 4 (with Palatino)
**Body Font:** Source Sans 3 (with Source Sans Pro)
**Label Font:** Source Sans 3 at 600

**Character:** The serif is reserved for the batch id and the step id, the way a monograph sets the entry name. Everything else is the sans, with tabular figures so times and readings line up.

### Hierarchy

- **Display** (600, 1.75rem, 1.2): The running head. Batch review on the list, the batch id on a batch.
- **Headline** (600, 1.35rem, 1.3): Section titles such as Runs and Findings, in indigo.
- **Title** (600, 1.35rem, 1.2): The batch id in a table and the step id, in Source Serif 4.
- **Body** (400, 1.0625rem, 1.45): Findings, comments, and notes. Prose stays within 68ch.
- **Label** (600, 0.95rem): Field names in the snapshot, in indigo.

### Named Rules

**The Serif Is the Identifier Rule.** Source Serif 4 is for a batch id or a step id. It is not a display face for sentences.

## Layout

The page is one column under a full-width running head. Content padding is 1.75rem, tightening to 1rem below 800px. The filter row sits on the paper, then the table. Sections put more space above a heading (2.25rem) than below it (0.55rem).

Below 800px the running head wraps, the title taking the full width, and each table row becomes a two-column stack so the result stays on screen. The batch id and the product name span that stack.

## Elevation & Depth

Depth is one shadow. The running head sits above the page. Tables and fields are flat sheets separated by hairlines, not by lift.

### Shadow Vocabulary

- **Running head** (`box-shadow: 0 10px 24px rgb(18 49 92 / 18%)`): Only the pinned indigo bar. Nothing else casts a shadow.

### Named Rules

**The One Shadow Rule.** The running head is the only elevated surface. Rows, notes, and buttons stay flat.

## Shapes

Corners are square (0). Rules are 1px hairlines. The button, the date field, and the note field share that square stroke. There is no pill, no card radius, and no thick colored side border.

## Components

### Buttons

- **Shape:** Square (0).
- **Primary:** Monograph indigo ground, cool paper type, 2.75rem tall, padding 0.5rem 0.9rem. The label is the action: Show, Add note.
- **Hover / Focus:** Hover uses pressed indigo. Focus is a 2px cool-paper ring on the head and a 2px indigo ring on the paper, offset 2px.

### Navigation

The running head is the navigation. On the list it reads "Batch review" with "Specification index · synthetic" at the right. On a batch, "Batches" returns to the list, the batch id is the title, and "Synthetic records" stays at the right. Below 800px the title wraps onto its own line.

### Inputs / Fields

- **Style:** Square, 1px monograph indigo stroke, sheet fill, 2.75rem minimum height. The caret is monograph indigo.
- **Focus:** The same 2px indigo ring as every other control.
- **Error:** A square 1px critical-red border around the message. The message names what to enter.

### Result word

Critical uses critical red. Needs Review uses review brown. No Findings uses record ink at regular weight. A batch with no stored run says "No review", which is not a severity.

### Specification table

Sheet ground, hairline rows, indigo column labels. The batch id is the serif link. On a narrow screen the header is kept for assistive tech and each cell shows its label above the value.

### Finding

The rule id and the severity sit on one line. The snapshot is a wrapping grid of label and value, with the reading shown beside the recorded unit. A comment is a labeled block of text. Notes are an ordered list under the finding, oldest first, followed by the name and note fields.

## Do's and Don'ts

### Do:

- **Do** keep the running head pinned and indigo, and set the batch id in Source Serif 4.
- **Do** show Critical, Needs Review, and No Findings as those words.
- **Do** keep comments and notes as text, with a readable measure.
- **Do** use a hairline, not a card, to separate rows.

### Don't:

- **Don't** add a sidebar, a status pill, or a colored row fill for severity.
- **Don't** put a second shadow on tables, notes, or buttons.
- **Don't** round the button or the fields.
- **Don't** set sentences in Source Serif 4.
