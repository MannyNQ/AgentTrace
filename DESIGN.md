---
name: AgentTrace
description: Open-source tracing and evals for LLM agents. The landing page's visual world, "Signal Box".
colors:
  panel: "#20272b"
  panel-2: "#283035"
  panel-3: "#313a40"
  panel-line: "#414b52"
  panel-ink: "#ece8dc"
  panel-dim: "#a8b1b5"
  track: "#6f7a80"
  well: "#12171a"
  berth-black: "#0f1417"
  shelf: "#181e21"
  enamel-plate: "#e9e5d8"
  lamp: "#fff3d2"
  red: "#ff5a4b"
  green: "#4fe08c"
  yellow: "#ffc83a"
  lever-red: "#c8261c"
  lever-yellow: "#e9b10a"
  paper: "#e3e8e1"
  paper-2: "#d7ddd5"
  slip-paper: "#f4f6f2"
  ink: "#1b2124"
  ink-2: "#3a4448"
  rule-blue: "#b6c6d7"
  rule-red: "#c75b52"
  ink-red: "#8c1c14"
  mark: "#ffd84d"
typography:
  display:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "clamp(2.6rem, 4.6vw, 4.3rem)"
    fontWeight: 800
    lineHeight: 0.92
    letterSpacing: "-0.01em"
  headline:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "clamp(2.4rem, 5vw, 4.4rem)"
    fontWeight: 800
    lineHeight: 0.95
  headline-ledger:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "clamp(2.4rem, 5vw, 4.4rem)"
    fontWeight: 800
    lineHeight: "64px"
  numeral:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "clamp(3rem, 7vw, 6.5rem)"
    fontWeight: 800
    lineHeight: 0.9
  title:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "1.2rem"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "0.02em"
  body:
    fontFamily: "Barlow, Helvetica Neue, Arial, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 400
    lineHeight: 1.6
  body-ledger:
    fontFamily: "Barlow, Helvetica Neue, Arial, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 400
    lineHeight: "32px"
  label-engraved:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "0.78rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0.12em"
  label-control:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "0.95rem"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "0.08em"
  mono:
    fontFamily: "JetBrains Mono, ui-monospace, Menlo, monospace"
    fontSize: "0.86em"
    fontWeight: 400
rounded:
  hairline: "2px"
  plate: "3px"
  pill: "999px"
  round: "50%"
spacing:
  gutter: "clamp(16px, 4vw, 64px)"
  rule: "32px"
  rule-2: "64px"
  book-pad: "96px"
  ledger-indent: "120px"
  max: "1320px"
components:
  plate:
    backgroundColor: "{colors.panel-2}"
    textColor: "{colors.panel-ink}"
    rounded: "{rounded.plate}"
    padding: "26px 22px 20px"
  command:
    backgroundColor: "{colors.well}"
    textColor: "{colors.lamp}"
    typography: "{typography.mono}"
    rounded: "{rounded.hairline}"
    padding: "13px 14px"
  copy-button:
    backgroundColor: "{colors.panel-3}"
    textColor: "{colors.panel-ink}"
    typography: "{typography.label-control}"
    padding: "0 14px"
    width: "78px"
  copy-button-hover:
    backgroundColor: "{colors.panel-line}"
  copy-button-done:
    backgroundColor: "{colors.green}"
    textColor: "{colors.ink}"
  push-button-cap:
    backgroundColor: "{colors.lamp}"
    rounded: "{rounded.round}"
    size: "46px"
  push-button-cap-small:
    backgroundColor: "{colors.lamp}"
    rounded: "{rounded.round}"
    size: "34px"
  nx-button:
    backgroundColor: "{colors.panel-2}"
    textColor: "{colors.panel-dim}"
    typography: "{typography.label-control}"
    rounded: "{rounded.pill}"
    padding: "7px 14px 7px 8px"
  nx-button-pressed:
    backgroundColor: "{colors.panel-2}"
    textColor: "{colors.panel-ink}"
  berth:
    backgroundColor: "{colors.berth-black}"
    textColor: "{colors.lamp}"
    typography: "{typography.mono}"
    rounded: "{rounded.hairline}"
    height: "34px"
    width: "148px"
  berth-dark:
    backgroundColor: "{colors.berth-black}"
    textColor: "{colors.track}"
  nav-link:
    textColor: "{colors.panel-dim}"
    typography: "{typography.label-control}"
    padding: "8px 0"
  nav-link-hover:
    textColor: "{colors.panel-ink}"
  diagram-plate:
    backgroundColor: "{colors.enamel-plate}"
    textColor: "{colors.ink}"
    rounded: "{rounded.hairline}"
    height: "22px"
  slip:
    backgroundColor: "{colors.slip-paper}"
    textColor: "{colors.ink}"
    padding: "0 18px"
---

# Design System: AgentTrace

Scope: this documents the public landing page in `site/` (`index.html`, `styles.css`, `main.js`). The self-hosted product UI in `web/` is a separate surface this document doesn't cover.

## Overview

**Creative North Star: "The Signal Box"**

The page is a railway signal box. Its two materials alternate down the page and never mix: the **panel**, an NX interlocking panel in slate enamel where every run is a route lit across a track diagram, and the **occurrence book**, a ruled grey-green ledger where the box writes down what happened. Panel sections (the hero, "What your agent actually did", the gate, replay, the quickstart) hold instruments: plates, push buttons, lamps, block instruments, a lever frame, a working timetable. Book sections (the failure, datasets, the rulebook, self-hosting) hold entries: dated headings, ledger rows, diffs, tickets, a pasted screenshot.

Color is a signalling code, not decoration. A lamp's color says a state, and an unlit element says the route isn't set. Scale does the emphasis: a 25% in condensed 800 weight, the ink-red "−50 pts" under it. The world is one fixed scheme with no dark-mode variant by design; the panel is already dark and the book already light, and the page relies on that alternation.

Motion is a single clock. Route lamps light one track circuit after another on arrival and whenever a route is set, then hold. Nothing else moves except a pressed push-button cap.

**Key Characteristics:**
- Two materials, strictly alternating: slate-enamel panel and ruled ledger paper.
- Lamps carry state: white route, red danger or fail, green clear or pass, yellow caution or underpowered.
- Lit means live: unlit track and dark berths for routes not set.
- Book text registered to a 32px rule pitch with a red margin rule in the content column.
- Plates screwed to the panel, lettering cut into them.
- One clock drives all motion: route lamps lighting in sequence.
- Real data everywhere: every berth, dial, lever, and register line carries a real span, score, or command.

## Colors

A drenched slate panel lit by four signal lamps, alternating with grey-green ledger paper ruled in blue and red.

### Primary
- **Route Lamp** (`lamp`): warm white. A set route's lit track, berth describer text, the push-button cap, command text in wells, the timetable step numbers, trace bars in the time-distance graph. It is the "this is live" color.

### Secondary (state lamps)
- **Danger Red** (`red`): danger aspects, failed checks (`steps FAIL`, `FAIL`, `regressed` and exit `3` in the train register), the "Line blocked" sector and verdict of a block instrument, the stop dot in the wordmark, the buffer-stop stripes.
- **Clear Green** (`green`): clear aspects and passes (`pass`, "Line clear"), the "Exit 0" lamp, the copy button once it has copied.
- **Caution Yellow** (`yellow`): caution and underpowered (the "Underpowered" dial sector, the highlighted telegram row), the points diamond on the diagram, and the focus ring, text selection, berth hover stroke and skip link.

### Tertiary (lever colors)
- **Lever Red** (`lever-red`): signal levers in the lever frame; trajectory scorers, which stop the train.
- **Lever Yellow** (`lever-yellow`): distant levers; the `contains` scorer, a caution signal. Spare levers are white, painted in Enamel Plate.

### Neutral
- **Slate Enamel** (`panel`): page ground and every panel section; also the insulated-joint gap color on the diagram.
- **Raised Enamel** (`panel-2`): plates and NX buttons.
- **Worn Enamel** (`panel-3`): copy buttons and inline code chips on the panel.
- **Panel Seam** (`panel-line`): every hairline border, rule, signal post, and berth stroke on the panel.
- **Enamel Ink** (`panel-ink`): headings and primary text on the panel.
- **Engraving Grey** (`panel-dim`): lede text, engraved labels, nav links, secondary columns.
- **Unlit Track** (`track`): the diagram's base rails, unlit. Text in dark berths is a lighter literal, #959fa4, which holds 4.5:1 on Berth Black.
- **Well** (`well`): command boxes and code plates; literal in `styles.css`, not a `:root` variable.
- **Berth Black** (`berth-black`): berth faces and lever tags; literal.
- **Instrument Shelf** (`shelf`): the block-instrument shelf and the lever frame's backing; literal.
- **Enamel Plate** (`enamel-plate`): cream plates on the diagram, lever number discs, the white spare lever; literal.
- **Ledger Paper** (`paper`): every book section.
- **Ledger Shade** (`paper-2`): inline code on paper, the pasted frame's lower edge.
- **Slip Paper** (`slip-paper`): diff and ticket slips laid on the ledger; literal.
- **Book Ink** (`ink`): text on paper, and dark text on lamp-colored fills.
- **Pencil** (`ink-2`): ledger row tags, captions, diff file names.
- **Rule Blue** (`rule-blue`): the 32px ledger rules and slip outlines.
- **Margin Red** (`rule-red`): the single vertical margin rule.
- **Red Ink** (`ink-red`): entry dates, rule numbers, deleted diff lines, the "−50 pts" delta.
- **Highlighter** (`mark`): added diff lines and highlighted ticket values on paper.

### Named Rules
**The Lamps Carry State Rule.** Red, green, and yellow on the panel each mean one thing: danger or fail, clear or pass, caution or underpowered. White means a set route. A lamp color never decorates.

**The Lit Means Live Rule.** Track lights only for the route that is set; berths off that route go dark (#959fa4 text on Berth Black, kept above 4.5:1). An unset NX button shows an unlit lamp well, a pressed one a lit Route Lamp.

**The Two Materials Rule.** A section is either panel or book, and they alternate. Panel colors don't appear on paper and paper colors don't appear on the panel, with two exceptions: the telegram, a paper card laid on the panel, and Book Ink as the text color on a lit lamp fill.

## Typography

**Display Font:** Barlow Condensed (with Arial Narrow)
**Body Font:** Barlow (with Helvetica Neue, Arial)
**Label/Mono Font:** JetBrains Mono (with ui-monospace, Menlo)

**Character:** Condensed caps are the panel's engraving: plates, buttons, rule numbers, verdicts. Barlow is the prose voice. JetBrains Mono is the machine voice: berths, commands, spans, the train register, every value that came out of a real run.

### Hierarchy
- **Display** (800, clamp(2.6rem, 4.6vw, 4.3rem), 0.92): the hero headline only, max 17ch, with a cut-in text shadow.
- **Headline** (800, clamp(2.4rem, 5vw, 4.4rem), 0.95): section headings on the panel, max 18ch.
- **Headline, ledger** (800, same size, 64px line height): section headings in the book, two rules tall; 2.2rem on 32px below 640px.
- **Numeral** (800, clamp(3rem, 7vw, 6.5rem), 0.9): the one big row in the baseline-vs-PR seam. Emphasis is scale.
- **Title** (700, 1.2rem, 1.3): timetable steps.
- **Body** (400, 1.125rem, 1.6): prose on the panel; ledes run 54–66ch.
- **Body, ledger** (400, 1.125rem, 32px): every line of text in the book, one line per rule.
- **Label, engraved** (600, 0.78rem, 0.12em, uppercase): plate labels, the "Route" legend, the describer head.
- **Label, control** (600, 0.95rem, 0.08em, uppercase): nav links, NX buttons, copy buttons, ledger row tags (700 weight there).
- **Mono** (400–500, 0.86em of context): code, berths, the graph, the register, lever tags.

### Named Rules
**The Cut-In Rule.** Lettering on the panel reads as engraved into the enamel: a dark shadow above, a faint light one below (`0 -1px 0` dark, `0 1px 0` light). The hero headline carries the same cut at 2px.

**The Writing On The Lines Rule.** In the book, every line of text is 32px tall and sits on its own rule. Inline code chips drop to line-height 1 and vertical padding 0 so they never push a line off the pitch.

## Layout

Content sits in a 1320px column with a fluid gutter (16px at phone width, up to 64px). Panel sections pad clamp(64px, 10vh, 112px) vertically (slim sections clamp(48px, 7vh, 80px)); the hero plates sit in a 1.65fr / 1fr grid aligned to the bottom, with the diagram running full bleed below them.

The book is a ruled page: a repeating 32px gradient draws a blue rule every 32px across the content column, and the section pads 96px (three rules) top and bottom, 64px below 960px. A red margin rule runs at gutter + 104px; entry dates sit in a 92px right-aligned column to its left, and headings and ledger content start at 120px, to its right. Every vertical gap in the book is a multiple of 32px: 32px under the heading, 64px before the seam and the pasted screenshot, 160px (five rules) for the big seam row.

Responsive behavior has two steps. Below 960px: single-column hero and splits, ledger indent removed, the margin rule and entry dates hidden, block instruments three across with the key below. Below 640px: secondary nav links, the lede's extra sentence and plate notes hide; the diagram stacks diagram, NX buttons, describer, caption; ledger rows stack; block instruments become one per row with the dial beside the verdict; the graph drops its scale and tokens column.

### Named Rules
**The Registered Ledger Rule.** Anything placed in the book lands on the 32px pitch. A new element in a book section measures its height and margins in 32px steps or it breaks the page.

## Elevation & Depth

Depth is physical, not ambient. Plates are raised enamel: a 1px Panel Seam border, a 1px inner highlight, and a short cast shadow (`0 10px 24px -18px`). Wells (commands, code plates, berths) are recessed by color alone, darker than the panel. The push-button cap is a domed lamp: a radial gradient off-center, a black bezel ring and a seam ring. The pasted screenshot in the book is a print taped into the ledger, rotated −0.4° with a soft drop shadow (flat on phones).

### Shadow Vocabulary
- **Plate** (`box-shadow: inset 0 1px 0 rgb(255 255 255 / 0.04), 0 10px 24px -18px rgb(0 0 0 / 0.8)`): any plate screwed to the panel.
- **Engraving** (`text-shadow: 0 -1px 0 rgb(0 0 0 / 0.7), 0 1px 0 rgb(255 255 255 / 0.06)`): engraved labels.
- **Push cap** (`box-shadow: 0 0 0 5px #11161a, 0 0 0 7px var(--panel-line), 0 6px 14px -6px rgb(0 0 0 / 0.7)`): the NX push button.
- **Pasted print** (`box-shadow: 0 2px 0 var(--paper-2), 0 20px 30px -24px rgb(0 0 0 / 0.5)`): a screenshot on paper.

### Named Rules
**The Screwed Plate Rule.** A plate has two screw heads in its top corners (7px radial-gradient dots, 9px in). A panel surface without screws isn't a plate.

## Shapes

Corners are nearly square: 2px on wells, berths, chips and diagram plates, 3px on plates. Two shapes are round because the hardware is: pills for NX entrance/exit buttons, circles for lamps, push-button caps, lever number discs and signal-head lenses. Signal heads are 26 × 54px lozenges (13px radius). The points are a yellow diamond. Levers are 14 × 92px bars rounded at the top. Every line on the panel is a 1px Panel Seam hairline; track is 7px wide with 6px insulated-joint breaks between track circuits.

## Components

### Plates
- **Shape:** gently squared (3px), Raised Enamel, 1px Panel Seam border, two screw heads.
- **Title plate:** holds the hero headline and lede. **Action plate:** an engraved label, the command, the push button, a note.

### Commands
- **Style:** a Well box with a 1px seam border, Route Lamp mono text, horizontal scroll rather than wrapping. A copy button is fused to its right edge.
- **Copy button:** Worn Enamel, control label "Copy", 78px min width; hover Panel Seam; after copying it turns Clear Green with Book Ink text and reads "Copied" (or "Selected" if the clipboard is refused) for 1.6s.

### Push buttons (primary action)
- **Style:** a 46px domed Route Lamp cap beside a 1.25rem condensed 700 uppercase legend. A small variant is 34px / 1.05rem.
- **Hover:** the cap presses in (scale 0.94, 160ms) and the legend underlines in Caution Yellow.
- **Secondary action:** a plain condensed uppercase link in Engraving Grey beside it.

### Navigation
- **Style:** the top rail, Slate Enamel with a seam bottom border; the wordmark (a main line and PR line with green and red lamps) and condensed uppercase links in Engraving Grey, Enamel Ink on hover. Below 640px only Quickstart and GitHub remain.

### NX buttons
- **Style:** pill, Raised Enamel, a 16px lamp well inside. Unpressed: Engraving Grey text, unlit well. Pressed (`aria-pressed`): Enamel Ink text, Unlit Track border, the well lit Route Lamp. Pressing one sets that route on the diagram.

### Interlocking diagram (signature)
- **What:** the two traces of the 1044 refund question drawn as a track diagram, from `main.js`. Nine track circuits with insulated joints, a yellow points diamond where the PR's route diverges, eight berths (one per span), two two-aspect exit signals whose lit lens and verdict text show the scorers' result, cream Enamel Plate labels.
- **Two layouts:** a wide one (1440 × 360, along the screen, min 1060px with horizontal scroll) and a narrow one (390 × 760, down the screen, max 430px), chosen by the diagram's own width at 640px and redrawn on resize.
- **Berths:** Berth Black, seam stroke, Route Lamp mono text, a condensed berth ID above. Hover or focus: Caution Yellow stroke; selected: Route Lamp stroke. Off the set route: dark (Unlit Track text). Selecting a berth (click, Enter, Space, arrow keys) writes its span into the describer below.

### Block instruments (gate verdicts)
- **What:** one dial per scorer on a shared Instrument Shelf. A semicircular arc with three sectors, Clear Green, Caution Yellow, Danger Red, at 0.28 opacity; the sector matching the reading lights full. A needle and hub in Enamel Ink.
- **Caption:** scorer name in mono, verdict in condensed 800 uppercase in the matching lamp color ("Line clear", "Underpowered" at 2rem, "Line blocked"), the numbers in mono. A key panel names all three.

### Lever frame
- **What:** `eval.yaml` drawn as five levers on an Instrument Shelf: Lever Yellow for `contains`, Lever Red for trajectory scorers, white (Enamel Plate) for spares. Each lever has a number disc, a mono name, a condensed caption, and a Berth Black tag.

### Ledger rows (book)
- **Style:** a 110px tag column in condensed 700 uppercase Pencil, prose in Book Ink, all on 32px lines, indented 120px past the margin. Two-column variant for short facts. Rulebook entries use a Red Ink condensed rule number in a 64px column.

### Slips and telegram
- **Slips:** diffs and tickets on Slip Paper with a 1px Rule Blue outline, 32px lines; deletions struck through in Red Ink, additions on Highlighter.
- **Telegram:** the real PR comment, a light paper card with a 6px Caution Yellow top border, laid on the panel.

### Train register and time-distance graph
- **Register:** real CLI output in a Well, failures, regressions and a non-zero exit in bold Danger Red; a passing replay's results and exit `0` in bold Clear Green.
- **Graph:** a span table drawn against the clock: rows in mono, Route Lamp bars (Caution Yellow for tool spans), seam gridlines at 1 s.

### Motion
- **One clock:** each lit track circuit has a delay of n × 110ms and switches on (a 1ms step), so the route lights circuit by circuit; it runs once on arrival and each time an NX button is pressed. Easing `cubic-bezier(0.16, 1, 0.3, 1)`; a plain toggle fades lit track over 260ms. With reduced motion the route lights at once.

## Do's and Don'ts

### Do:
- **Do** give every red, green, and yellow on the panel a state: danger or fail, clear or pass, caution or underpowered.
- **Do** keep unset routes unlit: base track in Unlit Track, off-route berths dark.
- **Do** alternate panel and book sections, and register every line and gap in the book to the 32px rule pitch.
- **Do** keep book content right of the margin rule (120px indent) on wide screens.
- **Do** letter the panel in condensed uppercase with the cut-in shadow, and screw plates down with two screw heads.
- **Do** drive motion from the one clock: route lamps lighting in sequence on arrival and on route setting.
- **Do** draw scorer verdicts as block instruments and scorer config as levers in lever colors.
- **Do** put real values in every berth, dial, lever, and register line.

### Don't:
- **Don't** use a lamp color as decoration.
- **Don't** light track or berths for a route that isn't set.
- **Don't** add a second animation, or animate anything besides route lamps and the push-button press.
- **Don't** add a dark-mode or light-mode variant; the world is one fixed scheme.
- **Don't** emphasize with a new color where scale will do.
