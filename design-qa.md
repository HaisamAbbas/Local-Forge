# Product Design QA — Prime Intellect visual direction

final result: passed

## Comparison target

- Source: [Prime Intellect](https://www.primeintellect.ai/), captured in Microsoft Edge on 2026-10-13.
- LocalForge route: `http://127.0.0.1:5173/#engines`, in its empty-profile state with the local API connected.
- User goal: carry the reference's dark, photo-led, high-contrast visual language into the existing dashboard while keeping LocalForge's engine setup clear.
- This is a style adaptation. Prime Intellect's homepage is a marketing page; LocalForge remains a task-focused inference dashboard.

## Viewports and evidence

- Source screenshot: 1440 × 1024 px, CSS viewport 1440 × 1024, device scale factor 1.
- LocalForge desktop screenshot: 1440 × 1024 px, CSS viewport 1440 × 1024, device scale factor 1.
- Combined comparison: 2880 × 1024 px, source and implementation side by side at native screenshot size.
- Responsive full-page screenshot: 590 × 1277 px at device scale factor 1.23. Edge measured a 476 × 946 CSS viewport with document and body widths both 476 px; the phone breakpoint (`max-width: 560px`) was active. Edge's headless window has a 476 px minimum CSS viewport, so this is not reported as an exact 390 px capture.

## Visual review

The implementation now uses a near-black canvas, warm white text, sharp outlined navigation and panels, white primary actions, and a sparse lime accent. Original LocalForge compute photography gives the page a dark hero and a distinct hardware image for the empty connection state. The catalog stays readable and the profile action remains prominent. Compared with the source, the content is denser because it is a working dashboard rather than a marketing homepage; that difference is intentional.

The first render review found these issues and they were corrected before the final capture:

| Priority | Finding | Resolution |
| --- | --- | --- |
| P2 | The earlier hero image was bright in its focal area and the blue-green accent mix did not carry the reference's dark visual tone. | Replaced it with an original, lower-key server-room photograph and moved the UI accent to restrained lime with warm white controls. |
| P2 | The empty profile panel used a generic server icon and a large tinted blank area. | Replaced it with an original close hardware photograph, a short explanatory line, and the existing first-engine action. |
| P1 | An older mobile rule hid the brand mark in the responsive navigation. | Restored the brand in the compact header and checked the responsive capture for page-level horizontal overflow. |

No actionable P0, P1, or P2 visual findings remain in the final desktop or responsive captures. The brand, engine controls, status colors, and secondary text remain distinguishable on the near-black surfaces.

## Interaction and implementation checks

- The API reports connected in the captured screen. The adapter disclosures and the existing Add engine action were not changed; the form-opening handler remains attached to the same button.
- No browser console inspection or E2E run was performed in this pass. No application behavior was intentionally changed.
- `npm run format:check`, `npm run lint`, `npm run typecheck`, and `npm run build` passed.

## Screenshot paths

- Source: `C:\Users\haisam.abbas\AppData\Local\Temp\prime-intellect-home.png`
- Desktop implementation: `C:\Users\haisam.abbas\AppData\Local\Temp\localforge-engines-final.png`
- Source and implementation comparison: `C:\Users\haisam.abbas\AppData\Local\Temp\prime-vs-localforge-final.png`
- Responsive implementation: `C:\Users\haisam.abbas\AppData\Local\Temp\localforge-engines-final-mobile-raw.png`
