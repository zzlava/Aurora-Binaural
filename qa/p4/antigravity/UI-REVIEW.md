# UI finish review

## 1. Disposition

**fix** — retain the current interface and visual system. The desktop/narrow and light/dark renders are valid, coherent, and usable. Two specific interaction defects remain in the supplied code; neither calls for a redesign.

## 2. Evidence checked

- Opened all four full-page screenshots: `screenshots/desktop-light.png` and `desktop-dark.png` (1440 × 1689), `screenshots/narrow-light.png` and `narrow-dark.png` (390 × 2479). Each begins at the document top, shows the claimed theme and layout, and contains loaded preview/export results. No blank regions, clipped controls, or visible horizontal overflow were found.
- Read `web/DESIGN.md`, `web/PRODUCT.md`, `web/src/main.tsx`, `web/src/style.css`, `web/src/player.ts`, and `web/src/api.ts`; compared the implementation with the supplied P4 functional brief and Impeccable craft floor.
- Read `qa/p4/antigravity/design-detector.json` (`[]`). This records no detector findings; it does not establish functional correctness.
- Read the existing shortcut assertions in `web/tests/workflow.spec.ts:78–110` to assess the gap below. The reported 4/4 Playwright result was supplied by the build agent; this reviewer did not rerun browsers or tests.
- Calculated representative WCAG text contrast directly from the CSS tokens. This is a code-led surface; no approved image comp or human visual approval was supplied or assumed.

## 3. Scorecard

| Area | Assessment | Evidence and implication |
| --- | --- | --- |
| P4 brief and task clarity | **4/5 — fix** | Import, presets, strength, source-time preview settings, A/B, full export, queue/report/download, connection modes, and folded developer area are present. The exact prominent stub statement and SADIE II default are clearly visible. Shared-clock dual-buffer playback and gain-only switching are implemented. Shortcut focus handling needs correction. |
| Contrast and state distinction | **5/5 — pass at reviewed scope** | Representative ratios: light body/surface 12.21:1, secondary/background 4.93:1, secondary/selected 4.50:1, primary button 7.05:1; dark body/surface 12.49:1, secondary/selected 4.97:1, primary button 9.76:1. Warning text is 6.87:1 light and 8.60:1 dark. Selected presets and A/B states use both native/pressed semantics and visible treatment. |
| Readability and hierarchy | **4/5 — pass** | Main task labels are easy to scan. Import → settings → preview → export → queue is preserved on narrow screens. Secondary measurements are intentionally compact (mostly 10–11px); they remain legible in the actual captures and do not replace the main action labels. No requested copy or content needs redesign. |
| Responsive behavior and feedback | **4/5 — fix** | The 390px captures stack presets and actions cleanly; metadata wraps without hiding downloads. Both themes preserve hierarchy. Disabled/loading/error/progress/cancel states have explicit code paths. Clicking the already-active connection mode currently discards the workspace without an actual mode change. |

## 4. Material fixes

1. **P2 — Keep A/B shortcuts available after interacting with playback buttons.** `web/src/main.tsx:247–261` ignores all key events whose target is any `button`, link, or summary. Normal use leaves focus on “播放试听” or an A/B button, so clicking B and pressing A does nothing; the advertised `A / B 切换` requires an undisclosed click on blank page content. The existing test explicitly clicks `h1` before checking shortcuts (`web/tests/workflow.spec.ts:92`), which misses this case. Separate the guards for letter shortcuts and Space: protect editable/form input contexts, allow A/B from playback buttons, and avoid double-handling native Space activation. Add a focused-control assertion: click B → press A → A becomes pressed; click Play → press B → B becomes pressed while position continues; verify Space does not double-toggle.

2. **P2 — Make selecting the current connection mode a no-op.** `web/src/main.tsx:486–505` always calls `connect("local")` or `resetConnection()` even when that mode is already active. Both clear imported assets, queue entries, and the loaded player (`resetConnection`, approximately lines 108–121). A redundant click on a selected segmented control therefore removes the user's working session while server jobs can keep running. Guard the active mode or disable its mode-switch action; keep the existing explicit reconnect control for failed local connections. Verify that repeated clicks on the selected Local or Remote mode preserve asset count, queue entries, and loaded preview, and that changing to a different mode still follows the intended reset behavior.

These are the complete material fix list for this review. Preserve the current layout, colors, copy, and primary task structure.

## 5. Limitations

- Screenshots establish the completed single-file flow only. Running/canceled/failed tasks, wrong-key recovery, multi-file import, empty/disconnected states, remote forms, and focus rings were assessed from code and supplied test evidence, not independently rendered by this reviewer.
- No browser, long test suite, real device, Safari/Firefox, assistive technology, human listening, or backend conformance check was run. Static review supports the common-clock A/B design; it does not certify audible seamlessness or BS.1770 accuracy.
- This verdict covers UI usability and the supplied functional brief at the reviewed sizes. It is not approval of P1 audio rendering, HRTF application, deployment, or publication.

## 6. Verdict pass — original two fixes only

**Disposition: ship at the scope of the two material fixes.** Both findings are **resolved**. This verdict supersedes the initial `fix` disposition for that list; it does not constitute a new whole-surface audit.

| Original finding | Score | Evidence checked |
| --- | --- | --- |
| P2: A/B shortcuts fail when playback buttons retain focus | **resolved** | The updated handler in `web/src/main.tsx:247–272` protects editable inputs while allowing A/B from focused buttons. Space within `.player` prevents default activation and calls playback once; native Space behavior outside the player remains available. Repeat keydowns are ignored. Updated `workflow.spec.ts` removes the heading-defocus workaround and asserts Play-focus B, B-button-focus A/B, and Space pause with a stable position. WebAudio assertions also require exactly two equal source-start records across switching. |
| P2: selecting the current connection mode clears the workspace | **resolved** | Both mode handlers return before connection/reset work when the requested mode is already selected. The updated workflow assertions verify the local selected-mode click preserves the imported asset, preview/export queue entries, and enabled loaded player; the remote selected-mode click preserves the uploaded asset and permits export. Actual changes to a different mode still reach the existing switch/reset path. |

Reopened all four refreshed screenshots at the original paths. They remain valid full-page captures in the named desktop/narrow and light/dark combinations, with the asset, loaded player, and completed task rows intact. No recapture is required for this verdict.

The build agent supplied successful npm build/lint and 4/4 E2E results (22.8s). This reviewer inspected the changed code, focused assertions, and refreshed images without launching a browser or rerunning tests. No additional defect hunt was performed. Backend checks, real-device behavior, audible seamlessness, and the earlier stated limitations remain outside this fix-list verdict.
