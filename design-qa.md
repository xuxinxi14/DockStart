# DockStart v0.9.6 Design QA

## Comparison Target

- Source visual truth:
  - `E:\DockStart\.codex-ui-audit\v0.9.6-references\structure-preparation.png`
  - `E:\DockStart\.codex-ui-audit\v0.9.6-references\docking-workbench.png`
  - `E:\DockStart\.codex-ui-audit\v0.9.6-references\result-analysis.png`
- Rendered implementation:
  - `E:\DockStart\.codex-ui-audit\v0.9.6-implementation\qa-preparation.png`
  - `E:\DockStart\.codex-ui-audit\v0.9.6-implementation\qa-docking.png`
  - `E:\DockStart\.codex-ui-audit\v0.9.6-implementation\qa-results.png`
  - Runtime interaction evidence: `E:\DockStart\.codex-ui-audit\v0.9.6-implementation\qa-results-fresh-mode2.png`
- Viewport: 1570 x 1002 CSS pixels, device pixel ratio 1.
- Runtime: real Tauri desktop application rendered by WebView2 at `http://127.0.0.1:1420/`.
- State:
  - Preparation: `result_demo_005`, existing-PDBQT and raw-structure modes.
  - Docking: `result_demo_005`, receptor and ligand loaded, valid 12 x 12 x 12 A box, preflight ready.
  - Results: fresh `result_demo_005`, `run_001`, modes 1-3 available; modes 2 and 3 loaded successfully during interaction QA.

## Comparison Evidence

### Full-view comparisons

- `E:\DockStart\.codex-ui-audit\v0.9.6-implementation\compare-preparation.png`
- `E:\DockStart\.codex-ui-audit\v0.9.6-implementation\compare-docking.png`
- `E:\DockStart\.codex-ui-audit\v0.9.6-implementation\compare-results.png`

Each comparison places the selected visual target and the rendered implementation in one image. The implementation intentionally applies the user's later all-dark, slightly lighter deep-blue direction across all three workspaces; the first two source images retain their earlier light center surfaces.

### Focused region comparisons

- `E:\DockStart\.codex-ui-audit\v0.9.6-implementation\focus-preparation.png`
- `E:\DockStart\.codex-ui-audit\v0.9.6-implementation\focus-docking.png`
- `E:\DockStart\.codex-ui-audit\v0.9.6-implementation\focus-results.png`

The focused comparisons cover typography hierarchy, tab and control spacing, the 3D viewport and Box controls, ranked pose rows, result tabs, the right context rail, status colors, icons, borders, radii, and fixed action placement.

## Findings

- No actionable P0, P1, or P2 difference remains.
- [P3] The bundled viewer-result demo contains deliberately tiny toy receptor and ligand structures, so the rendered molecules occupy less of the 3D viewport than the illustrative design target. This is expected scientific-data fidelity, not a layout defect; no fake atoms or replacement molecular imagery were added.
- [P3] Minor text-density and row-height tuning may still be useful after testing with long real-world project and file names. Current controls remain aligned, readable, and free of horizontal overflow at the QA viewport.

### Required fidelity surfaces

- Fonts and typography: existing system CJK font stack retained; title, section, label, metadata, and table hierarchy match the target's industrial console character. No broken wraps or truncated primary actions were observed.
- Spacing and layout rhythm: the four-workspace shell, central work areas, right rails, lower ledgers, table rows, and fixed docking action bar remain aligned at 1570 x 1002. No horizontal overflow was observed.
- Colors and visual tokens: the app uses a coherent deep-blue token scale that is intentionally slightly lighter than the all-dark reference. Button blue and link/selection blue are separated, with readable status and disabled states.
- Image and asset fidelity: the product logo and existing Phosphor icon system are preserved. Molecular imagery is rendered from real project PDBQT content in 3Dmol; no placeholder, CSS-art, emoji, or fabricated scientific image substitutes were introduced.
- Copy and content: Chinese terminology remains consistent with the DockStart scientific wording rules. The interface continues to state that docking scores do not prove binding or efficacy.
- Accessibility and interaction: tabs expose selected state, Box bindings expose `aria-pressed`, icon-only viewer controls retain labels, navigation remains keyboard-reachable, and selected/status states do not rely on color alone.

## Comparison History

1. [P1] The docking primary action was below the initial viewport.
   - Fix: converted the run action area into a persistent bottom bar positioned above the status bar.
   - Post-fix evidence: `qa-docking.png` and `compare-docking.png`.
2. [P2] Result metadata duplicated the engine name and allowed awkward time text.
   - Fix: normalized engine/version display, computed elapsed time from run timestamps when needed, and formatted saved time consistently.
   - Post-fix evidence: `qa-results.png` and `focus-results.png`.
3. [P2] The result example advertised modes 2 and 3 while its output file contained only one unwrapped pose.
   - Fix: aligned the example with three explicit `MODEL` / `ENDMDL` blocks and added backend assertions for modes 1-3.
   - Post-fix evidence: fresh `result_demo_005`; runtime loaded Mode 2 and Mode 3 with no alert or console error.
4. [P2] The earlier fragmented flow exposed redundant Box, Vina, and result surfaces.
   - Fix: consolidated the visible navigation into four workspaces, retained legacy route compatibility, made the docking workbench the single visible Box/run surface, and embedded pose analysis in the result page rather than a dialog.
   - Post-fix evidence: all three final full-view and focused comparisons.

## Primary Interactions Tested

- Four-item sidebar navigation between Project, Structure Preparation, Docking Workbench, and Results.
- Preparation mode switch between existing PDBQT and raw-structure preparation; selected tab state and controls updated without errors.
- Box `size_x` mouse-wheel binding changed 12.0 to 12.1 A; binding `size_y` then cleared the `size_x` binding, confirming single-selection behavior.
- Fresh result demo creation through the desktop UI.
- Mode 2 and Mode 3 selection and 3D pose loading from `run_001`.
- Persistent docking actions, integrated result viewer, result tabs, and absence of the previous pose dialog.
- Browser/WebView console: `Runtime.exceptionThrown` and error-level `Log.entryAdded` were monitored during the above interactions; no errors were recorded.

## Implementation Checklist

- [x] Apply the approved slightly lighter deep-blue theme globally.
- [x] Consolidate visible navigation without breaking legacy project routes.
- [x] Rebuild Structure Preparation, Docking Workbench, and Results around the selected visual targets.
- [x] Preserve real project data, scientific disclaimers, and existing backend commands.
- [x] Verify core interactions and fresh multi-pose demo data in the real desktop runtime.
- [x] Compare source and implementation at full-view and focused-region levels.

## Follow-up Polish

- Revisit compact row density only after collecting screenshots from longer real project names and larger receptor/ligand files.
- Fine-tune 3D camera defaults later with representative scientific datasets rather than changing the toy demo to make screenshots look fuller.

## Final v0.9.6 Corrective Pass

- User evidence:
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-c033b7a9-d0e6-4a95-a5f9-8e258beff618.png`
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-257fb25a-4cdc-440f-9721-62a1afe454ce.png`
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-6cb049aa-6a6e-4b20-9a6d-83fec6d4d639.png`
- Rendered implementation evidence:
  - `E:\DockStart\.codex-ui-audit\v096-dark-custom-titlebar.png`
  - `E:\DockStart\.codex-ui-audit\v096-light-theme.png`
  - `E:\DockStart\.codex-ui-audit\contrast-fullscreen-box.png`
  - `E:\DockStart\.codex-ui-audit\contrast-fullscreen-box-axes-hidden.png`
- Verified in the real Tauri desktop runtime at 1200 x 800:
  - native gray title decoration removed and replaced by an integrated draggable command bar;
  - new DockStart artwork loaded in the application shell;
  - dark/light theme state persisted through a Tauri rebuild;
  - sidebar brand and command bar both measured 60 px high;
  - sidebar footer and workspace status bar shared the same top coordinate;
  - full-screen Box values and wheel binding stayed synchronized with the normal inspector;
  - axis visibility toggled in both run and result viewers without runtime errors.

## Structure Source Horizontal Workflow Pass

- User reference:
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-755fbfab-5f6d-4046-a17b-26b4d56631d2.png`
- Real Tauri implementation:
  - `E:\DockStart\.codex-ui-audit\structure-fetch-horizontal\accepted-search-results-1000.png`
  - `E:\DockStart\.codex-ui-audit\structure-fetch-horizontal\accepted-search-results.png`
- Same-comparison inputs:
  - `E:\DockStart\.codex-ui-audit\structure-fetch-horizontal\comparison-1000.png`
  - `E:\DockStart\.codex-ui-audit\structure-fetch-horizontal\comparison.png`
- Regression evidence:
  - `E:\DockStart\.codex-ui-audit\structure-fetch-horizontal\navigation-after-pending-search.png`
- Reference viewport: 978 x 894 pixels.
- Compact implementation viewport: 1000 x 900 CSS pixels, captured at 150% device scaling.
- State: the receptor query `1IEP` returned one selectable RCSB result; the receptor workflow is shown before the ligand workflow.

### Visible findings and iterations

1. [P1] The former receptor/ligand side-by-side columns compressed search results, metadata, previews, and actions.
   - Fix: converted the page into two full-width work rows, receptor first and ligand second. Each row now uses a status summary, a wide search/result workspace, and a local import/action area.
2. [P1] A 981-1070 px viewport could retain the wider internal grid while the card clipped overflow.
   - Fix: introduced the compact breakpoint at 1120 px, reducing search controls to two columns and stacking candidate list and preview without horizontal clipping.
3. [P1] Leaving the page while an online search or preview request was unfinished could allow late Tauri responses and delayed 3D callbacks to update an unmounted page.
   - Fix: added a page operation scope with generation checks before parsing or committing responses; deferred 3D work now also verifies the active generation and connected container.
4. [P2] Large candidate payloads could make route changes feel blocked by synchronous preview work.
   - Fix: limited the complete serialized interactive preview response to 2 MiB, enforced a hard network deadline in the short-lived backend process, and used a lighter receptor representation.
5. [P2] Busy states did not consistently communicate that controls were temporarily unavailable.
   - Fix: all relevant search, import, selection, overwrite, and management controls now share the row busy state, with `aria-busy`, labelled regions, and live status text.

### Runtime verification

- At the compact viewport, the 1IEP result, its metadata, `3D 预览`, and `选择并准备` remained visible without horizontal clipping.
- In the current release-mode desktop build, a receptor search was started and the sidebar `对接工作台` action was invoked 35 ms later.
- Navigation completed in 378 ms; the process reported `responding: true`.
- The right-side white assistant bubble visible in QA screenshots belongs to the Codex capture workflow and is not part of DockStart.

### Automated verification

- Frontend production build: passed, 4638 modules.
- Frontend async lifecycle tests: 4 passed.
- Backend tests: 375 passed, 7 subtests passed.
- Rust desktop tests: 18 passed.

final result: passed
