# PromptFit Studio 2.1.0

## What changed

- Consolidated the workout composer, race-plan builder, FIT editor, verifier,
  and Garmin delivery controls into one guided workspace.
- Reorganized the flow around **Start → Build → Review → Deliver**.
- Added a responsive phone layout with a bottom workflow bar for quick Garmin
  access.
- Moved advanced targets, mileage normalization, FIT internals, and alternate
  upload controls behind clear progressive-disclosure panels.
- Removed repeated API credential fields and created one shared Settings area.
- Generated single-workout FIT files now load directly into the editor on the
  same page.
- Written plans are prepared automatically as the source for calendar
  generation, eliminating the previous download-and-reupload step.
- Preserved `/plan` and `/fit-editor` links for existing bookmarks.
- Added macOS, Windows, and Docker launch paths plus a repeatable release
  packager.
- Added saved multi-pace athlete profiles and deterministic pace resolution for
  common Daniels, Pfitzinger, Canova, Hansons, Tinman/Schwartz, McMillan, and
  general coaching terminology.
- Added a native SwiftUI iPhone companion project for personal installation
  through Xcode. It connects to PromptFit on the same trusted Wi-Fi for workout
  creation, graph review, FIT sharing, and confirmed Garmin uploads.

## Compatibility

- Python 3.11 or newer is recommended.
- Modern versions of Safari, Chrome, Edge, and Firefox are supported.
- Garmin Connect upload remains unofficial; local FIT export works without it.

## Verification performed

- Workout FIT export and parse round-trip
- Preset plan calendar ZIP generation
- Prompt fallback FIT generation
- Desktop and phone-width layout checks
- Legacy route and static asset checks
- Python and JavaScript syntax checks
