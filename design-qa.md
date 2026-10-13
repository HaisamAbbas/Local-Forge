# Product Design QA — Prime Intellect visual direction

final result: passed

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
