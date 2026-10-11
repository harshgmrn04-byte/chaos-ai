# ChaosPilot presentation

`ChaosPilot.pptx` is a 17-slide, 16:9 deck for Harsh's Bosch hackathon talk.
Diagrams, six tables, and the experiment-score chart are editable PowerPoint objects.
Each slide has presenter notes and source references. `renders/` contains previews of
the delivered PPTX, not just the draft.

## Rebuild

From `chaos-ai/`, use the installed Codex presentation runtime:

```sh
/Users/harsh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node docs/presentation/generate.mjs
```

The script resolves bundled dependencies from the home directory. Other installations
can set `CHAOSPILOT_RUNTIME_MODULES`, `CHAOSPILOT_PRESENTATION_SKILL`,
`CHAOSPILOT_RUNTIME_PYTHON`, and optionally `CHAOSPILOT_CHROME`.
No dependencies are added to the application. Chrome captures saved HTML with every
network request blocked. If Chrome cannot run, the generator emits explicit screenshot
FILL markers instead. It never starts Docker, loads dotenv, calls an LLM, or runs a fault.

Rebuilding requires the original local ignored `runs/` evidence and `.chaosai/baseline.json`,
plus the repository documentation and root `BUILD_PLAN.md`. Missing real evidence is an
error rather than an invented metric. All outputs and temporary build files remain here.
The latest directory is selected by UTC run ID, so review its context before rebuilding
after additional experiments. The current deck distinguishes completed campaign
`20261011-035048` from latest signal-interrupted Redis run `20261011-041633`.

## Evidence and verification

`evidence.json` records input SHA-256 hashes, slide content, notes, source references,
rounding, dependency edges and intentional FILL markers. Baseline limits are calculated
from recorded multipliers and absolute floors. Scores use saved integers and the saved
overall mean. Test counts come from the final follow-up in `docs/progress.md`, which
AGENTS.md requires reading; the older BUILD_PLAN checkpoint has historical counts.

`validation.json` records package integrity, geometry/font checks, native tables,
chart values and embedded workbook checks, and successful Artifact Tool import.
The literal chart workbook is a snapshot of saved experiment scores, not a source
workbook with formulas. Native PowerPoint opening and animation behavior are not
established by these validators.

The saved report images show the real watchdog UNDO event and report summary. The
watchdog image uses the actual event markup from the report, with readable styling,
and does not claim to be a terminal capture.

## Remaining screenshot

Slide 14 intentionally contains `[[FILL: approval prompt screenshot]]`.
Save a real, credential-free approval-prompt capture as `assets/approval.png`, then
rebuild and inspect slide 14. Do not substitute an invented terminal screenshot.

## You verify

- Open the deck in PowerPoint or your intended presenter app and check fonts, notes,
  chart labels, diagram connectors, and the repository hyperlink.
- Add the real approval screenshot and rehearse the short live talk.

Historical Phase 10 “You verify (Docker)” list, copied from BUILD_PLAN.md:
“run `scripts/clean_run.sh` from scratch on your machine, start to finish.”
That is a project reproduction check, not a command run for this documentation task.
