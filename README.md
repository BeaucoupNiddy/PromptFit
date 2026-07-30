Running Plan → Calendar + Garmin FIT
====================================

This toolkit turns a running plan into calendar files, FIT workouts, and even on-demand workouts generated from natural language prompts.

PromptFit Studio 2.0 now brings the complete workflow into one responsive page:

1. **Start** with a quick workout, a full race plan, or an existing FIT file.
2. **Build** from plain language, a bundled preset, or plan JSON.
3. **Review** every workout leg and pace segment in the integrated FIT editor.

PromptFit also supports a saved multi-pace athlete profile and understands common Daniels, Pfitzinger, Canova, Hansons, Tinman/Schwartz, McMillan, and general coaching terminology. See [PACE_TERMINOLOGY.md](PACE_TERMINOLOGY.md) for precedence, aliases, and inference behavior.
4. **Deliver** by download, USB sideload, or explicit Garmin Connect selection.

For a shareable install, start with [QUICK_START.md](QUICK_START.md). Release
details are in [RELEASE_NOTES.md](RELEASE_NOTES.md).

Feature highlights:
- Generate HTML and ICS calendars with embedded X-WORKOUT JSON for every session.
- Detect interval repeats automatically so Garmin watches show lap/rep counters in exported FIT workouts.
- Export spec-compliant FIT workouts with warmup/cooldown safeguards, optional pace/speed targets, and cleaned names.
- Parse generated FIT files to sanity-check headers, durations, targets, and repeat metadata without leaving the GUI.
- Run a Prompt → FIT FastAPI web app that accepts free-text plans, calls OpenAI/OpenRouter, and emits FIT or uploads to Garmin Connect (unofficial).
- Optional macOS Keychain integration for API keys plus a one-time, token-based Garmin connection.
- Headless CLI/GUI utilities to convert existing ICS files into FIT workouts.

What’s inside:
- training_plan_gui.py: End-to-end GUI to generate ICS/FIT, view FIT summaries, and launch the ICS→FIT tool.
- ics_to_fit_gui.py: Standalone GUI + headless `convert_ics_to_fit` helper for bulk FIT export and inspection.
- hm_plan_calendar.py: Plan generator and scaling logic used by both GUIs.
- hm_plan_to_garmin.py: Core workout flattener with repeat detection and target generation knobs.
- final_spec_compliant_fix.py: Minimal FIT writer built on `fit_tool` that mirrors known-good Garmin files.
- webapp/app.py (+ webapp/static/): Prompt → FIT FastAPI service with a lightweight frontend and optional Garmin Connect upload.

Install (recommended venv)
- macOS/Linux:
  - `python -m venv .venv && source .venv/bin/activate`
  - `pip install -r requirements.txt`
- Windows (PowerShell):
  - `py -m venv .venv`
  - `.venv\Scripts\Activate.ps1`
  - `pip install -r requirements.txt`

Launch the Plan GUI
```
python training_plan_gui.py
```

Workflow (Plan GUI)
- Pick your plan JSON (weeks/days/workouts).
- Set race date, race distance, target race pace, and peak mileage.
- Click “Generate Plan” to create an ICS in `Calendars/`.
- Under “FIT Export (Spec‑Compliant)”, choose the output dir (default `fit_out_gui`).
- Optional targets:
  - Enable pace targets: adds a custom speed range per step (m/s), encoded correctly for devices.
  - Mode “pace” or “speed”: both produce proper speed targets (pace is converted to speed for FIT).
  - ± sec/mile: sets the width of the range around the base pace.
  - Include WU/CD targets: by default warmup/cooldown remain open; toggle to add targets.
- Naming: exported FIT workout names include a `NNwDDd` prefix (e.g., `02w05d`) for Week/Day to aid sorting on watch.
- Notes: targeted steps include a single pace note (mm:ss/mi) based on the midpoint of the target range.
- Click “Generate FITs (spec)” to write Garmin FIT workouts.
- Click “Parse Output” to view a compact decode of the files (headers + first steps).

Standalone ICS → FIT
```
python ics_to_fit_gui.py
```
Select an ICS exported by the Plan GUI, pick an output directory, and click “Generate FITs”. Use “Parse Output” to inspect them quickly.

Dependencies
------------

Core: `ics`, `fit_tool`, `fitparse`
Web app: `fastapi`, `uvicorn`, `keyring`
Optional (Garmin Connect upload, unofficial): `garminconnect`, `garth`
See `requirements.txt` for versions.

FIT Writer Notes
----------------

- File layout matches the “Workout file” spec: File Id → Workout → Workout Step messages only.
- Names are sanitized to ASCII, remove parentheses/tilde, keep decimals, and are trimmed for device safety.
- Steps:
  - TIME steps encode duration so they decode/display as seconds.
  - DISTANCE steps encode raw distance so they decode/display as meters, matching working samples.
- Warmup/cooldown/recovery steps force open targets (no bounds) by default.
- Targets are optional and default off in the GUI for maximal device compatibility.
- Custom speed targets:
  - Stored as FIT raw units (m/s × 1000) for `custom_target_speed_low/high`.
  - `target_speed_zone=0` is set to mark custom ranges (important for some devices).
  - Step notes include a single target pace (mm:ss/mi) computed at the midpoint of the range.
- Simple easy runs:
  - For broad import compatibility, single‑step distance “easy‑like” runs are converted to a time‑based warmup step using the estimated duration.
  - This mirrors known‑working Garmin files that consistently import across devices.

Troubleshooting
---------------

- Missing dependencies: The GUIs warn and list `pip install` commands if `ics`, `fit_tool`, or `fitparse` are missing.
- FIT won’t import: Try leaving targets disabled and use simplified names. Use “Parse Output” to check decoded fields.
 - If targeted workouts fail to import: ensure `target_speed_zone=0` is present (our exporter sets this automatically) and that `custom_target_speed_*` decode to realistic speeds (2.5–5.5 m/s typical for running).
- Tk errors on Linux: Install your distro’s Tk package (e.g., `sudo apt-get install python3-tk`).

Repo Guide
----------

- training_plan_gui.py
- ics_to_fit_gui.py
- hm_plan_calendar.py
- hm_plan_to_garmin.py
- final_spec_compliant_fix.py

Outputs
- ICS files under `Calendars/`
- FIT files under `fit_out_gui/` by default

Prompt → FIT web app
--------------------

Start the server
```
./run_webapp.sh
```
Open http://localhost:8000 and:
- Paste a prompt like `8 mile easy @ 8:00/mi` (or a series).
- Select provider (auto/openai/openrouter) and provide an API key.
- Optional: set race distance, race pace, and easy pace (for mileage estimates), enable targets, ± sec/mi.
- Click “Generate FIT” — you’ll get a FIT or a ZIP.

Choose workouts → Garmin Connect
---------------------------------

Garmin's mobile share/import flow does not turn a FIT workout into a structured workout. The web app now handles the missing conversion: it reads a workout FIT file, recreates its steps/repeats/pace targets as a Garmin Connect workout, and adds it to your workout library through the community `garminconnect` integration.

1) On the Mac, double-click `run_webapp.command` (or run `./run_webapp.sh`) and open `http://localhost:8000/#garmin-connect`.
2) Under “Garmin connection,” enter your Garmin credentials and click “Connect Garmin once.” Enter a verification code if Garmin requests one.
3) For each future upload, check the exact workout or workouts in the “Workouts on this Mac” list, optionally choose a schedule date, and click “Upload checked workouts.” Nothing uploads automatically.
4) To select a Mac workout from your phone, keep the Mac and phone on the same trusted Wi-Fi and open the phone address printed by the launcher, such as `http://Your-Mac.local:8000/#garmin-connect`. The list is populated from the Mac's `fit_out_gui/` folder, so you do not have to transfer the FIT file onto your phone first. The phone does not need the Garmin password after the Mac is connected.
5) If a FIT file is somewhere else, use “Choose files from this device” as a fallback.
6) Open Garmin Connect and sync. Workouts are under More → Training & Planning → Workouts; use Garmin's send-to-device button if needed.

For full plans, the plan editor can generate quality-workout FITs only, package only those FITs, and upload a rolling number of weeks beginning today. Leave “Replace earlier PromptFit uploads” enabled to remove app-tracked workouts in that window before the refreshed plan is scheduled. The HTML plan preview also has upload/replace actions for each week and each generated workout day.

The Garmin password is discarded after setup. Revocable Garmin session tokens are stored with owner-only permissions under `~/Library/Application Support/PromptFit/garmin`. Connection and disconnection are localhost-only operations. Because phone access uses local HTTP, use it only on a private network you trust. The Garmin workout API is unofficial and can change.

Keychain (macOS, localhost only)
- The UI can load/save secrets (`/api/secrets`).
- Fields: `openai_api_key`, `openai_model`, `openrouter_api_key`, `openrouter_model`.
- CLI one‑time save:
  ```
  python - <<'PY'
  import keyring; svc='prompt-fit'
  keyring.set_password(svc,'openai_api_key','sk-...')
  keyring.set_password(svc,'openrouter_api_key','...')
  print('saved')
  PY
  ```

Sideload to Garmin
------------------

1) Connect the watch via USB and open the storage volume.
2) Copy `.fit` files to `GARMIN/Workouts` (preferred) or `GARMIN/NewFiles` (auto‑import on disconnect).
3) Safely eject. Workouts appear under Training → Workouts.

Notes:
- Some devices are picky with single‑step distance runs; the exporter emits compatible time‑based warmup steps for those.
- Targets on warmup/cooldown are optional; leave them off if a device behaves inconsistently.

Web App: Prompt → FIT (with Keychain)
-------------------------------------

Run locally
- Install deps in venv and start server:
  - `python -m venv .venv && source .venv/bin/activate`
  - `pip install -r requirements.txt`
  - `./run_webapp.sh`
- Open http://localhost:8000
- Paste a prompt, choose provider, optionally turn on targets.
- For Garmin Connect (optional, unofficial), use the localhost-only one-time connection, then explicitly select workouts to upload.

macOS Keychain integration
- Install keyring: `pip install keyring` (already in requirements.txt)
- Save once from Terminal:
  - `python - <<'PY'`
    `import keyring; svc='prompt-fit'`
    `keyring.set_password(svc,'openai_api_key','sk-...')`
    `keyring.set_password(svc,'openrouter_api_key','...')`
    `print('saved')`
    `PY`
- On page load, the UI (only on localhost) calls `/api/secrets` to prefill those fields.
- Click “Save secrets” in the UI to update Keychain from the browser (localhost only).

Docker (optional)
- Build: `docker build -t prompt-fit .`
- Run: `docker run -p 8000:8000 prompt-fit`
  - Note: macOS Keychain is not available inside the container. Use env vars or type keys in the UI when running in Docker.

Auto‑start on login (macOS launchd)
- Edit `launchd/com.promptfit.webapp.plist` and set your absolute project path.
- Copy, load, start:
  - `cp launchd/com.promptfit.webapp.plist ~/Library/LaunchAgents/`
  - `launchctl load ~/Library/LaunchAgents/com.promptfit.webapp.plist`
  - `launchctl start com.promptfit.webapp`
- Logs: `/tmp/promptfit.out.log`, `/tmp/promptfit.err.log`

Security notes
- Garmin Connect upload uses community libraries and private APIs; it may break. Web-based MFA is supported. The Garmin password is not saved; OAuth session tokens are persisted locally with owner-only permissions so future selected uploads do not require credentials. Disconnecting Garmin removes those local session tokens.

Disclaimer
----------

This project uses public FIT SDK concepts via the `fit_tool` Python library and is not affiliated with Garmin. Always verify workouts on your device before training.
