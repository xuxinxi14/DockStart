**Comparison Target**

- Source visual truth:
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-facf131a-a11d-44e3-af85-7668c7ffdadf.png` — selected task card, 379 × 162 px.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-f76d1f6d-8dbb-4747-8cd1-bf25f7d1420d.png` — previous help task banner, 1245 × 241 px.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-68ae977e-9db8-4421-af8e-28e66738d5aa.png` — full project creation page, 1743 × 1230 px.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-18d4f1fb-d0fb-43fb-87c3-a1a8acc4bd3e.png` — redundant first-run chooser, 1708 × 1156 px.
- Browser-rendered implementation:
  - `E:\DockStart\output\playwright\dockstart-startup-ui\01-help-page.png` — help page viewport.
  - `E:\DockStart\output\playwright\dockstart-startup-ui\02-help-task-guide.png` — focused help task guide, 968 × 380 px.
  - `E:\DockStart\output\playwright\dockstart-startup-ui\03-project-create-default.png` — default project creation page, 1600 × 1000 px.
  - `E:\DockStart\output\playwright\dockstart-startup-ui\04-project-create-pose-score.png` — selected pose-scoring state, 1600 × 1000 px.
  - `E:\DockStart\output\playwright\dockstart-startup-ui\05-project-create-assisted-pose.png` — assisted/local pose-scoring state, 1600 × 1000 px.
  - `E:\DockStart\output\playwright\dockstart-startup-ui\06-project-task-picker.png` — focused task picker, 948 × 183 px.
- Viewport: 1600 × 1000 CSS px, Chrome, dark theme, device scale factor 1, screenshot scale `css`.
- State: no project loaded; help is the application start page; project navigation opens the consolidated project creation page. Task states checked: global docking, pose scoring, and assisted/local pose scoring.
- Density normalization: source screenshots use different crops and sizes. Contact sheets preserve each crop's aspect ratio and downsample only for shared visual review; no density-only mismatch was filed.

**Full-view Comparison Evidence**

- `E:\DockStart\output\playwright\dockstart-startup-ui\comparison-create.png` compares the supplied complete creation-page reference with the browser-rendered consolidated creation page.
- The implementation intentionally puts “本次任务” before “输入来源”, following the requested scientific information architecture. The lower duplicated “打开已有项目” form is absent; the page-header action remains.
- The no-project “项目” navigation was exercised from the help page and opened this creation page directly, so the redundant first-run chooser is no longer on the reachable startup path.

**Focused Region Comparison Evidence**

- `E:\DockStart\output\playwright\dockstart-startup-ui\comparison-task-picker.png` shows that the selected radio is now a normal circular control without the former input-like rectangle. The entire card remains the selected-state surface.
- `E:\DockStart\output\playwright\dockstart-startup-ui\comparison-help.png` shows the former over-wide two-action banner beside the aligned three-task guide. The new section uses the same 20 px content inset and divider rhythm as neighboring help modules.

**Findings**

- No actionable P0, P1, or P2 findings remain.
- Fonts and typography: existing DockStart families, weights, line heights, and hierarchy are retained. Task labels and scientific prerequisites remain readable without truncation at the tested viewport.
- Spacing and layout rhythm: task/source sections share the main-panel edges, “本次任务” has a full 20 px top inset, and the help task guide aligns with adjacent help sections. Document width equals viewport width (1600 px); no horizontal overflow was detected.
- Colors and visual tokens: selected, hover, border, rail, and text colors use existing DockStart design tokens. No new palette or competing surface style was introduced.
- Image quality and asset fidelity: these screens contain no raster product imagery. Existing Phosphor icons are retained; no replacement CSS drawings, emoji, or handcrafted SVG assets were introduced.
- Copy and content: the three task descriptions now state use case, input prerequisite, result, and scientific boundary. Pose scoring/local optimization explicitly require a positioned ligand in the receptor coordinate system.

**Comparison History**

- Initial supplied evidence:
  - [P1] Global input styling made the radio look like a rectangular text input.
  - [P2] “本次任务” was visually crowded against the source tabs.
  - [P2] The lower “打开已有项目” form repeated the header action in every source tab.
  - [P2] The help task banner did not align with neighboring sections and omitted global docking.
  - [P1] The no-project dashboard duplicated the complete project creation page and split the startup decision across two screens.
- Fixes made:
  - Reset the scoped radio dimensions/background/focus style while keeping card-level keyboard focus.
  - Reordered the decision flow to task first, input source second, with explicit section spacing.
  - Removed the shared inline open-project form and retained the single page-header action.
  - Rebuilt the help area as an aligned three-card guide with scientific prerequisites and output expectations.
  - Routed no-project home navigation to the complete project creation page while preserving help as the application start page.
- Post-fix evidence: `comparison-task-picker.png`, `comparison-help.png`, and `comparison-create.png`. No P0/P1/P2 issue was found in the post-fix pass.

**Primary Interactions Tested**

- Help page → “创建第一个项目”.
- Help page → sidebar “项目” → consolidated project creation page.
- Global docking → pose scoring task selection.
- Existing PDBQT → assisted source tab while pose scoring is selected.
- Project creation page → “返回帮助”.
- Verified exactly one selected radio, zero duplicate inline open-project forms, and no horizontal document overflow.
- Browser console check: 0 errors, 0 warnings after the Tauri browser audit mock was installed.

**Implementation Checklist**

- [x] Selected task control has no rectangular input artifact.
- [x] Task selection has clear top spacing and precedes input-source selection.
- [x] Duplicate inline open-project form removed from all source tabs.
- [x] Help section aligned and expanded to explain all three tasks.
- [x] No-project project entry opens the complete creation page.
- [x] Frontend production build passes.
- [x] Browser screenshots and focused comparisons reviewed.

**Follow-up Polish**

- None required for this handoff. A narrower-window responsive pass can be included in the later unified UI test cycle.

**v0.13.2 Incremental Check**

- Source visual truth:
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-5a319e69-d293-4e7f-8b27-da0d0ea5f9ab.png` — crowded task heading, 430 × 129 px.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-5734efcd-d833-49c5-b913-29b3af33ea61.png` — redundant input-source sentence.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-5eedfc4b-e05d-41f3-b8e7-d1f247e88743.png` — ligand selection without file-level visibility or controls.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-8e5be943-5551-4f5d-a4a2-9dd8917b4314.png` — oversized tool-details disclosure.
- Browser-rendered implementation: `E:\DockStart\output\playwright\v0.13.2-ui\project-create-task-spacing.png`, 1728 × 1040 CSS px, Chrome/Playwright CLI, dark theme.
- Combined comparison: `E:\DockStart\output\playwright\v0.13.2-ui\comparison-task-spacing.png` contains the supplied task-heading crop and the corresponding v0.13.2 region in one review image.
- The task heading now uses the existing section-title scale with 24 px visual inset above the heading content and 32 px section top padding; the removed source sentence is absent from both DOM snapshot and screenshot.
- Selected ligand rows use existing surface, border, icon, text-button, and danger-color tokens. Each row exposes a visible file name/path and separate replace/remove actions; the frontend production build validates both PDBQT and SDF/MOL branches.
- The tool-details disclosure is scoped to the preparation context rail: compact centered summary when collapsed, left-aligned contents when expanded. No global `details` styling was changed.
- Visual review found no clipping, horizontal overflow, misaligned borders, or competing radius/palette treatment at the tested viewport.

**v0.13.2 Density, Local Output, and Report Preview Check**

- Source visual truth:
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-8f88d6e4-0902-438c-b332-98f2f781ef8f.png` — structure search density and alignment.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-580c9ac9-7723-4efe-9bce-f48241840bd9.png` — Vina parameter baseline alignment.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-b02c6cb0-f16e-4aeb-acbc-c21593a151d9.png` — Vina maps action density.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-1c7e0959-2159-43ee-9e81-7175e12171cf.png` — redundant task-mode explanation.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-36c60994-e58a-4038-a59e-226f23fea26f.jpg` — native browser title tooltip.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-ff270d53-f79e-4d9c-b8c0-fbbcee45c92a.png` — crowded export badge.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-5f6845c8-6d40-4169-babc-c718aa2a1033.png` — result-side actions.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-5605de6f-bfe2-41ba-8683-78e7e4444a90.png` — report page before Markdown preview.
- Browser-rendered evidence (Chrome/Playwright CLI, 1728 × 1040 CSS px, dark theme):
  - `E:\DockStart\output\playwright\v0.13.2-review\01-structure-fetch.png`
  - `E:\DockStart\output\playwright\v0.13.2-review\02-run-workbench.png`
  - `E:\DockStart\output\playwright\v0.13.2-review\03-run-parameters.png`
  - `E:\DockStart\output\playwright\v0.13.2-review\04-report-preview.png`
  - `E:\DockStart\output\playwright\v0.13.2-review\05-report-preview-focused.png`
  - `E:\DockStart\output\playwright\v0.13.2-review\06-result-directory-actions.png`
- Focused implementation evidence:
  - `07-workspace-modes.png`, `08-vina-maps-actions.png`, `09-vina-parameter-row.png`, `10-result-directory-actions-focused.png`, and `11-report-status-card.png` in the same review directory.
- Combined source/implementation comparisons:
  - `comparison-structure.png`, `comparison-workbench.png`, and `comparison-report.png` in the same review directory.

**Findings and Verification**

- Structure source summaries now remain on one compact row; the hidden live-status element no longer occupies a grid cell. Search controls and local-import disclosure rows share a tighter, consistent rhythm for receptor and ligand.
- Scoring protocol, numeric parameters, and helper rows share one baseline. The maps save and import cards fit their content instead of stretching to the tallest card, while retaining responsive stacking.
- The task-mode bar contains only the three controls; the far-right explanatory sentence is removed.
- Runtime DOM contains zero `[title]` attributes before and after a 900 ms hover on the top-right Help action. Its `aria-label` remains `帮助`, so removing the native gray tooltip does not remove the accessible control name.
- The report status card places the status badge at the far-right vertical center. The result rail exposes “打开拓扑 SDF 目录” and “打开报告目录”. Playwright recorded both commands with the expected project, run, target, and project-relative SDF path.
- The Rust resolver accepts only the current project's existing `reports` directory or the current run's existing `.sdf` under `exports`; traversal, wrong extensions, symlinks, and missing project markers are rejected.
- Markdown preview reads only an existing UTF-8 `.md` under the project `reports` directory, limits content to 2 MiB, and renders headings, paragraphs, lists, block quotes, tables, fenced code, emphasis, and inline code through React nodes without HTML injection.
- The tested page had `clientWidth = scrollWidth = 1728`; browser console reported 0 errors and 0 warnings.
- `npm run build` passed. Targeted Rust path-boundary test passed: 1 test, 32 filtered out.

**Remaining Scope**

- No installer was built and no full release suite was run, matching the requested fast visual-review scope.

**v0.13.3 Maps and Structure Heading Check**

- Source references:
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-3acbaf3e-9d70-4bf1-ad4a-3092fb59a5f9.png` — requested combined maps constraint/generation module.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-c643e7e7-679a-4acb-af6d-b08f2ddbe272.png` — adjacent maps import module.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-a32f98c3-6208-4850-aec6-e9a18d827124.png` — receptor/ligand summary-heading baseline.
- Browser-rendered evidence (Chrome/Playwright CLI, 1728 × 1040 CSS px, dark theme):
  - `E:\DockStart\output\playwright\v0.13.3-ui\maps-panel-final.png`
  - `E:\DockStart\output\playwright\v0.13.3-ui\structure-source-headings.png`
- Combined source/implementation comparisons:
  - `E:\DockStart\output\playwright\v0.13.3-ui\comparison-maps.png`
  - `E:\DockStart\output\playwright\v0.13.3-ui\comparison-structure-headings.png`
- The maps constraints and save action now form one coherent left module beside the import module. Playwright confirmed both action cards have the same rendered height and the action grid has no horizontal or vertical overflow.
- Receptor/ligand English step labels and Chinese role titles use a stronger hierarchy while remaining on one row. Both summary bars passed horizontal-overflow checks.
- `npm run build` passed. No broad backend, Rust, scientific, installation, or release-gate suite was run for this fast candidate.

**v0.13.4 Unified Structure Input and Layout Check**

- Source references:
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-070bc92b-0dc0-4892-a92f-535030376ae2.png` and `codex-clipboard-9e130ea2-e456-4243-87f0-9de412caad71.png` — disclosure summaries before centering.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-7d15343a-9a36-48dc-b537-2f5ea2aab10c.png` — clipped scoring/maps panel.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-0a78663f-f913-4a18-a690-4e859878fc69.png`, `codex-clipboard-393565e4-d031-44ab-9cd0-69d832c61272.png`, and `codex-clipboard-1305b7ed-9164-49ca-872b-27b4e5427e95.png` — duplicated PDBQT/raw input routes.
- Browser-rendered evidence (Chrome/Playwright CLI, 1728 × 1040 CSS px, dark theme):
  - `E:\DockStart\output\playwright\v0.13.4-ui\project-create-unified.png`
  - `E:\DockStart\output\playwright\v0.13.4-ui\preparation-unified.png`
  - `E:\DockStart\output\playwright\v0.13.4-ui\run-protocol-overflow.png`
- Combined comparison evidence:
  - `E:\DockStart\output\playwright\v0.13.4-ui\comparison-unified-input.png`
  - `E:\DockStart\output\playwright\v0.13.4-ui\comparison-run-protocol.png`
  - `E:\DockStart\output\playwright\v0.13.4-ui\comparison-disclosures.png`

**v0.13.4 Findings and Verification**

- Project creation has one structure-input route. Receptor and ligand are classified independently, so PDBQT/raw pairs are accepted without forcing a shared mode.
- The preparation page no longer has separate direct-PDBQT and conversion tabs. A prepared PDBQT displays ready state and on-demand 3D preview; a raw structure exposes the matching conversion action.
- Expandable summaries use the existing DockStart surface and text tokens with centered labels; arrows remain independently aligned at the edge where present.
- Browser-native form history is disabled on project/path inputs. The inspected text inputs reported `autocomplete=off`; radio controls remain unaffected.
- Run-workbench measurements: document `1728/1728`, scoring/maps card `1080/1080`, and maps panel `1048/1048` for client/scroll widths. No right-side clipping or horizontal overflow remains.
- The project-create page measured `clientWidth = scrollWidth = 1728`.
- `npm run build` passed. The focused structure-input classifier test passed (2/2). No broad backend, Rust, scientific, installer-lifecycle, or release-gate suite was run for this fast candidate.

**v0.13.5 Help, Window Controls, and Receptor Recovery Check**

- Source references:
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-6c0ffffa-c39b-41e3-86e4-aed6d124b276.png` — two quick-start cards leaving an empty third column.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-f9c98e61-eaf9-43ae-8185-ff05f8c3e7c2.png` — narrow native window controls.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-384ebef8-c076-4e7c-9210-0d70cd37e673.png` — verbose troubleshooting heading.
- Browser evidence: `E:\DockStart\output\playwright\v0.13.5-ui\help-start-window.png` and `help-common-questions.png`, captured at 1728 × 1080 CSS px in the existing DockStart dark theme.
- Combined comparisons: `comparison-help-cards.png`, `comparison-window-controls.png`, and `comparison-common-questions.png` in the same directory.
- The quick-start grid now renders two equal 527 px columns inside a 1064 px content width. The cards fill the row without introducing a third empty track.
- Minimize, maximize, and close controls each render at 56 × 59 px. Their native `title` attributes are absent, while accessible labels remain.
- The former troubleshooting heading is now the compact “常见问题 / 安装自检” module. Accordion summaries remain aligned and collapsed by default.
- Document `clientWidth` and `scrollWidth` both measured 1728 px; no horizontal overflow was introduced.
- Targeted TypeScript/Vite production build passed. The official `1fpu_receptorH.pdb` recovery flow is covered by backend tests and a real bundled-Meeko conversion, but the recovery card could not be driven through the browser-only Tauri mock in this visual pass.

**v0.13.6 Flexible Receptor Review Check**

- Source reference: `C:\Users\19701\AppData\Local\Temp\codex-clipboard-a63d2344-c184-47c4-bbde-c8b59963c31d.png`, showing raw multi-candidate backend output expanding the page and shifting the result column.
- Browser-rendered evidence: `E:\DockStart\output\playwright\v0.13.6-ui\flexible-review-fixed.png`, captured at 1280 × 720 CSS px with the official 1FPU `A:315` review state.
- Combined comparison: `E:\DockStart\output\playwright\v0.13.6-ui\flexible-review-comparison.png`.
- The structured `FLEX_BAD_RESIDUES_REVIEW_REQUIRED` response now renders a bounded confirmation card instead of a raw-error transcript. The complete 27-residue review list wraps inside the card and remains user-expandable.
- Document and body `clientWidth`/`scrollWidth` both measured 1280 px. The expanded review preformatted region measured 585/585 px, used `white-space: pre-wrap`, and produced no horizontal document overflow.
- `A:315` remains a valid selected flexible residue. The 27 unrelated Meeko template mismatches are presented for explicit confirmation, after which preparation may continue with the official `--allow_bad_res` semantics.
- No actionable P0, P1, or P2 visual findings remain in this state.

**v0.13.7 Flexible Summary and Macrocycle Workflow Check**

- Source references:
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-b2d1c700-a264-4a85-b305-55a41bb2a52d.png` — oversized flexible-receptor ready card with duplicated status prose.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-c49374d1-98d6-4986-9ed4-0d6b8cf722f4.png` and `codex-clipboard-15f19ea1-04ef-4eb6-9e71-a6606331e539.png` — disconnected macrocycle failure, settings, and continuation controls.
- Browser-rendered evidence (Chrome/Playwright CLI, dark theme):
  - `E:\DockStart\output\playwright\v0.13.7-ui\flexible-panel.png`
  - `E:\DockStart\output\playwright\v0.13.7-ui\macrocycle-panel-final.png`
- Combined source/implementation comparisons:
  - `E:\DockStart\output\playwright\v0.13.7-ui\comparison-flexible.png`
  - `E:\DockStart\output\playwright\v0.13.7-ui\comparison-macrocycle.png`
- The flexible-receptor ready state is now a compact aligned summary row containing the preparation ID, selected residue, reviewed-residue count, and mode actions. The redundant sentence and unused card height are removed.
- Macrocycle preparation is a visible four-step workflow: analyze candidates, select a bond set, confirm the break, and convert PDBQT. The prepared state exposes a direct `设置搜索范围并继续` action in the same module.
- At the 1728 px viewport, document width measured `1728/1728` and the macrocycle module measured `1030/1030` for client/scroll width. The continuation action was visible and the browser console reported zero errors and zero warnings.
- No actionable P0, P1, or P2 visual findings remain in the two requested states.

**v0.13.8 Batch Preparation, Macrocycle, and Toolchain Check**

- Source references:
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-e4e6e934-5b74-4cda-ade9-e6f37f0ecefe.png` — receptor review controls overflowing a narrow action column.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-eaba7809-3854-4b30-aa2b-088c4f9a608c.png` — one generic ligand shown after a six-ligand import.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-54e6996d-6215-46b8-a39c-0599eef3d8d6.png` — crowded macrocycle settings and unclear selection state.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-9b3e864c-2684-467b-b6a5-8cf217a1fe97.png` — equal-height toolchain columns and unstable expanded details.
- Browser-rendered evidence (Chrome/Playwright CLI, DockStart dark theme):
  - `E:\DockStart\output\playwright\v0.13.8-ui\preparation-review-1180.png`
  - `E:\DockStart\output\playwright\v0.13.8-ui\preparation-batch-1180.png`
  - `E:\DockStart\output\playwright\v0.13.8-ui\preparation-macrocycle-reviewed-1180.png`
  - `E:\DockStart\output\playwright\v0.13.8-ui\toolchain-expanded-1180.png`
- Same-composite source/implementation comparisons reviewed:
  - `comparison-receptor-review.png`, `comparison-batch-ligands.png`, `comparison-macrocycle.png`, and `comparison-toolchain.png` in `E:\DockStart\output\playwright\v0.13.8-ui`.
- The structure-review section now spans the preparation panel and measures `911/911` client/scroll width at 1180 px; no controls overlap the right rail.
- The batch browser exposes all six source identities (`P69`, `P59`, `P55`, `P48`, `P38`, `P33`), switches the 3D candidate without mutating the active project, and distinguishes source records from unique docking snapshots.
- The macrocycle radio is 16 × 16 px with transparent background and no box shadow. Candidate rows use square internal separators with one left selection marker; the selector measures `853/853` and the document `1180/1180`.
- The toolchain page has a visible AutoGrid4 configuration action. Its two-column card grid measures `569/569`; expanded bundled-resource details render below the card grid at `569/569`, so card columns and the right rail do not jump or overlap.
- No actionable P0, P1, or P2 visual findings remain in these tested states.

**v0.13.8 Batch Results, AutoGrid Gate, and Hydrated Navigation Check**

- Source references:
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-f04c2fbb-5223-4947-93c7-cb3f86e94a50.png` — oversized blue checkbox focus rectangle.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-e920661d-01ea-4186-b40f-90b03d77da69.png` and `codex-clipboard-dd0c73c4-ff38-471c-a3ea-c3df147cadf3.png` — blank batch pose viewer and ordinary-run empty result state.
  - `C:\Users\19701\AppData\Local\Temp\codex-clipboard-cdb0f405-ce03-4690-b99e-41f5cf082164.png` — coupled toolchain card rows with irregular empty space.
- Browser-rendered evidence (Chrome/Playwright CLI, 1728 × 1050 CSS px, DockStart dark theme):
  - `E:\DockStart\output\playwright\latest-fixes\batch-checkbox-focus.png`
  - `E:\DockStart\output\playwright\latest-fixes\batch-results.png`
  - `E:\DockStart\output\playwright\latest-fixes\batch-pose-modal.png`
  - `E:\DockStart\output\playwright\latest-fixes\toolchain-layout.png`
  - `E:\DockStart\output\playwright\latest-fixes\hydrated-autogrid-warning.png`
  - `E:\DockStart\output\playwright\latest-fixes\hydrated-sidebar-finished.png`
- Same-input source/implementation comparisons were reviewed for the checkbox, batch empty/result states, pose dialog, and toolchain layout.
- Focused batch checkboxes now compute to `15 × 15 px`, `box-shadow: none`, and `outline: none`; keyboard focus is carried by the containing row without the oversized blue rectangle.
- A completed screening opens a dedicated result workspace with completion metrics, frozen protocol, output records, sortable ligand rows, and a working pose dialog. Its Results sidebar entry now resolves to the green ready state. The real `text3_Batch_docking` project returned verified receptor and pose content for `ligand_0001`, Mode 1.
- Vina/Python and AutoGrid4/resource cards flow in independent columns, so one expanded or content-heavy card no longer stretches the unrelated card beside it.
- The hydrated workflow presents a visible AutoGrid4 gate and configuration action before maps generation. A finished `hydrated_ad4_experimental` run produces the green Hydrated AD4 sidebar state.
- Browser console reported zero errors and zero warnings. TypeScript/Vite build and focused frontend tests passed.

final result: passed
