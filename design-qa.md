# Product Design QA — Prime Intellect visual direction

Earlier Engine-to-reference result: passed

## Comparison target

- Source: [Prime Intellect](https://www.primeintellect.ai/), captured in Microsoft Edge on 2026-10-13.
- LocalForge route: `http://127.0.0.1:5173/#engines`, empty-profile state with the local API connected.
- User goal: adapt the reference's cinematic dark photography, crisp navigation, restrained green light, and structured content modules for LocalForge's engine dashboard.
- The 1440 × 1024 source and implementation captures were combined at their native dimensions and reviewed side by side.

## Visual review

The page now leads with an immersive, full-width compute-lab photograph. Visible room detail continues behind the headline, with lime and amber light carried into the dashboard accents. The announcement strip is centered, the navigation reads as segmented numbered tabs, and the two working areas use clear numbered labels, lightly raised surfaces, and brighter boundaries. White primary actions and warm white body text keep the engine setup legible against the dark canvas.

The narrow Edge capture keeps the title, primary action, catalog details, and profile image readable. The navigation remains horizontally scrollable at the narrow viewport.

| Priority | Finding | Resolution |
| --- | --- | --- |
| P2 | The previous hero left a broad empty black area, making the page read as a split between flat color and a small photo. | Replaced it with an original wide architectural compute-lab photograph with detail across the full frame and a lower-contrast text area. |
| P2 | The image-led introduction felt short beside the reference's large photographic opening. | Increased the desktop hero to a responsive 420–520 px range and raised the mobile hero to 250–300 px. |
| P2 | Panel boundaries and supporting copy blended into nearby dark surfaces. | Raised panel/card separation and kept secondary copy at readable contrast while preserving the dark palette. |

The LocalForge screen remains focused on connecting runtimes and inspecting adapters. It uses the reference's visual rhythm while keeping the dashboard's existing controls and workflow.

## Implementation and checks

- The Add engine actions and catalog disclosures retain their existing behavior; this pass changes styling and imagery only.
- `npm run format:check`, `npm run lint`, `npm run typecheck`, and `npm run build` passed.
- No automated behavior tests were run for this visual-only change.

## Edge captures

- Prime Intellect source: `C:\Users\haisam.abbas\AppData\Local\Temp\prime-intellect-home.png` — 1440 × 1024.
- LocalForge desktop: `C:\Users\haisam.abbas\AppData\Local\Temp\localforge-engines-revised.png` — 1440 × 1024.
- Side-by-side comparison: `C:\Users\haisam.abbas\AppData\Local\Temp\prime-vs-localforge-revised.png` — 2880 × 1024.
- LocalForge narrow viewport: `C:\Users\haisam.abbas\AppData\Local\Temp\localforge-engines-revised-mobile.png` — 476 × 946. Edge's headless window uses a 476 px minimum CSS viewport; this is not reported as a 390 px capture.



## Product Design QA - remaining workspace pages

final result: passed

### Capture setup

- Browser: Microsoft Edge via Playwright's `msedge` channel.
- Local app: `http://127.0.0.1:5173/`; local API connected.
- Desktop viewport: 1440 x 1024 CSS pixels, device scale factor 1.
- Narrow viewport: 476 x 946 CSS pixels, device scale factor 1. Edge's minimum headless viewport width is 476 CSS pixels.
- State: empty engine profiles and run history; current local machine profile available. Experiments and Learning show not-yet-available states with links into implemented workflows.
- Browser console and page errors: none observed during route captures.
- Narrow-width check: document width matched the 476 px viewport on all six routes; the Experiments and Learning links navigated to their target sections.

### Reference and comparison captures

The existing Engines experience is the visual reference for the workspace's full-width photographic hero, typography, privacy badge, and elevated dark surfaces. Edge captures were reviewed side by side at native dimensions; comparison files are 2880 x 1060 pixels with labels.

- Reference: `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-after-engines.png` (1440 x 1024); narrow: `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-after-engines-mobile.png` (476 x 946).
- Overview: `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-after-overview.png`, `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-after-overview-mobile.png`; comparison: `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-design-compare-overview.png`.
- System: `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-after-system.png`, `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-after-system-mobile.png`; comparison: `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-design-compare-system.png`.
- Workloads: `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-after-workloads.png`, `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-after-workloads-mobile.png`; comparison: `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-design-compare-workloads.png`.
- Runs: `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-after-runs.png`, `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-after-runs-mobile.png`; comparison: `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-design-compare-runs.png`.
- Experiments: `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-after-experiments.png`, `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-after-experiments-mobile.png`; comparison: `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-design-compare-experiments.png`.
- Learning: `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-after-learning.png`, `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-after-learning-mobile.png`; comparison: `C:/Users/haisam.abbas/AppData/Local/Temp/localforge-design-compare-learning.png`.

### Visual review

Each screen now has its own panoramic image, while the shared overlay, title scale, navigation, privacy treatment, and dark content surfaces follow the Engines page. The subjects are distinct: compute workspace, PC hardware, fiber-lit workload infrastructure, run instrumentation, experiment benches, and a learning notebook with hardware. Left-side shading keeps the title and description readable over the art.

The page bodies retain their current workflows. Desktop content uses consistent gutters; Runs now aligns with the other page surfaces. At 476 pixels the titles stay left aligned, the privacy badge yields space, and controls stack without clipping. Overview and Workloads descriptions no longer repeat their in-page headings. Experiments and Learning present contextual guidance and links to implemented areas instead of generic filler.

No visual regressions were observed across the six desktop and six narrow captures. Experiments and Learning remain placeholders because those features are not yet implemented; their design does not imply otherwise.

### Distinct hero assets

- Overview: `frontend/public/images/localforge-overview-hero.jpg` (2172 x 724).
- System: `frontend/public/images/localforge-system-hero.jpg` (2171 x 724).
- Workloads: `frontend/public/images/localforge-workloads-hero.jpg` (2172 x 724).
- Runs: `frontend/public/images/localforge-runs-hero.jpg` (2172 x 724).
- Experiments: `frontend/public/images/localforge-experiments-hero.jpg` (2172 x 724).
- Learning: `frontend/public/images/localforge-learning-hero.jpg` (2172 x 724).
