---
version: 1
slug: "site-index-html"
primary_target: "site/index.html"
related_targets: []
---

# Landing page (site/)

Scope: the public landing page for Trayce, a standalone static site in `site/`, separate from the self-hosted app. Mode: Persuade. Redesign: the first build ("Route Diagram") was judged too basic by the user; it is evidence, not authority. The user delegated the direction choice ("let it do its thing").

Audience and job: senior engineers who ship LLM agents, between incidents, evaluating tools. They should leave believing a prompt change can't silently break their agent's path, and run the stack locally.

Action: run it locally (clone, `docker compose up -d --wait`), with a working copyable command in the first viewport. Secondary: add the GitHub Action.

Proof: real captures in `site/proof/` and `site/img/` (2026-10-27): the 1042 trace, main-vs-PR traces for "Can I get a refund on order 1044?", real eval output, the real PR comment (✅ verdict with `steps` underpowered at −50 pts), the app's side-by-side view. No logos, testimonials, counts, or prices.

Constraints: brand toolkit voice, claims, and lexicon binding; visual direction open; WCAG 2.2 AA; no eyebrow labels.

Unresolved: replay output not captured; domain undecided; PyPI name settled as `trayce` but not yet published.

## Direction contract

THESIS: Trayce is the signal box on your agent's line: every run is a route set across an illuminated track diagram, a prompt change throws the points, and the gate is the signal that holds the merge. Refuses the dark hero with glow, the screenshot-over-gradient, and three feature cards.

OWN-WORLD: An NX interlocking panel: drenched slate-enamel ground, pale unlit track lines, warm-white route lamps, red track-occupied and danger lamps, green clear aspects, yellow caution. Engraved condensed caps on plates (Barlow Condensed), Barlow for prose, JetBrains Mono in train-describer berths and the occurrence book. A lever frame in the real lever colors (red signal, yellow distant, black points, blue lock, white spare). Paper sections are the box's occurrence book: grey-green ledger ruled in blue and red.

STORY: The visitor sees two runs of the 1044 refund question set across one diagram, the PR's points reversed past lookup_order, the block instrument's verdict, then sets the route themselves: the quickstart.

FIRST VIEWPORT: A full-bleed panel. Top-left engraved plate holds the headline (to about 5.5rem) and subline; top-right a plate with the copyable command and a round panel push-button "Run it locally". The lower 55% is the track diagram, edge to edge: main line and PR line from the question through the points at the first LLM call, berths with real span describers, exit signals showing the scorers' aspects.

FORM: Signal-box interlocking panel, candidate 3 of 7 on my list (re-roll round 1); seed:903637ce15c6b1f3. Signature interaction: NX route setting — entrance/exit buttons light main's or the PR's route lamp by lamp; any berth shows its span in the describer. Raises: total palette commitment (depot blind), scale not color for emphasis (Massin), lit means live (Akari), one clock drives all motion (paper menagerie), statuses print as register lines, not badges (phosphor terminal), baseline poses and PR answers across one seam (Brodovitch).

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
