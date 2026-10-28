# UI redesign: handoff for testing and review

The UI layer was reworked: theme, scroll story, floating top bar, black and white palette, IST
clock and circuit-wiring background. The forecasting pipeline and data logic are unchanged.
Nothing is committed yet.

## Files

| File | Status | What it holds |
|---|---|---|
| `ui_story.py` | new | Scroll story (Streamlit Custom Component v2 `ao02_story`), operator view only |
| `ui_fx.py` | new | `WIRES_JS` (shared circuit wiring), `ambient()` (site background and live clock), `server_hall()` (decorative 3D hall, tenant view only) |
| `assets/style.css` | rewritten | HUD skin, floating top bar, full-screen layout, black and white palette |
| `.streamlit/config.toml` | rewritten | Dark theme, black and white tokens, fonts (Space Grotesk, Inter, JetBrains Mono), `toolbarMode="viewer"` |
| `assets/logo.svg`, `assets/mark.svg` | changed | White and grey rack glyph, red LED |
| `app.py` | changed | See the next section |
| `selfcheck.py` | one line | Picks the "View as" selectbox by label, not index (see below) |

The other modified files (`config.py`, `pipeline/*`, the "Data centre" selector, `REVIEW_NOTES.md`)
came from a parallel session. They are not part of this UI work.

## `app.py` changes

- **Palette and helpers:**
  - The `UI` palette dict is neutral greys.
  - `TOTAL_COLOR = "#fb923c"` colours the rack-total line in "Tenant demand by workload". It sits
    outside the tenant palette and away from the yellow and red attention lines.
  - Added helpers `panel()`, `kpi()` and `sev_badge()`, plus the dicts `SEV_SOLID`, `SEV_ICON`,
    `SEV_BADGE` and `RES_COLOR`.
- **`story_payload(results)`** (operator only) builds the story's data:
  - per rack: status, headline, and a series per resource (actual every 3rd day over the last
    180 days, P50 forecast, safe/full ETA markers);
  - per rack and workload: tenant shares as % of capacity;
  - month ticks and HUD counts.
- **Page structure:**
  - Operator: `ui_story.story(story_payload(results))`, then the existing tabs.
  - Tenant: the static hero plus a decorative `ui_fx.server_hall`, then the existing tabs.
- **Page setup:** `set_page_config(..., initial_sidebar_state="expanded")`, because the sidebar is
  the top bar and must never collapse.
- **Sidebar:** dividers removed. The key/value list (Source, Data as of, Horizon, Confirm) and the
  "Read-only" footer are plain `st.html`.
- **Clock:** label and placeholder are "IST clock" / `--:--:-- IST`.

## How the pieces work

- **Scroll story (`ui_story.py`):**
  - The component is 640vh tall. Its 100vh `.stage` is pinned by translating it by `-rect.top` on
    every animation frame, and the scroll container is `[data-testid="stMain"]`.
  - Progress `p` (0 to 1) drives three acts: `ACT = {roofEnd: .16, hallStart: .18, hallEnd: .74, tenStart: .82}`.
    1. Roof top view in black and white: 3×5 chiller fans spinning, 420 steam particles.
    2. Camera descends and dwells at each rack. A chart card draws the rack's combined resource
       graph: axes rise from the bottom and the line is revealed by a clip-path, once per rack.
       Hovering a rack (raycast) switches the card to that rack.
    3. Top view of the rack row. Each rack gets a squarified treemap of tenant % of capacity, with
       a hatched "free" tile, a used-vs-safe bar, and a Compute/Storage/Network toggle.
  - **Many racks** (for example M100, 49 racks):
  - 3D racks stand in rows of at most 12 (`COLS_MAX`, `ROW_GAP = 7`). Decorative rows and the
    roof grow to fit.
  - The guided hall tour stops at no more than 8 racks (`TOUR_MAX`): critical, then warning, then
    plan, then the rest, visited in floor order. The step list ends with "+N more · hover any
    rack", and hovering still reaches every rack.
  - Tenant treemap cards hang over their racks only when one row fits them at 150px or more.
    Otherwise they form a grid of about 190–240px cards between the header and the legend. Rows
    that don't fit scroll with the page: the story grows by `st.mapsScroll` px, and the `.mi`
    wrapper is translated by the extra scroll.
  - The story's container key is now `ao02_story_<rack hash>` (set by the other session), so the
    CSS uses `[class*="st-key-ao02_story"]`.
- three.js 0.160.0 loads from jsdelivr via dynamic `import()`. If it fails, the overlays (HUD,
    charts, treemaps) still work and only the 3D scene is missing.
  - Rendering pauses when the story is off screen and is capped at about 45 fps. The 3D camera
    doesn't move when the OS asks for reduced motion.
- **Circuit wiring (`WIRES_JS` in `ui_fx.py`):**
  - `makeWires(canvas, {density, alpha})` returns `{resize(w, h, dpr), draw(t)}`.
  - Random grid traces (orthogonal with 45° turns) are pre-rendered once per resize to an
    offscreen canvas.
  - Pulses travel at 18–42 px/s with a fading tail, then rest for a random time.
  - Used in two places:
    - the story: a `<canvas class="wires">` with `mix-blend-mode: screen` between the WebGL canvas
      and the vignette;
    - the site background: `#dc-ambient` (fixed, z-index 0), which replaced the old packet streaks.
  - The ambient JS runs once per page (`window.__dcAmbient` guard) and redraws at about 30 fps,
    paused in hidden tabs.
- **Clock:** `toLocaleTimeString("en-GB", {timeZone: "Asia/Kolkata", hour12: false}) + " IST"`,
  in both `ui_fx` (`.hud-clock` elements) and `ui_story` (`[data-clock]`).
- **Floating top bar:**
  - It's CSS only: `[data-testid="stSidebar"]` becomes a fixed, centred glass pill. The selectboxes
    have inline labels, and the collapse button and resize handle are hidden.
  - Sidebar captions turn into an "i" chip with a hover popover. The caption container's built-in
    `opacity: .6` is reset to 1 so the popover is opaque.
  - Breakpoints: below 1500px the footer is hidden, below 1400px only 2 key/values show, below
    1080px no key/values show, and below 860px the bar shows just the logo and selects.
- **Full screen:**
  - `stMainBlockContainer` has no max-width, and padding is `5rem 1.5rem`.
  - `.st-key-ao02_story` uses negative margins so the story bleeds edge to edge under the bar.

## Constraints a reviewer should verify

1. **Tenant isolation.**
   - `story_payload` is only called for `viewer == "Operator"`.
   - The tenant view gets a constant decorative `server_hall` payload (`[{"name": "", "status": "NONE"}] * 4`,
     `live=False`) with no rack names, capacity or other tenants.
   - Check that the browser payload in the tenant view has no other tenant names.
2. **Selfcheck:** it must end `ALL PASSED`.
3. **Selfcheck selectbox lookup:**
   - It now finds the selectbox with `next(sb for sb in at.sidebar.selectbox if sb.label == "View as")`.
   - This changed because the parallel session's "Data centre" select became `selectbox[0]`.
   - Renaming the "View as" label breaks the test.
4. **Read-only:** no new controls act on the facility. The only story interactions are hover and
   the workload toggle, both client-side.
5. **External resources:** jsdelivr (three.js) and Google Fonts. Without internet the 3D scene and
   fonts fall back and everything else works.

## How to test

```bash
venv/bin/python selfcheck.py          # must print "ALL PASSED"
venv/bin/streamlit run app.py --server.port 8502
```

Streamlit does not reload imported modules (`ui_story.py`, `ui_fx.py`) when they change, so
restart the server after editing them. `style.css` and `app.py` reload on rerun.

**Manual checklist** (Chrome, about 1600×1000, then about 1387×600 and a phone width):

- **Opening screen:** roof top view with fans turning slowly, steam rising, wiring pulses moving,
  and the HUD clock ticking in IST.
- **Hall:** scroll down. The camera drops through the roof and visits each rack. Each chart draws
  in once, with axes rising and the line drawing. Hovering a different rack switches the card.
- **Many racks:** switch Data centre to M100. The hall tour shows 8 racks plus "+41 more", the racks stand in rows, and the tenant act shows a readable card grid that scrolls through all racks.
- **Tenant act:** treemaps sit centred over the rack tops. The Compute/Storage/Network toggle is
  clickable, below the bar. Free space is hatched, and the bar turns amber within 10 points of the
  safe limit and red at or above it.
- **Below the story:**
  - The Overview tabs run full width.
  - Plotly charts are black with grey grids.
  - The rack-total line is orange.
  - The panels' corner brackets are visible.
  - Wiring shows faintly in the gutters.
- **Top bar:**
  - Both selects open.
  - Switching "View as" to a tenant shows the static hero and decorative hall, with no story.
  - On hourly datasets (Alibaba 2018), hovering the "i" chip shows an opaque note.
  - The bar never overflows the viewport.
- **Reduced motion:** with `prefers-reduced-motion: reduce`, wiring is static, no CSS animations
  run, and the camera snaps instead of easing.
- **Console:** no errors besides a three.js warning when offline.

## Known limitations

- Scrolled content (the tab strip) passes under the floating bar. This is intended, since the bar
  is a floating header.
- The story is operator only. The tenant view keeps the earlier static hero.
- `.stApp::after` scanlines and the ambient canvas are always on: about 30 fps of 2D canvas work
  while the tab is visible.

## Backups (pre-change copies)

- `/tmp/claude-1000/-home-anmol-rogue-ao02/735927dc-c90b-4cef-8145-74d6aa72a29e/scratchpad/backup/`: original `app.py` and `config.toml`
- `/tmp/claude-1000/-home-anmol-rogue-ao02/735927dc-c90b-4cef-8145-74d6aa72a29e/scratchpad/backup_v2/`: the first redesign (`app.py`, `assets/`, `config.toml`)
