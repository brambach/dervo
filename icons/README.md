# Dervo app icon

The forest pebble, Dervo's one mark (design sweep, 19 Sep): pine with a cream face on the cream tile, drawn from the
geometry in `frontend/src/local/pebble.ts`. No external artwork or font is required.

`dervo.svg` is the editable source. `dervo-small.svg` is the same tile with the face drawn on the 16px pixel grid,
used for the 16 and 32px bitmaps, where the regular face loses its eyes. After installing the frontend dependencies,
run `python3 -B scripts/build-icons.py` from the app folder. The script uses the installed Tauri CLI, works offline,
and retains only the PNG and ICNS files referenced by `tauri.conf.json`. On a Mac it assembles the ICNS with
`iconutil`, so the ICNS carries the small face at 16 and 32.

The PNGs and ICNS have transparent outer margins. The native bundle still needs inspection after a build to confirm the
Dock and Finder use these assets.

## Icon choices (beta.15, group 7)

`choices/choices.json` lists the icons Settings > Preferences > App icon offers: Cream (the bundled icon above), nine
alternates and the founder Obsidian icon. Each alternate has a 1024px master (`choices/<id>.svg`) and a small face
(`choices/<id>-small.svg`) for 16 and 32px, like `dervo-small.svg`. Meadow, Crew, Terminal and Night lose their face at
16px with that small face, so they also have `choices/<id>-16.svg` ("tiny" in `choices.json`) for the 16px bitmap only:
the pebble and face as `dervo-small.svg` draws them, on their own tile, without the scenery. `scripts/build-icons.py` renders every choice to
`choices/<id>.icns` and a picker preview at `frontend/public/app-icons/<id>.png`. The designs come from
`evidence/design-tests/app-icons.html` and `founder-icons.html` in the mvp1 folder.

A Mac app can't switch its bundled icon the way an iOS app can. The running app sets its Dock icon
(`src/app_icon.rs`), and the Dock tile plug-in (`src/docktile`, built by `scripts/build-docktile.sh` into
`Contents/PlugIns`) keeps the choice in the Dock while Dervo is closed. Finder, Spotlight and Launchpad always show Cream.

## Dark, Clear and Tinted (macOS 26)

`Dervo.icon` is an Icon Composer source: the cream tile with the pine pebble, and Forest's colours as the Dark layer.
It was written by hand without Icon Composer or Xcode, so it hasn't been opened, compiled or checked, and it isn't in
the bundle. Open it in Icon Composer, check Default, Dark, Clear and Tinted, then add the compiled `Assets.car` to the
bundle in a later beta. Beta.15 ships without it (Bryce, 24 Sep 2026): this Mac has only the Command Line Tools, which
can't compile it.
