import json
import base64
import re
import copy
import os
import io
import tempfile
from pathlib import Path
import webbrowser
from datetime import date, datetime, timedelta
import html
from ics import Calendar, Event
from ics.grammar.parse import ContentLine  # for extra properties

# -------------------------------
# User Input Parameters (Modify These)
race_date = date(2025, 12, 13)       # Goal race date (final Saturday of week N)

# Base race pace in min/mi (used for % of race pace calculations)
race_pace_min_per_mile = 6.7
# Optional easy/non-workout pace (min/mi) to improve mileage estimates
easy_pace_min_per_mile = None
# All athlete-entered pace anchors.  The web/mobile apps populate this mapping
# so named intensities can use the athlete's exact pace rather than one generic
# percentage of a single race pace.
pace_anchors_min_per_mile = {}
# Race distance used for labeling/metadata (None -> infer from plan meta or default)
race_distance = None
peak_mileage = 50                   # Adjustable target peak mileage (default base JSON ~50 mpw)
input_json_file = "hm_plan_custom_19w_HMP_doubles.json"  # using the new 19-week plan with doubles
output_ics_file = "training_plan_hmp.ics"

# Scaling factor (will be set dynamically from plan meta in main())
factor = 1.0

# Separate scale controls
# Easy mileage scale follows `factor` by default; workouts can be dialed separately.
easy_scale_factor = 1.0           # derived from `factor`
workout_scale_factor = 1.0        # can differ from `factor`

# Optional overrides (user-tunable)
# If set, overrides workout_scale_factor computation in main().
workout_factor_override = None     # e.g., 0.85 to keep more of workout volume
workout_factor_multiplier = 1.0    # multiplies base `factor` to get workout factor if override not set
workout_factor_mode = "same"       # "same" (keep existing plan/base behavior), "normalize", "custom", "original"

# Start of week-1 (place Week 1 Monday). Historically assumed 16 weeks (110 days back),
# but we compute dynamically in main() based on len(weeks): (weeks-1)*7 + 5 days back
start_date = None  # computed in main()

# Global variable to track current phase for intelligent scaling
CURRENT_PHASE = None

# Reference race pace (min/mi) that the base plan was authored around
reference_hmp_min_per_mile = None

# Pace conversion exponent (Riegel). Used to translate between race distances.
RIEGEL_EXPONENT = 1.06

# Global toggle for alternating double-threshold days (AM/PM alternate)
double_threshold_toggle = True

# Global option: include implicit warmup/cooldown (~1.0 mi, ~9:15/mi) for
# workout types (interval/tempo/long/etc.). Set to False when your JSON
# already encodes warm-ups/cool-downs explicitly.
include_implicit_wu_cd = False

# Optionally scale explicit warm-up/cool-down segments (e.g., 10–20 min very easy)
scale_wu_cd_segments = False

# Optional override for implicit WU/CD totals (combined warmup+cooldown)
implicit_wu_cd_distance_miles = None  # total miles
implicit_wu_cd_duration_min = None    # total minutes
WU_CD_PACE_MIN_PER_MILE = 9.25        # default 9:15/mi for conversions

# Optional doubles controls
enable_optional_doubles = False   # when True, split days marked as double_optional
double_split_ratio = 0.65         # split main/short at 65% / 35%

# Collapse doubles: when a day has AM/PM and this is True, prefer keeping
# the workout session over an easy one. If both are easy, keep the longer.
collapse_doubles = False

# When a day has both AM and PM workouts (both are workout-like), keep them split by default.
# If set to True, consolidate them into a single session by concatenating segments/sets.
consolidate_two_workout_doubles = False

# Rest days configuration
# Target number of rest days per week (including any built-in rest days).
# 0 means "do not modify rest days".
rest_days_per_week_target = 0
# When removing days to reach the target, redistribute removed mileage across
# remaining easy-like days in the same week.
redistribute_removed_load = True

# Normalize weekly mileage across athlete paces by compensating on easy days
# so that time-based workouts don't cause slower athletes to under-hit mileage.
normalize_weekly_to_reference = True
# Normalization mode: "both" (default), "reduce_only", or "increase_only"
normalize_weekly_mode = "both"

# When normalizing, also reduce easy time for faster athletes to match baseline
normalize_reduce_for_fast = False

# For distance ranges like "10–12 mi", choose which value to use in calculations.
# Options: "high" (default), "midpoint", "low"
range_distance_preference = "high"

# Internal override for computing distances from time with a specific base pace
pace_override_min_per_mile = None

# When collapsing doubles (keeping workout and dropping an easy session),
# redistribute the dropped easy minutes evenly across easy-like sessions in the same week.
redistribute_collapsed_double_minutes = True

def _fmt_minutes(m):
    return f"{int(round(m))} min"

def _fmt_distance(amount, unit):
    unit = unit.lower()
    if unit in ["km", "kilometers"]:
        return f"{round(amount,1)} km"
    if unit in ["mi", "miles"]:
        return f"{round(amount,1)} mi"
    if unit in ["m", "meters"]:
        return f"{int(round(amount))} m"
    # fallback: miles
    return f"{round(amount,1)} mi"

def maybe_split_optional_double(workout):
    """
    If a workout dict carries 'double_optional': true and represents a single
    session (simple duration or distance), optionally split into AM/PM per
    double_split_ratio with the short PM marked optional. Otherwise, return as-is.
    """
    try:
        if not isinstance(workout, dict):
            return workout
        if not workout.get('double_optional'):
            return workout
        # Only split simple single-session entries
        if any(k in workout for k in ('am','pm','segments','sets')):
            return workout
        wt = (workout.get('type','') or '').lower()
        ds = workout.get('duration') or workout.get('distance')
        if not ds:
            return workout
        if not enable_optional_doubles:
            # keep as single session when splitting disabled
            return workout
        # Make a split copy
        am = { 'type': workout.get('type',''), 'intensity': workout.get('intensity','') }
        pm = { 'type': workout.get('type',''), 'intensity': workout.get('intensity',''), 'optional': True }
        dmin = parse_duration_str(ds)
        if dmin is not None:
            am_min = max(1.0, dmin * double_split_ratio)
            pm_min = max(1.0, dmin - am_min)
            am['duration'] = _fmt_minutes(am_min)
            pm['duration'] = _fmt_minutes(pm_min)
        else:
            parsed = parse_distance_str(ds)
            if not parsed:
                return workout
            ((low_mi, high_mi), unit, (orig_low, orig_high)) = parsed
            # prefer original units and value
            total = orig_high if orig_high else orig_low
            am_amt = max(0.1, round(total * double_split_ratio, 1))
            pm_amt = max(0.1, round(total - am_amt, 1))
            am['distance'] = _fmt_distance(am_amt, unit)
            pm['distance'] = _fmt_distance(pm_amt, unit)
        return { 'am': am, 'pm': pm }
    except Exception:
        return workout

# -------------------------------
# Helpers: race labels/metadata

_DEFAULT_RACE_DISTANCE = "half marathon"

def normalize_race_distance(value):
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    s = s.replace("_", " ").replace("-", " ")
    s = re.sub(r"\s+", " ", s)
    # Order matters: "half marathon" contains "marathon"
    if "half" in s or "13.1" in s or "21.1" in s or "21.097" in s:
        return "half marathon"
    if "marathon" in s or "26.2" in s or "42.1" in s or "42.195" in s:
        return "marathon"
    if "10k" in s or "10 km" in s or "10000" in s or "10,000" in s:
        return "10k"
    if "5k" in s or "5 km" in s or "5000" in s or "5,000" in s:
        return "5k"
    return None

def get_race_distance():
    return normalize_race_distance(globals().get("race_distance")) or _DEFAULT_RACE_DISTANCE

def race_distance_display(value=None):
    dist = normalize_race_distance(value) or get_race_distance()
    if dist == "5k":
        return "5K"
    if dist == "10k":
        return "10K"
    if dist == "marathon":
        return "Marathon"
    return "Half Marathon"

def race_pace_label(value=None):
    dist = normalize_race_distance(value) or get_race_distance()
    if dist == "5k":
        return "5K pace"
    if dist == "10k":
        return "10K pace"
    if dist == "marathon":
        return "Marathon pace"
    return "HMP"

def race_distance_miles(value=None):
    dist = normalize_race_distance(value) or get_race_distance()
    if dist == "5k":
        return 3.10686
    if dist == "10k":
        return 6.21371
    if dist == "marathon":
        return 26.2188
    return 13.1094

def _label_distance_miles(label_text: str):
    if not label_text:
        return None
    s = str(label_text).strip().lower()
    if "race" in s:
        return race_distance_miles()
    if "5k" in s:
        return 3.10686
    if "10k" in s:
        return 6.21371
    if "half" in s or "hmp" in s:
        return 13.1094
    if "marathon" in s:
        return 26.2188
    return None

def _pace_at_distance(pace_min_per_mile, from_miles, to_miles):
    """Estimate pace at a different race distance using Riegel scaling."""
    try:
        p = float(pace_min_per_mile)
        fm = float(from_miles)
        tm = float(to_miles)
        if p <= 0 or fm <= 0 or tm <= 0:
            return p
        return p * ((tm / fm) ** (RIEGEL_EXPONENT - 1.0))
    except Exception:
        return pace_min_per_mile

def _pct_from_label(pct, label_text):
    """Convert % of a labeled pace (e.g., 10k pace) into % of current race pace."""
    try:
        pct_val = float(pct)
    except Exception:
        return pct
    label_dist = _label_distance_miles(label_text)
    race_dist = race_distance_miles()
    if not label_dist or not race_dist:
        return pct_val
    if abs(label_dist - race_dist) < 0.01:
        return pct_val
    p_label = _pace_at_distance(race_pace_min_per_mile, race_dist, label_dist)
    target_pace = pace_at_percentage(p_label, pct_val)
    if not target_pace:
        return pct_val
    return (race_pace_min_per_mile / target_pace) * 100.0

def race_distance_key(value=None):
    dist = normalize_race_distance(value) or get_race_distance()
    if dist == "half marathon":
        return "half_marathon"
    return dist.replace(" ", "_")

def _pace_label_pattern():
    # Accept common race-pace labels for percent parsing
    return r"(?:hmp|hm\s*pace|half(?:\s*marathon)?(?:\s*pace)?|half\s*marathon|10k(?:\s*pace)?|5k(?:\s*pace)?|marathon(?:\s*pace)?|race\s*pace)"

def _format_pace_label(label_text):
    s = (label_text or "").strip().lower()
    if "hmp" in s or "half" in s or "hm pace" in s:
        return "HMP"
    if "10" in s and "k" in s:
        return "10K pace"
    if "5" in s and "k" in s:
        return "5K pace"
    if "marathon" in s:
        return "Marathon pace"
    if "race" in s:
        return "Race pace"
    return label_text.strip()

# -------------------------------
# Helpers: speed/pace math and parsing

def get_phase(week_num):
    if 1 <= week_num <= 5:
        return "General Phase"
    elif 6 <= week_num <= 11:
        return "Race-Supportive Phase"
    elif 12 <= week_num <= 14:
        return "Race-Specific Phase"
    elif 15 <= week_num <= 16:
        return "Taper/Race Phase"
    return ""

def shorten_phase(phase):
    return phase.replace(" Phase", "").replace(" ", "-")

def pace_at_percentage(base_pace_min_per_mile, percentage):
    """Return pace (min/mi) at a % of race pace (speed-based)."""
    return base_pace_min_per_mile * (100.0 / percentage)

def _parse_pace_value(val):
    if val is None:
        return None
    try:
        p = parse_pace_str(str(val))
        if p is not None:
            return float(p)
    except Exception:
        pass
    try:
        return float(val)
    except Exception:
        return None

def _is_easy_like_intensity(intensity_str, pct=None):
    s = (intensity_str or "").strip().lower()
    if any(k in s for k in ("easy", "recovery", "steady", "warm", "cool")):
        return True
    if "moderate" in s and all(k not in s for k in ("hard", "tempo", "threshold", "interval")):
        return True
    if pct is not None and pct <= 88.0:
        return True
    return False

def pace_for_intensity(intensity_str, pct):
    """Return pace (min/mi) for intensity, honoring optional easy-pace override."""
    resolved = resolve_intensity_pace(intensity_str)
    if resolved is not None:
        return resolved
    base = pace_override_min_per_mile or race_pace_min_per_mile
    if pace_override_min_per_mile is None and easy_pace_min_per_mile and _is_easy_like_intensity(intensity_str, pct):
        base_easy = pace_at_percentage(race_pace_min_per_mile, intensity_to_pct("easy"))
        scale = easy_pace_min_per_mile / base_easy if base_easy else 1.0
        return pace_at_percentage(race_pace_min_per_mile, pct) * scale
    return pace_at_percentage(base, pct)

def resolve_intensity_pace(intensity_str):
    """Resolve coach terminology from the current multi-anchor pace profile."""
    try:
        from webapp.pace_knowledge import normalize_pace_profile, resolve_intensity_pace as resolve
        profile = normalize_pace_profile(
            pace_anchors_min_per_mile,
            reference_pace=pace_override_min_per_mile or race_pace_min_per_mile,
            race_distance=get_race_distance(),
            easy_pace=easy_pace_min_per_mile,
        )
        return resolve(intensity_str, profile, get_race_distance())
    except Exception:
        return None

def is_effort_only_intensity(intensity_str):
    try:
        from webapp.pace_knowledge import is_effort_only_intensity as check
        return check(intensity_str)
    except Exception:
        return False

def pace_to_string(pace):
    total_seconds = max(1, int(round(float(pace) * 60)))
    mins, secs = divmod(total_seconds, 60)
    return f"{mins}:{secs:02}/mi"

def parse_duration_str(dur_str):
    """Parse 'X min' or 'Y sec' to minutes (float)."""
    if not dur_str or not isinstance(dur_str, str):
        return None
    ds = dur_str.strip().lower()
    m = re.match(r"(\d+(\.\d+)?)\s*min", ds)
    if m:
        return float(m.group(1))
    m = re.match(r"(\d+(\.\d+)?)\s*sec", ds)
    if m:
        return float(m.group(1)) / 60.0
    return None

def parse_distance_str(dist_str):
    """Return ((low_mi, high_mi), unit, (orig_low, orig_high)) or None."""
    if not dist_str or not isinstance(dist_str, str):
        return None
    ds = dist_str.strip().lower().replace('-', '–')
    pattern_km = r"(\d+(\.\d+)?)(?:–(\d+(\.\d+)?))?\s*(km|kilometers)\b"
    pattern_mi = r"(\d+(\.\d+)?)(?:–(\d+(\.\d+)?))?\s*(mi|miles)\b"
    pattern_m  = r"(\d+(\.\d+)?)(?:–(\d+(\.\d+)?))?\s*(m|meters)\b"

    m = re.match(pattern_km, ds)
    if m:
        low_val = float(m.group(1))
        high_val = float(m.group(3)) if m.group(3) else low_val
        return ((low_val * 0.621371, high_val * 0.621371), "km", (low_val, high_val))
    m = re.match(pattern_mi, ds)
    if m:
        low_val = float(m.group(1))
        high_val = float(m.group(3)) if m.group(3) else low_val
        return ((low_val, high_val), "mi", (low_val, high_val))
    m = re.match(pattern_m, ds)
    if m:
        low_val = float(m.group(1))
        high_val = float(m.group(3)) if m.group(3) else low_val
        return ((low_val / 1609.34, high_val / 1609.34), "m", (low_val, high_val))
    return None

def _range_miles(low_mi: float, high_mi: float) -> float:
    """Pick a representative miles value from a [low, high] range."""
    pref = (globals().get('range_distance_preference') or "high").strip().lower()
    if pref in ("low", "min", "lower"):
        return float(low_mi)
    if pref in ("mid", "midpoint", "avg", "average", "mean"):
        return (float(low_mi) + float(high_mi)) / 2.0
    # default to high end
    return float(high_mi)

def parse_repetitions(val, default=1):
    """Coerce repetitions to a safe integer. Supports ranges like '4–5'."""
    if val is None:
        return default
    if isinstance(val, bool):
        return default
    if isinstance(val, (int, float)):
        try:
            return max(1, int(round(float(val))))
        except Exception:
            return default
    if isinstance(val, str):
        s = val.strip().lower()
        if not s:
            return default
        s = s.replace("reps", "").replace("rep", "").replace("x", "")
        s = s.replace("–", "-").strip()
        m = re.match(r"^\s*(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?\s*$", s)
        if m:
            try:
                low = float(m.group(1))
                high = float(m.group(2)) if m.group(2) else low
                return max(1, int(round((low + high) / 2.0)))
            except Exception:
                return default
    try:
        return max(1, int(round(float(val))))
    except Exception:
        return default

def _safe_fit_filename(date_obj, name, used=None):
    base = f"{date_obj.strftime('%Y-%m-%d')}_{name}"
    s = re.sub(r"\s*\([^)]*\)", "", base)
    s = s.replace("~", "").replace(",", "")
    s = s.replace("/", "-").replace(" ", "_")
    s = re.sub(r"[^A-Za-z0-9._-]", "", s)
    s = re.sub(r"[_-]{2,}", lambda m: m.group(0)[0], s)
    s = s.strip("-_")
    stem = s[:80] or "Workout"
    if used is not None:
        count = used.get(stem, 0)
        used[stem] = count + 1
        if count > 0:
            stem = f"{stem}_{count+1}"
    return stem + ".fit"

def _export_fit_bytes(name, workout, *, targets_enabled=True, target_mode="pace", target_margin=30, pace_min_per_mile=None, include_implicit_wu_cd=False):
    import hm_plan_to_garmin as hm
    import final_spec_compliant_fix as spec

    hm.TARGETS_ENABLED = bool(targets_enabled)
    hm.TARGET_MODE = target_mode if target_mode in ("pace", "speed") else "pace"
    hm.TARGET_MARGIN_SEC = int(target_margin)
    hm.INCLUDE_IMPLICIT_WU_CD = bool(include_implicit_wu_cd)
    if pace_min_per_mile:
        hm.RACE_PACE_MIN_PER_MILE = float(pace_min_per_mile)

    steps = hm.workout_to_garmin_steps(workout)
    if not steps:
        raise ValueError("No steps generated from workout")

    with tempfile.TemporaryDirectory() as td:
        outp = os.path.join(td, "w.fit")
        spec.export_spec_compliant_fit_workout(name, steps, outp, estimated_miles=0.0)
        with open(outp, "rb") as f:
            return f.read()

_PACE_RE = re.compile(r"(\d{1,2})\s*:\s*([0-5]\d)")
_DUR_HM_RE = re.compile(r"(\d+)\s*h(?:\s*(\d+)\s*m)?", re.I)
_DUR_COLON_RE = re.compile(r"(\d+):(\d{2})(?::(\d{2}))?")
_DUR_M_RE = re.compile(r"(\d+)\s*m\b", re.I)

def _parse_pace_text(text):
    if not text:
        return None
    m = _PACE_RE.search(str(text))
    if not m:
        return None
    try:
        mins = int(m.group(1))
        secs = int(m.group(2))
        return mins + (secs / 60.0)
    except Exception:
        return None

def _parse_duration_text(text):
    if not text:
        return None
    s = str(text).lower()
    m = _DUR_HM_RE.search(s)
    if m:
        hours = int(m.group(1) or 0)
        mins = int(m.group(2) or 0)
        return float(hours * 3600 + mins * 60)
    m = _DUR_COLON_RE.search(s)
    if m:
        a = int(m.group(1) or 0)
        b = int(m.group(2) or 0)
        c = int(m.group(3) or 0)
        if m.group(3) is not None:
            return float(a * 3600 + b * 60 + c)
        return float(a * 60 + b)
    m = _DUR_M_RE.search(s)
    if m:
        mins = int(m.group(1) or 0)
        return float(mins * 60)
    return None

def _pace_from_speed(mps):
    if not mps:
        return None
    try:
        mps_val = float(mps)
    except Exception:
        return None
    if mps_val <= 0:
        return None
    return (1609.34 / mps_val) / 60.0

def _speed_from_pace(pace_min_per_mi):
    if not pace_min_per_mi:
        return None
    try:
        pace_val = float(pace_min_per_mi)
    except Exception:
        return None
    if pace_val <= 0:
        return None
    return 1609.34 / (pace_val * 60.0)

def _pace_from_step(step):
    mps_low = step.get("custom_target_speed_low")
    mps_high = step.get("custom_target_speed_high")
    mps = None
    if mps_low and mps_high:
        try:
            mps = (float(mps_low) + float(mps_high)) / 2.0
        except Exception:
            mps = None
    elif mps_low:
        mps = mps_low
    elif mps_high:
        mps = mps_high
    pace = _pace_from_speed(mps)
    if pace:
        return pace
    text = step.get("notes") or step.get("wkt_step_name") or step.get("name")
    return _parse_pace_text(text)

def _duration_type_key(val):
    if isinstance(val, (int, float)):
        try:
            if int(val) == 6:
                return "repeat_until_steps_cmplt"
        except Exception:
            pass
    if val is None:
        return ""
    try:
        if hasattr(val, "name"):
            val = val.name
    except Exception:
        pass
    return str(val).strip().lower()

def _intensity_key(val):
    key = ""
    if val is not None:
        try:
            if hasattr(val, "name"):
                val = val.name
        except Exception:
            pass
        key = str(val).strip().lower()
    if "warmup" in key:
        return "warmup"
    if "cooldown" in key:
        return "cooldown"
    if "rest" in key or "recovery" in key or "walk" in key:
        return "rest"
    return key or "active"

def _extract_workout_total_seconds(ff):
    try:
        def fields_dict(msg):
            return {f.name: f.value for f in msg.fields if getattr(f, 'value', None) is not None}
        for m in ff.get_messages('workout'):
            d = fields_dict(m)
            for key in ("total_time", "estimated_duration", "duration", "workout_duration"):
                if key in d:
                    try:
                        val = float(d[key])
                        if val > 0:
                            return val
                    except Exception:
                        pass
            name = d.get("wkt_name") or d.get("workout_name") or ""
            parsed = _parse_duration_text(name)
            if parsed:
                return parsed
    except Exception:
        return None
    return None

def _fit_graph_bytes(data):
    try:
        from fitparse import FitFile
    except Exception as e:
        return {"error": f"fitparse not installed: {e}"}
    try:
        ff = FitFile(io.BytesIO(data))
        def fields_dict(msg):
            return {f.name: f.value for f in msg.fields if getattr(f, 'value', None) is not None}
        steps = [fields_dict(m) for m in ff.get_messages('workout_step')]
        if not steps:
            return {"segments": [], "total_seconds": 0}

        total_hint = _extract_workout_total_seconds(ff)

        known = []
        for st in steps:
            pace = _pace_from_step(st)
            if pace:
                known.append(pace)
        base = sorted(known)[len(known) // 2] if known else 8.0

        def default_pace(intensity):
            if intensity in ("rest", "recovery", "walk"):
                return base + 4.0
            if intensity in ("warmup", "cooldown"):
                return base + 2.0
            return base + 1.0

        expanded = []
        for idx, st in enumerate(steps):
            dtype = _duration_type_key(st.get("duration_type"))
            if dtype.startswith("repeat"):
                start_idx = int(st.get("duration_step") or 0)
                if start_idx >= idx and idx > 0:
                    start_idx = max(0, idx - 1)
                repeat_count = int(st.get("repeat_steps") or st.get("target_repeat_steps") or st.get("target_value") or 1)
                repeat_count = max(1, repeat_count)
                if repeat_count <= 1:
                    continue
                block = []
                found = False
                for seg in reversed(expanded):
                    raw_idx = seg.get("raw_index", -1)
                    if raw_idx < start_idx or raw_idx >= idx:
                        if found:
                            break
                        continue
                    found = True
                    block.append(seg)
                block = list(reversed(block))
                if not block:
                    continue
                for _ in range(repeat_count - 1):
                    for seg in block:
                        expanded.append({"raw_index": seg.get("raw_index"), "step": seg.get("step")})
            else:
                expanded.append({"raw_index": idx, "step": st})

        segments = []
        total = 0.0
        unknown_idxs = []
        rest_durations = []
        for entry in expanded:
            st = entry.get("step") or {}
            dtype = _duration_type_key(st.get("duration_type"))
            intensity = _intensity_key(st.get("intensity"))
            pace = _pace_from_step(st)
            if pace is None:
                pace = default_pace(intensity)
            duration = 0.0
            if dtype == "time":
                duration = float(st.get("duration_time") or 0)
            elif dtype == "distance":
                dist = float(st.get("duration_distance") or 0)
                mps = _speed_from_pace(pace) or 0.0
                duration = dist / mps if mps > 0 else 0.0
            else:
                if st.get("duration_time"):
                    duration = float(st.get("duration_time") or 0)
                elif st.get("duration_distance"):
                    dist = float(st.get("duration_distance") or 0)
                    mps = _speed_from_pace(pace) or 0.0
                    duration = dist / mps if mps > 0 else 0.0
            if duration <= 0:
                segments.append({
                    "duration_s": 0.0,
                    "pace_min_per_mi": round(pace, 3) if pace else None,
                    "intensity": intensity,
                    "label": st.get("wkt_step_name") or st.get("notes") or st.get("name") or intensity,
                    "duration_inferred": True,
                })
                unknown_idxs.append(len(segments) - 1)
                continue
            segments.append({
                "duration_s": round(duration, 2),
                "pace_min_per_mi": round(pace, 3) if pace else None,
                "intensity": intensity,
                "label": st.get("wkt_step_name") or st.get("notes") or st.get("name") or intensity
            })
            total += duration
            if intensity == "rest":
                rest_durations.append(duration)

        inferred_total = 0.0
        if unknown_idxs:
            default_rest = min(rest_durations) if rest_durations else 90.0
            default_other = 60.0
            for idx in unknown_idxs:
                intensity = segments[idx].get("intensity")
                dur = default_rest if intensity == "rest" else default_other
                segments[idx]["duration_s"] = round(dur, 2)
                segments[idx]["duration_inferred"] = True
                inferred_total += dur
            total += inferred_total

        if total_hint and unknown_idxs:
            hint_val = float(total_hint)
            if hint_val > 0:
                diff = hint_val - total
                if diff > 0 and diff / hint_val <= 0.25:
                    share = diff / max(1, len(unknown_idxs))
                    inferred_total += diff
                    total += diff
                    for idx in unknown_idxs:
                        segments[idx]["duration_s"] = round(segments[idx]["duration_s"] + share, 2)

        return {
            "segments": segments,
            "total_seconds": round(total, 2),
            "inferred_seconds": round(inferred_total, 2),
            "total_hint_seconds": round(total_hint, 2) if total_hint else None,
        }
    except Exception as e:
        return {"error": str(e)}

def format_distance_in_original_unit(low_mi, high_mi, unit, original_vals):
    (orig_low, orig_high) = original_vals
    if unit in ["km", "kilometers"]:
        orig_low_mi = orig_low * 0.621371
        orig_high_mi = orig_high * 0.621371
    elif unit in ["mi", "miles"]:
        orig_low_mi = orig_low
        orig_high_mi = orig_high
    else:
        orig_low_mi = orig_low / 1609.34
        orig_high_mi = orig_high / 1609.34
    base_mi = orig_low_mi if orig_low_mi > 0 else orig_high_mi
    scale_factor = low_mi / base_mi if base_mi > 0 else 1.0
    new_low = orig_low * scale_factor
    new_high = orig_high * scale_factor
    if unit in ["km", "kilometers"]:
        if abs(new_low - new_high) < 0.01:
            return f"{round(new_low,1)} km"
        else:
            return f"{round(new_low,1)}–{round(new_high,1)} km"
    elif unit in ["mi", "miles"]:
        if abs(new_low - new_high) < 0.01:
            return f"{round(new_low,1)} mi"
        else:
            return f"{round(new_low,1)}–{round(new_high,1)} mi"
    elif unit in ["m", "meters"]:
        nl = round(new_low)
        nh = round(new_high)
        return f"{nl} m" if nl == nh else f"{nl}–{nh} m"
    return ""

def intensity_to_pct(intensity_str):
    """
    Parse intensity like '98–100% of race pace' or labels like 'Easy', 'Very Easy'.
    Returns a single percentage (averages a range).

    Priority:
      1. If explicit % values are present, use them.
      2. Otherwise, map descriptive labels to the guide values.
    """
    if not intensity_str:
        return 100.0

    norm = intensity_str.strip().lower()

    # Existing % of race-pace parsing (will match numbers inside strings like "≤60% of HMP")
    norm_for_nums = norm.replace('-', '–')
    pattern = rf"(\d+(\.\d+)?)(?:–(\d+(\.\d+)?))?\s*%\s*of\s*({_pace_label_pattern()})"
    m = re.search(pattern, norm_for_nums)
    if m:
        low = float(m.group(1))
        if m.group(3):
            high = float(m.group(3))
            avg = (low + high) / 2.0
        else:
            avg = low
        label = m.group(5) or ""
        return _pct_from_label(avg, label)

    # Bare percentages without labels: assume % of race pace
    if not re.search(_pace_label_pattern(), norm):
        m = re.search(r"(\d+(\.\d+)?)(?:–(\d+(\.\d+)?))?\s*%", norm_for_nums)
        if m:
            low = float(m.group(1))
            if m.group(3):
                high = float(m.group(3))
                return (low + high) / 2.0
            return low

    # Descriptive label mappings (based on provided guide) -> convert from 10k-anchored % to race %
    def pct10k_to_race(pct10k: float) -> float:
        return _pct_from_label(pct10k, "10k pace")

    if "very easy" in norm or "recovery" in norm or "shakeout" in norm:
        return pct10k_to_race(70.0)
    if "easy to moderate" in norm:
        return pct10k_to_race(79.0)
    if "moderate" in norm and "easy" not in norm:
        return pct10k_to_race(83.0)
    if "easy" in norm and "very easy" not in norm:
        return pct10k_to_race(75.0)
    if "steady" in norm or "lt1" in norm:
        return pct10k_to_race(87.0)
    if "strong" in norm or "marathon pace" in norm or "predicted marathon pace" in norm:
        return pct10k_to_race(90.0)
    if "sub-threshold" in norm or "sub threshold" in norm:
        return pct10k_to_race(92.0)
    if "half marathon" in norm or "hm pace" in norm:
        return pct10k_to_race(95.0)
    if "threshold" in norm or "lt2" in norm or "t pace" in norm or "ssmax" in norm:
        return pct10k_to_race(96.5)
    if "10k" in norm and "pace" in norm:
        return pct10k_to_race(100.0)
    if "8k" in norm:
        return pct10k_to_race(102.0)
    if "5k" in norm:
        return pct10k_to_race(105.0)
    if "vvo2" in norm or "i pace" in norm:
        return pct10k_to_race(108.0)
    if "3k" in norm:
        return pct10k_to_race(110.0)
    if ("mile" in norm and "pace" in norm) or "r pace" in norm:
        return pct10k_to_race(115.0)

    # default
    return 100.0


def is_rest_intensity(intensity_str):
    """Return True when intensity text indicates complete rest/walk break."""
    if not intensity_str or not isinstance(intensity_str, str):
        return False
    norm = intensity_str.strip().lower()
    return ('rest' in norm) or ('walk' in norm)

def pace_ratio_for_intensity(intensity_str):
    """Return user:reference pace ratio for a given intensity.
    If reference pace is unknown, return 1.0.
    """
    try:
        if not intensity_str or reference_hmp_min_per_mile is None:
            return 1.0
        pct = intensity_to_pct(intensity_str)
        # Map intensity to pace (min/mi) for user vs reference
        p_user = pace_at_percentage(race_pace_min_per_mile, pct)
        p_ref  = pace_at_percentage(reference_hmp_min_per_mile, pct)
        if p_ref <= 0:
            return 1.0
        return p_user / p_ref
    except Exception:
        return 1.0

def get_effective_scale_factor(workout_type, intensity_str=None):
    """Choose easy vs workout scaling factor based on type.
    Easy-like: easy, very easy, steady, easy to moderate -> easy_scale_factor
    Everything else -> workout_scale_factor
    """
    wt = (workout_type or '').lower()
    if wt in ['easy','very easy','steady','easy to moderate']:
        return easy_scale_factor
    return workout_scale_factor

def ensure_of_hmp(intensity):
    """
    Normalize intensity strings for descriptions:
    - Replace 'easy' with '80% of race pace'
    - Replace 'very easy' or 'recovery' with '75% of race pace'
    - If a % is missing a pace label, append the current race pace label
    """
    if not intensity:
        return intensity

    norm = intensity.strip().lower()
    pace_label = race_pace_label()

    # Calendar descriptions should show the same final pace that FIT targets
    # use.  Preserve an existing explicit pace and otherwise annotate any
    # terminology the multi-anchor resolver understands.
    if parse_pace_str(intensity) is None:
        resolved = resolve_intensity_pace(intensity)
        if resolved is not None and not is_effort_only_intensity(intensity):
            return f"{intensity.strip()} (~{pace_to_string(resolved)})"

    if "very easy" in norm or "recovery" in norm:
        pct = intensity_to_pct(intensity)
        return f"Very easy ({pct:.0f}% of {pace_label})"
    if "easy" in norm:
        pct = intensity_to_pct(intensity)
        return f"Easy ({pct:.0f}% of {pace_label})"

    if not re.search(_pace_label_pattern(), norm):
        if re.match(r"^\d+(\.\d+)?(–\d+(\.\d+)?)?%$", intensity.strip()):
            return intensity.strip() + f" of {pace_label}"

    return intensity

def add_paces_to_string(desc, base_pace_hmp):
    """
    Annotate any '% of race pace' occurrence with (~mm:ss/mi).
    e.g., '100% of HMP' -> '100% of HMP (~7:10/mi)'
    """
    if "(Original plan:" in desc:
        parts = desc.split("(Original plan:")
        scaled = parts[0].strip()
        original = "(Original plan:" + "(Original plan:".join(parts[1:]).strip()
    else:
        scaled = desc
        original = ""
    
    pace_label = race_pace_label()

    def ensure(text):
        # Handle both regular dash and Unicode en-dash
        regex = rf"(\d+(?:\.\d+)?(?:[–-]\d+(?:\.\d+)?)?)%\b(?!\s*of\s*{_pace_label_pattern()})"
        return re.sub(regex, rf"\1% of {pace_label}", text, flags=re.IGNORECASE)
    
    scaled = ensure(scaled)
    
    def repl(m):
        pct_text = m.group(1)
        label_text = m.group(2)
        # Handle both regular dash and Unicode en-dash
        if "–" in pct_text or "-" in pct_text:
            separator = "–" if "–" in pct_text else "-"
            low, high = pct_text.split(separator)
            avg = (float(low) + float(high)) / 2.0
        else:
            avg = float(pct_text)
        pace = pace_to_string(pace_at_percentage(base_pace_hmp, avg))
        disp_label = _format_pace_label(label_text) or pace_label
        return f"{pct_text}% of {disp_label} (~{pace})"
    
    # Handle both regular dash and Unicode en-dash in pattern
    pattern = rf"(\d+(?:\.\d+)?(?:[–-]\d+(?:\.\d+)?)?)%\s*of\s*({_pace_label_pattern()})"
    scaled = re.sub(pattern, repl, scaled, flags=re.IGNORECASE)
    
    return (scaled + "\n" + original).strip()

def parse_pace_str(s):
    """
    Parse explicit pace strings like '9:30/mi', '9:30 min/mi', '9:30 per mile'
    and return minutes-per-mile as float. Returns None if no explicit pace found.
    """
    if not s or not isinstance(s, str):
        return None
    ss = s.strip().lower()
    # look for mm:ss/mi or mm:ss per mile patterns
    m = re.search(r"(\d+):(\d{1,2})\s*(?:/mi|per mile|min/mi|min per mile)", ss)
    if m:
        mins = int(m.group(1))
        secs = int(m.group(2))
        return mins + secs / 60.0
    # allow formats like '9.5 min/mi' - but require the /mi or per mile part
    m = re.search(r"(\d+(\.\d+)?)\s*min\s*(?:/mi|per mile)", ss)
    if m:
        return float(m.group(1))
    return None

# -------------------------------
# Mileage computation

def compute_distance_from_time(dur_min, pct_of_hmp, intensity_str=None):
    """dur_min in minutes -> miles at the given % of race pace.
    Respects a temporary pace override if set for cross-pace calculations.
    """
    pace = pace_for_intensity(intensity_str, pct_of_hmp)  # min/mi
    return dur_min / pace

def compute_sets(sets):
    """
    Supports two forms inside each set:
      1) legacy: {repetitions, distance OR duration, intensity, recovery?}
      2) new:    {repetitions, sequence: [ {distance/duration, intensity, recovery?}, ... ]}
    """
    miles = 0.0
    for s in sets:
        reps = parse_repetitions(s.get('repetitions', 1))
        if 'sequence' in s:
            seq = s['sequence']
            for _ in range(reps):
                for step in seq:
                    step_reps = parse_repetitions(step.get('repetitions', 1))
                    for _ in range(step_reps):
                        ds = step.get('distance','') or step.get('duration','')
                        intensity = step.get('intensity','')
                        if not is_rest_intensity(intensity):
                            pct = intensity_to_pct(intensity)
                            dmin = parse_duration_str(ds)
                            if dmin is not None:
                                miles += compute_distance_from_time(dmin, pct, intensity)
                            else:
                                parsed = parse_distance_str(ds)
                                if parsed:
                                    ((l, h), u, o) = parsed
                                    miles += _range_miles(l, h)
                        # optional recovery after each rep
                        rec = step.get('recovery',{})
                        if rec and rec.get('type','').lower() == "jog":
                            rmin = parse_duration_str(rec.get('duration',''))
                            if rmin is not None:
                                miles += compute_distance_from_time(rmin, 60.0, "recovery")
                            else:
                                rds = rec.get('distance','')
                                rparsed = parse_distance_str(rds)
                                if rparsed:
                                    ((rl, rh), uu, oo) = rparsed
                                    miles += _range_miles(rl, rh)
        else:
            ds = s.get('distance','') or s.get('duration','')
            intensity = s.get('intensity','')
            if is_rest_intensity(intensity):
                continue
            pct = intensity_to_pct(intensity)
            dmin = parse_duration_str(ds)
            if dmin is not None:
                miles += compute_distance_from_time(dmin, pct, intensity) * reps
            else:
                parsed = parse_distance_str(ds)
                if parsed:
                    ((l, h), u, o) = parsed
                    miles += _range_miles(l, h) * reps
            rec = s.get('recovery',{})
            if rec and rec.get('type','').lower() == "jog":
                rmin = parse_duration_str(rec.get('duration',''))
                if rmin is not None:
                    miles += compute_distance_from_time(rmin, 60.0, "recovery") * reps
                else:
                    rds = rec.get('distance','')
                    rparsed = parse_distance_str(rds)
                    if rparsed:
                        ((rl, rh), uu, oo) = rparsed
                        miles += _range_miles(rl, rh) * reps
    return miles

def compute_segments(segments):
    mi = 0.0
    for seg in segments:
        if 'sets' in seg:
            mi += compute_sets(seg['sets'])
        else:
            reps = parse_repetitions(seg.get('repetitions', 1))
            ds = seg.get('distance','') or seg.get('duration','')
            intensity = seg.get('intensity','')
            if is_rest_intensity(intensity):
                continue
            pct = intensity_to_pct(intensity)
            dmin = parse_duration_str(ds)
            if dmin is not None:
                mi += compute_distance_from_time(dmin, pct, intensity) * reps
            else:
                parsed = parse_distance_str(ds)
                if parsed:
                    ((l, h), u, o) = parsed
                    mi += _range_miles(l, h) * reps
            rec = seg.get('recovery',{})
            if rec and rec.get('type','').lower() == "jog":
                rmin = parse_duration_str(rec.get('duration',''))
                if rmin is not None:
                    mi += compute_distance_from_time(rmin, 60.0, "recovery") * reps
                else:
                    rds = rec.get('distance','')
                    rparsed = parse_distance_str(rds)
                    if rparsed:
                        ((rl, rh), uu, oo) = rparsed
                        mi += _range_miles(rl, rh) * reps
    return mi

def compute_obj_miles(obj):
    """Compute miles for a workout object; adds +1.0 mi for workout types."""
    wt = (obj.get('type','') or '').lower()
    if 'segments' in obj:
        mi = compute_segments(obj['segments'])
        if include_implicit_wu_cd and is_wu_cd_workout(obj) and not _has_explicit_wu_cd(obj):
            mi += _implicit_wu_cd_miles_time()[0]
        return mi
    if 'sets' in obj:
        mi = compute_sets(obj['sets'])
        if include_implicit_wu_cd and is_wu_cd_workout(obj) and not _has_explicit_wu_cd(obj):
            mi += _implicit_wu_cd_miles_time()[0]
        return mi
    # distance or duration only
    ds = obj.get('distance','') or obj.get('duration','')
    intensity = obj.get('intensity','')
    if is_rest_intensity(intensity):
        return 0.0
    pct = intensity_to_pct(intensity)
    reps = parse_repetitions(obj.get('repetitions', 1))
    dmin = parse_duration_str(ds)
    mi = 0.0
    if dmin is not None:
        mi = compute_distance_from_time(dmin, pct, intensity) * reps
    else:
        parsed = parse_distance_str(ds)
        if parsed:
            ((l, h), u, o) = parsed
            mi = _range_miles(l, h) * reps
    if include_implicit_wu_cd and is_wu_cd_workout(obj) and not _has_explicit_wu_cd(obj):
        mi += _implicit_wu_cd_miles_time()[0]
    return mi

def compute_day_mileage(workout):
    if isinstance(workout, dict):
        if 'am' in workout or 'pm' in workout:
            am = workout.get('am')
            pm = workout.get('pm')
            return (compute_obj_miles(am) if am else 0.0) + (compute_obj_miles(pm) if pm else 0.0)
        else:
            return compute_obj_miles(workout)
    elif isinstance(workout, list):
        return sum(compute_day_mileage(w) for w in workout)
    return 0.0

def compute_day_estimated_time_mins(workout):
    """
    Compute estimated total time (minutes) for a workout object.
    Uses intensities to pick pace (via pace_at_percentage) and:
      - For distance entries: minutes = miles * pace(min/mi)
      - For duration entries: minutes = given duration
      - For recovery jogs: use 60% of race pace for pace if distance given, or use duration
      - Adds 9:15 (9.25 min) for warmup/cooldown on appropriate workout types / segments
    Accepts a scaled workout object (distances already scaled if you want scaled times).
    """
    wu_cd_pace = WU_CD_PACE_MIN_PER_MILE  # minutes per mile for warmup/cooldown
    
    def parse_complex_long_run_intensity(intensity_str, total_miles):
        """
        Parse complex long run intensities like:
        - "Last 30 min @ 92–95% of race pace"
        - "Include last 20–25 min @ 88–92% of race pace"
        - "Easy→Moderate (70–80% of race pace)"
        Returns (easy_miles, fast_miles, fast_pace_min_per_mi)
        """
        if not intensity_str or not isinstance(intensity_str, str):
            # Default to easy pace for entire run
            easy_pace = pace_at_percentage(race_pace_min_per_mile, 80.0)
            return total_miles, 0.0, easy_pace
        
        intensity_lower = intensity_str.lower()
        
        # Check for "last X min @ Y% of race pace" patterns (handle both – and - dashes)
        # Also handle "Include last X min" patterns
        last_time_match = re.search(r"(?:include\s+)?last\s+(\d+(?:[–-]\d+)?)\s*min\s*@\s*(\d+(?:[–-]\d+)?)\s*%", intensity_lower)
        if last_time_match:
            time_str = last_time_match.group(1)
            pct_str = last_time_match.group(2)
            
            # Parse time (take average if range)
            if "–" in time_str or "-" in time_str:
                separator = "–" if "–" in time_str else "-"
                low_time, high_time = map(int, time_str.split(separator))
                fast_time_min = (low_time + high_time) / 2.0
            else:
                fast_time_min = float(time_str)
            
            # Parse percentage (take average if range)
            if "–" in pct_str or "-" in pct_str:
                separator = "–" if "–" in pct_str else "-"
                low_pct, high_pct = map(int, pct_str.split(separator))
                fast_pct = (low_pct + high_pct) / 2.0
            else:
                fast_pct = float(pct_str)
            
            # Calculate paces
            fast_pace = pace_at_percentage(race_pace_min_per_mile, fast_pct)
            easy_pace = pace_at_percentage(race_pace_min_per_mile, 80.0)  # Assume easy for the rest
            
            # Calculate distances
            fast_miles = fast_time_min / fast_pace
            easy_miles = max(0.0, total_miles - fast_miles)
            
            return easy_miles, fast_miles, fast_pace
        
        # Check for simple percentage ranges like "Easy→Moderate (70–80% of race pace)"
        pct_range_match = re.search(r"(\d+)(?:–(\d+))?\s*%", intensity_lower)
        if pct_range_match:
            low_pct = float(pct_range_match.group(1))
            high_pct = float(pct_range_match.group(2)) if pct_range_match.group(2) else low_pct
            avg_pct = (low_pct + high_pct) / 2.0
            pace = pace_for_intensity(intensity_str, avg_pct)
            return total_miles, 0.0, pace
        
        # Default to easy pace
        easy_pace = pace_for_intensity("easy", 80.0)
        return total_miles, 0.0, easy_pace
    
    def obj_time(o):
        mins = 0.0
        wt = (o.get('type','') or '').lower() if isinstance(o, dict) else ''
        # handle AM/PM containers (e.g., special block or doubles)
        if isinstance(o, dict) and ('am' in o or 'pm' in o):
            if 'am' in o and isinstance(o['am'], (dict, list)):
                mins += obj_time(o['am'])
            if 'pm' in o and isinstance(o['pm'], (dict, list)):
                mins += obj_time(o['pm'])
            return mins
        # segments
        if isinstance(o, dict) and 'segments' in o:
            for seg in o['segments']:
                if 'sets' in seg:
                    mins += sets_time(seg['sets'])
                else:
                    reps = parse_repetitions(seg.get('repetitions', 1))
                    ds = seg.get('distance','') or seg.get('duration','')
                    intensity = seg.get('intensity','')
                    # allow explicit pace like '9:30/mi' in intensity
                    p = parse_pace_str(intensity)
                    dmin = parse_duration_str(ds)
                    if dmin is not None:
                        mins += dmin * reps
                    else:
                        parsed = parse_distance_str(ds)
                        if parsed:
                            ((l, h), u, opar) = parsed
                            miles = _range_miles(l, h) * reps
                            if p is not None:
                                pace = p
                            else:
                                pct = intensity_to_pct(intensity)
                                pace = pace_for_intensity(intensity, pct)
                            mins += miles * pace
                    rec = seg.get('recovery',{})
                    if rec and rec.get('type','').lower() == "jog":
                        rmin = parse_duration_str(rec.get('duration',''))
                        if rmin is not None:
                            mins += rmin * reps
                        else:
                            rds = rec.get('distance','')
                            rparsed = parse_distance_str(rds)
                            if rparsed:
                                ((rl, rh), uu, oo) = rparsed
                                rmiles = _range_miles(rl, rh) * reps
                                rpace = pace_for_intensity("recovery", 60.0)
                                mins += rmiles * rpace
            # segments may include implicit WU/CD in original mileage logic; optionally add ~1 mi WU/CD time
            if include_implicit_wu_cd and is_wu_cd_workout(o) and not _has_explicit_wu_cd(o):
                mins += _implicit_wu_cd_miles_time()[1]
            return mins

        # sets (top-level)
        if isinstance(o, dict) and 'sets' in o:
            mins += sets_time(o['sets'])
            # add warmup/cooldown for certain workout types
            if include_implicit_wu_cd and is_wu_cd_workout(o) and not _has_explicit_wu_cd(o):
                mins += _implicit_wu_cd_miles_time()[1]
            return mins

        # simple distance/duration
        if isinstance(o, dict):
            ds = o.get('distance','') or o.get('duration','')
            intensity = o.get('intensity','')
            reps = parse_repetitions(o.get('repetitions', 1))
            p = parse_pace_str(intensity)
            dmin = parse_duration_str(ds)
            if dmin is not None:
                mins += dmin * reps
            else:
                parsed = parse_distance_str(ds)
                if parsed:
                    ((l, h), u, opar) = parsed
                    miles = _range_miles(l, h) * reps
                    if p is not None:
                        pace = p
                    else:
                        # Special handling for long runs with complex intensity descriptions
                        if wt in ['long run', 'long'] and intensity:
                            easy_miles, fast_miles, fast_pace = parse_complex_long_run_intensity(intensity, miles)
                            if fast_miles > 0:
                                easy_pace = pace_for_intensity("easy", 80.0)
                                workout_time = easy_miles * easy_pace + fast_miles * fast_pace
                                mins += workout_time
                            else:
                                # Use the returned pace for the entire run
                                mins += miles * fast_pace
                        else:
                            pct = intensity_to_pct(intensity)
                            pace = pace_for_intensity(intensity, pct)
                            mins += miles * pace
            if include_implicit_wu_cd and is_wu_cd_workout(o) and not _has_explicit_wu_cd(o):
                mins += _implicit_wu_cd_miles_time()[1]
            return mins

        # lists
        if isinstance(o, list):
            for item in o:
                mins += obj_time(item)
            return mins

        return mins

    def sets_time(sets):
        total = 0.0
        for s in sets:
            reps = parse_repetitions(s.get('repetitions', 1))
            if 'sequence' in s:
                seq = s['sequence']
                for _ in range(reps):
                    for step in seq:
                        step_reps = parse_repetitions(step.get('repetitions', 1))
                        for _ in range(step_reps):
                            ds = step.get('distance','') or step.get('duration','')
                            intensity = step.get('intensity','')
                            p = parse_pace_str(intensity)
                            dmin = parse_duration_str(ds)
                            if dmin is not None:
                                total += dmin
                            else:
                                parsed = parse_distance_str(ds)
                                if parsed:
                                    ((l, h), u, opar) = parsed
                                    miles = _range_miles(l, h)
                                    if p is not None:
                                        pace = p
                                    else:
                                        pct = intensity_to_pct(intensity)
                                        pace = pace_for_intensity(intensity, pct)
                                    total += miles * pace
                            # optional recovery for each rep
                            rec = step.get('recovery',{})
                            if rec and rec.get('type','').lower() == "jog":
                                rmin = parse_duration_str(rec.get('duration',''))
                                if rmin is not None:
                                    total += rmin
                                else:
                                    rds = rec.get('distance','')
                                    rparsed = parse_distance_str(rds)
                                    if rparsed:
                                        ((rl, rh), uu, oo) = rparsed
                                        rmiles = _range_miles(rl, rh)
                                        rpace = pace_for_intensity("recovery", 60.0)
                                        total += rmiles * rpace
            else:
                ds = s.get('distance','') or s.get('duration','')
                intensity = s.get('intensity','')
                p = parse_pace_str(intensity)
                dmin = parse_duration_str(ds)
                if dmin is not None:
                    total += dmin * reps
                else:
                    parsed = parse_distance_str(ds)
                    if parsed:
                        ((l, h), u, opar) = parsed
                        miles = _range_miles(l, h) * reps
                        if p is not None:
                            pace = p
                        else:
                            pct = intensity_to_pct(intensity)
                            pace = pace_for_intensity(intensity, pct)
                        total += miles * pace
                rec = s.get('recovery',{})
                if rec and rec.get('type','').lower() == "jog":
                    rmin = parse_duration_str(rec.get('duration',''))
                    if rmin is not None:
                        total += rmin * reps
                    else:
                        rds = rec.get('distance','')
                        rparsed = parse_distance_str(rds)
                        if rparsed:
                            ((rl, rh), uu, oo) = rparsed
                            rmiles = _range_miles(rl, rh) * reps
                            rpace = pace_for_intensity("recovery", 60.0)
                            total += rmiles * rpace
        return total

    return obj_time(workout)

# -------------------------------
# Descriptions

def describe_set(st):
    reps = parse_repetitions(st.get('repetitions', 1))
    if 'sequence' in st:
        parts = []
        for step in st['sequence']:
            ds = step.get('distance','') or step.get('duration','')
            intensity = ensure_of_hmp(step.get('intensity','').strip())
            rec = step.get('recovery',{})
            rec_str = ""
            if rec:
                if 'duration' in rec: rec_str += f" w/ {rec['duration']}"
                if 'distance' in rec: rec_str += f" w/ {rec['distance']}"
                if 'type' in rec:     rec_str += f" {rec['type']}"
            parts.append(f"{ds} {intensity}{rec_str}".strip())
        return f"{reps}× (" + " + ".join(parts) + ")"
    else:
        ds = st.get('distance','') or st.get('duration','')
        intensity = ensure_of_hmp(st.get('intensity','').strip())
        rec = st.get('recovery',{})
        rec_str = ""
        if rec:
            if 'duration' in rec: rec_str += f" w/ {rec['duration']}"
            if 'distance' in rec: rec_str += f" w/ {rec['distance']}"
            if 'type' in rec:     rec_str += f" {rec['type']}"
        return f"{reps}x {ds} {intensity}{rec_str}".strip()

def describe_sets(sets):
    return " | ".join(describe_set(s) for s in sets)

def describe_segments(segments):
    segs = []
    for seg in segments:
        if 'sets' in seg:
            segs.append(describe_sets(seg['sets']))
        else:
            reps = parse_repetitions(seg.get('repetitions', 1))
            ds = seg.get('distance','') or seg.get('duration','')
            intensity = ensure_of_hmp(seg.get('intensity','').strip())
            rec = seg.get('recovery',{})
            rec_str = ""
            if rec:
                if 'duration' in rec: rec_str += f" w/ {rec['duration']}"
                if 'distance' in rec: rec_str += f" w/ {rec['distance']}"
                if 'type' in rec:     rec_str += f" {rec['type']}"
            segs.append(f"{reps}x {ds} {intensity}{rec_str}".strip())
    return " + ".join(segs)

def workout_to_string_original(workout):
    if isinstance(workout, dict):
        wt = workout.get('type','')
        if wt.lower() == 'race':
            return f"Race {race_distance_display()}"
        if 'am' in workout or 'pm' in workout:
            parts = []
            if 'am' in workout:
                am = workout['am']
                if 'segments' in am:
                    parts.append("AM: " + f"{am.get('type','').capitalize()}: {describe_segments(am['segments'])}")
                elif 'sets' in am:
                    parts.append("AM: " + f"{am.get('type','').capitalize()}: {describe_sets(am['sets'])}")
                else:
                    ds = am.get('distance','') or am.get('duration','')
                    intensity = ensure_of_hmp(am.get('intensity','').strip())
                    reps = am.get('repetitions', 1)
                    parts.append(f"AM: {am.get('type','')} {reps}x {ds} {intensity}".strip())
            if 'pm' in workout:
                pm = workout['pm']
                if 'segments' in pm:
                    parts.append("PM: " + f"{pm.get('type','').capitalize()}: {describe_segments(pm['segments'])}")
                elif 'sets' in pm:
                    parts.append("PM: " + f"{pm.get('type','').capitalize()}: {describe_sets(pm['sets'])}")
                else:
                    ds = pm.get('distance','') or pm.get('duration','')
                    intensity = ensure_of_hmp(pm.get('intensity','').strip())
                    reps = pm.get('repetitions', 1)
                    parts.append(f"PM: {pm.get('type','')} {reps}x {ds} {intensity}".strip())
            if wt.lower() == 'special block':
                return "Special block: " + " | ".join(parts)
            return " | ".join(parts)
        else:
            if 'sets' in workout:
                return f"{wt.capitalize()}: {describe_sets(workout['sets'])}"
            elif 'segments' in workout:
                return f"{wt.capitalize()}: {describe_segments(workout['segments'])}"
            else:
                ds = workout.get('distance','') or workout.get('duration','')
                intensity = ensure_of_hmp(workout.get('intensity','').strip())
                reps = workout.get('repetitions', 1)
                return f"{wt} {reps}x {ds} {intensity}".strip()
    elif isinstance(workout, list):
        return " + ".join(workout_to_string_original(w) for w in workout)
    return str(workout)


# --- Scaling utilities (comprehensive implementation from 10k script) ---

def scale_distance_entry(ds, factor):
    parsed = parse_distance_str(ds)
    if parsed:
        ((l, h), unit, orig) = parsed
        return format_distance_in_original_unit(l * factor, h * factor, unit, orig)
    return ds

def scale_time_based_keep_duration(ds):
    """For time-based intervals, keep duration but scale repetitions instead."""
    return ds

def scale_for_run_type(workout_type, ds, reps, factor, intensity_str=None, in_sequence=False):
    """
    Intelligent scaling based on workout type and content.
    - Distance-based: scale distance
    - Time-based: scale repetitions for intervals; keep duration for progression runs; scale duration for easy/steady runs
    - Apply phase-specific minimums for race-specific workouts
    """
    reps = parse_repetitions(reps)
    dmin = parse_duration_str(ds)
    parsed = parse_distance_str(ds)
    wt = workout_type.lower()
    eff_factor = factor if factor is not None else get_effective_scale_factor(workout_type, intensity_str)
    
    if dmin is not None:
        # Time-based workout
        # Scale duration for easy/steady style runs directly by factor
        if wt in ['easy', 'very easy', 'steady', 'easy to moderate']:
            # Scale by mileage factor and compensate upwards for slower paces only.
            # Faster athletes do not get time reduced here; weekly normalization handles balance.
            pace_mult = pace_ratio_for_intensity(intensity_str or f"Easy (80% of {race_pace_label()})")
            pace_mult = max(1.0, pace_mult)
            new_min = max(1.0, dmin * eff_factor * pace_mult)
            return f"{int(round(new_min))} min", reps
        # Do not scale time for races/tune-up races embedded as time intervals
        if intensity_str:
            norm_i = intensity_str.strip().lower()
            if (('race' in norm_i) and (('tune' in norm_i) or ('competition' in norm_i) or ('test' in norm_i))):
                return ds, reps
        if wt in ['interval', 'tempo'] and reps > 1:
            # Scale repetitions for time-based intervals
            new_reps = max(1, round(reps * eff_factor))
            return ds, new_reps
        elif wt in ['progression run', 'progression']:
            # For progression runs, keep the duration but don't scale repetitions
            # The progression structure should remain intact
            return ds, reps
        else:
            # Optionally scale explicit warm-up/cool-down (very easy) segments
            if scale_wu_cd_segments and intensity_str:
                norm = intensity_str.strip().lower()
                if ('very easy' in norm or 'warm' in norm or 'cool' in norm) and wt in ['interval','tempo','special block','kenyan-style progression run']:
                    new_min = max(8.0, min(30.0, dmin * eff_factor))
                    return f"{int(round(new_min))} min", reps
            # Keep duration for easy runs and single tempo runs
            return ds, reps
    
    if not parsed:
        return ds, reps

    # When scaling nested sequence entries that repeat a shorter rep many times,
    # prefer to adjust how many reps execute instead of shrinking the distance
    # of each rep. This keeps key workout prescriptions (e.g., 500 m reps) intact
    # while honoring the user scale factor.
    if in_sequence and reps > 1:
        new_reps = max(1, round(reps * eff_factor))
        return ds, new_reps

    # Distance-based workout
    ((l, h), u, o) = parsed
    # Skip scaling for races/tune-up races embedded in interval sets
    if intensity_str:
        norm_i = intensity_str.strip().lower()
        if (('race' in norm_i) and (('tune' in norm_i) or ('competition' in norm_i) or ('test' in norm_i))):
            new_ds = ds
        else:
            new_ds = scale_distance_entry(ds, eff_factor)
    else:
        new_ds = scale_distance_entry(ds, eff_factor)
    
    # Enforce minimum total distance in Race-Specific phase for race pace work
    # Do NOT apply this to individual entries within a sequence; only apply at the set/segment level
    if CURRENT_PHASE and "Race-Specific" in CURRENT_PHASE and intensity_str and not in_sequence:
        intensity_pct = intensity_to_pct(intensity_str)
        if 98 <= intensity_pct <= 102:  # Race pace work
            parsed_new = parse_distance_str(new_ds)
            if parsed_new:
                ((new_low, new_high), unit, orig_vals) = parsed_new
                total = new_low * reps
                race_mi = race_distance_miles()
                min_total_mi = max(1.5, race_mi * 0.45)
                if unit in ["mi", "miles"]:
                    min_total = min_total_mi
                elif unit in ["km", "kilometers"]:
                    min_total = min_total_mi * 1.60934
                else:
                    min_total = min_total_mi
                if total < min_total:
                    new_per = min_total / reps
                    new_ds = f"{new_per:.1f} {unit}"
    
    return new_ds, reps

def scale_sets(sets_list, workout_type):
    new_sets = []
    eff_factor = get_effective_scale_factor(workout_type)
    for s in sets_list:
        s_copy = copy.deepcopy(s)
        reps = parse_repetitions(s_copy.get('repetitions', 1))

        if 'sequence' in s_copy:
            # Sequence-based sets (e.g., 2k + 1k): scale the repetitions of the
            # sequence only. Keep individual step distances/durations unchanged
            # to avoid double-scaling.
            new_reps = max(1, round(reps * eff_factor))
            s_copy['repetitions'] = new_reps
            scaled_sequence = []
            for step in s_copy.get('sequence', []):
                step_copy = copy.deepcopy(step)
                original_has_reps = 'repetitions' in step
                step_reps = parse_repetitions(step_copy.get('repetitions', 1))
                ds = step_copy.get('distance', '') or step_copy.get('duration', '')
                intensity = step_copy.get('intensity', '')

                if ds and (original_has_reps or step_reps > 1):
                    new_ds, new_step_reps = scale_for_run_type(
                        workout_type,
                        ds,
                        step_reps,
                        eff_factor,
                        intensity,
                        in_sequence=True
                    )
                    if 'distance' in step_copy:
                        step_copy['distance'] = new_ds
                    elif 'duration' in step_copy:
                        step_copy['duration'] = new_ds
                    if original_has_reps or new_step_reps != 1:
                        step_copy['repetitions'] = new_step_reps
                    elif 'repetitions' in step_copy and not original_has_reps:
                        step_copy.pop('repetitions', None)
                elif step_reps != 1:
                    step_copy['repetitions'] = max(1, round(step_reps * eff_factor))
                elif not original_has_reps and 'repetitions' in step_copy:
                    step_copy.pop('repetitions', None)

                scaled_sequence.append(step_copy)
            s_copy['sequence'] = scaled_sequence
        else:
            # Regular sets: distance-based entries scale distance; time-based
            # entries (with reps>1) scale reps, not duration.
            ds = s_copy.get('distance','') or s_copy.get('duration','')
            intensity = s_copy.get('intensity','')
            new_ds, new_reps = scale_for_run_type(workout_type, ds, reps, eff_factor, intensity)

            if 'distance' in s_copy:
                s_copy['distance'] = new_ds
            elif 'duration' in s_copy:
                s_copy['duration'] = new_ds
            s_copy['repetitions'] = new_reps

        new_sets.append(s_copy)
    return new_sets

def adjust_segments(segments, workout_type):
    new_segments = []
    for seg in segments:
        s_copy = copy.deepcopy(seg)
        if 'sets' in seg:
            s_copy['sets'] = scale_sets(seg['sets'], workout_type)
        else:
            reps = parse_repetitions(s_copy.get('repetitions', 1))
            ds = s_copy.get('distance','') or s_copy.get('duration','')
            intensity = s_copy.get('intensity','')
            eff_factor = get_effective_scale_factor(workout_type, intensity)
            new_ds, new_reps = scale_for_run_type(workout_type, ds, reps, eff_factor, intensity)
            
            if 'distance' in s_copy:
                s_copy['distance'] = new_ds
            elif 'duration' in s_copy:
                s_copy['duration'] = new_ds
            s_copy['repetitions'] = new_reps
        new_segments.append(s_copy)
    return new_segments

def adjust_interval_workout(workout):
    w_copy = copy.deepcopy(workout)
    wt = w_copy.get('type','').lower()
    
    if wt == 'race':
        ds = w_copy.get('distance')
        parsed = parse_distance_str(ds) if isinstance(ds, str) else None
        if parsed:
            return w_copy
        race_mi = race_distance_miles()
        w_copy['distance'] = f"{round(race_mi,1)} mi"
        return w_copy
    
    if 'sets' in w_copy:
        w_copy['sets'] = scale_sets(w_copy['sets'], wt)
    
    if 'segments' in w_copy:
        w_copy['segments'] = adjust_segments(w_copy['segments'], wt)
    
    if 'distance' in w_copy:
        ds = w_copy['distance']
        r = parse_repetitions(w_copy.get('repetitions', 1))
        eff_factor = get_effective_scale_factor(wt, w_copy.get('intensity',''))
        new_ds, new_reps = scale_for_run_type(wt, ds, r, eff_factor, w_copy.get('intensity',''))
        w_copy['distance'] = new_ds
        w_copy['repetitions'] = new_reps
    
    return w_copy

def adjust_easy_workout(workout):
    w_copy = copy.deepcopy(workout)
    wt = w_copy.get('type','').lower()
    
    # If this easy-like workout has segments (e.g., collapsed AM/PM), scale each segment
    if 'segments' in w_copy and isinstance(w_copy['segments'], list):
        w_copy['segments'] = adjust_segments(w_copy['segments'], wt)
        return w_copy

    ds = w_copy.get('distance','') or w_copy.get('duration','')
    if ds:
        r = parse_repetitions(w_copy.get('repetitions', 1))
        eff_factor = get_effective_scale_factor(w_copy.get('type',''), w_copy.get('intensity',''))
        new_ds, new_reps = scale_for_run_type(w_copy['type'], ds, r, eff_factor, w_copy.get('intensity',''))
        if 'distance' in w_copy:
            w_copy['distance'] = new_ds
        elif 'duration' in w_copy:
            w_copy['duration'] = new_ds
        w_copy['repetitions'] = new_reps
    
    return w_copy

def adjust_workout(workout):
    """Return scaled copy of a workout object, applying comprehensive scaling logic."""
    w_copy = copy.deepcopy(workout)

    if isinstance(w_copy, list):
        return [adjust_workout(w) for w in w_copy]
    elif isinstance(w_copy, dict):
        w_copy = _expand_progression_run(w_copy)
        wt = w_copy.get('type','').lower()
        has_sets_or_segments = ('segments' in w_copy) or ('sets' in w_copy)
        
        # Route types that are truly workout-structured (or steady with segments)
        if wt in ['interval','kenyan-style progression run','tempo','progression','progression run','long','long run','race','easy to moderate'] or (wt == 'steady' and has_sets_or_segments):
            return adjust_interval_workout(w_copy)
        elif 'am' in w_copy or 'pm' in w_copy:
            # Handle AM/PM workouts
            def _type_of(x):
                if isinstance(x, dict):
                    t = (x.get('type','') or '').lower()
                    return t
                return ''
            def _is_easy_like(t):
                return t in ['easy','very easy','easy to moderate','steady']
            def _is_workout_like(t):
                return t in ['interval','tempo','progression','progression run','long','long run','race','special block','kenyan-style progression run']
            def _is_easy_drop_type(t):
                return t in ['easy','very easy','easy to moderate','moderate']
            def _max_intensity_pct(w):
                max_pct = None
                if isinstance(w, dict):
                    inten = w.get('intensity','')
                    if inten:
                        try:
                            pct = intensity_to_pct(inten)
                            if pct is not None:
                                max_pct = pct if max_pct is None else max(max_pct, pct)
                        except Exception:
                            pass
                    if 'am' in w or 'pm' in w:
                        for key in ('am','pm'):
                            sub = w.get(key)
                            if sub is not None:
                                sub_pct = _max_intensity_pct(sub)
                                if sub_pct is not None:
                                    max_pct = sub_pct if max_pct is None else max(max_pct, sub_pct)
                    if 'segments' in w and isinstance(w['segments'], list):
                        for seg in w['segments']:
                            sub_pct = _max_intensity_pct(seg)
                            if sub_pct is not None:
                                max_pct = sub_pct if max_pct is None else max(max_pct, sub_pct)
                    if 'sets' in w and isinstance(w['sets'], list):
                        for st in w['sets']:
                            sub_pct = _max_intensity_pct(st)
                            if sub_pct is not None:
                                max_pct = sub_pct if max_pct is None else max(max_pct, sub_pct)
                    if 'sequence' in w and isinstance(w['sequence'], list):
                        for step in w['sequence']:
                            sub_pct = _max_intensity_pct(step)
                            if sub_pct is not None:
                                max_pct = sub_pct if max_pct is None else max(max_pct, sub_pct)
                    if 'recovery' in w and isinstance(w['recovery'], dict):
                        sub_pct = _max_intensity_pct(w['recovery'])
                        if sub_pct is not None:
                            max_pct = sub_pct if max_pct is None else max(max_pct, sub_pct)
                elif isinstance(w, list):
                    for item in w:
                        sub_pct = _max_intensity_pct(item)
                        if sub_pct is not None:
                            max_pct = sub_pct if max_pct is None else max(max_pct, sub_pct)
                return max_pct
            def _is_quality_secondary(w):
                t = _type_of(w)
                if _is_workout_like(t):
                    return True
                if t == 'steady':
                    return True
                try:
                    threshold = intensity_to_pct("marathon pace")
                except Exception:
                    threshold = 90.0
                pct = _max_intensity_pct(w)
                if pct is not None and pct >= threshold:
                    return True
                return False
            def _to_segments(w):
                if not isinstance(w, dict):
                    return []
                if 'segments' in w and isinstance(w['segments'], list):
                    return copy.deepcopy(w['segments'])
                if 'sets' in w and isinstance(w['sets'], list):
                    return [{ 'sets': copy.deepcopy(w['sets']) }]
                seg = {}
                if 'duration' in w:
                    seg['duration'] = w['duration']
                if 'distance' in w:
                    seg['distance'] = w['distance']
                if 'intensity' in w and w['intensity']:
                    seg['intensity'] = w['intensity']
                return [seg] if seg else []
            def _combine_scaled(amw_obj, pmw_obj, primary_type):
                """Scale AM/PM separately, then combine into a single segments workout."""
                segs = []
                if amw_obj is not None:
                    am_scaled = adjust_workout(amw_obj)
                    segs += _to_segments(am_scaled)
                if pmw_obj is not None:
                    pm_scaled = adjust_workout(pmw_obj)
                    segs += _to_segments(pm_scaled)
                ctype = (primary_type or '').lower() or 'special block'
                return { 'type': ctype, 'segments': segs }
            if collapse_doubles and wt != 'special block':
                amw = w_copy.get('am')
                pmw = w_copy.get('pm')
                am_t = _type_of(amw)
                pm_t = _type_of(pmw)
                # Prefer workout if one is easy-like and the other is workout-like
                if amw is not None and pmw is not None:
                    if _is_easy_drop_type(am_t) and _is_workout_like(pm_t):
                        if _is_quality_secondary(amw):
                            return _combine_scaled(amw, pmw, pm_t)
                        return adjust_workout(pmw)
                    if _is_workout_like(am_t) and _is_easy_drop_type(pm_t):
                        if _is_quality_secondary(pmw):
                            return _combine_scaled(amw, pmw, am_t)
                        return adjust_workout(amw)
                    if _is_quality_secondary(amw) and _is_workout_like(pm_t):
                        return _combine_scaled(amw, pmw, pm_t)
                    if _is_quality_secondary(pmw) and _is_workout_like(am_t):
                        return _combine_scaled(amw, pmw, am_t)
                    # If both easy-like, COMBINE into one session (segments) instead of choosing one
                    if _is_easy_like(am_t) and _is_easy_like(pm_t):
                        ctype = am_t if am_t == pm_t and am_t else 'easy to moderate'
                        combined = { 'type': ctype, 'segments': _to_segments(amw) + _to_segments(pmw) }
                        return adjust_workout(combined)
                    # Both workouts: default keep both unless consolidation requested
                    if _is_workout_like(am_t) and _is_workout_like(pm_t):
                        if consolidate_two_workout_doubles:
                            combined = { 'type': 'special block', 'segments': _to_segments(amw) + _to_segments(pmw) }
                            return adjust_workout(combined)
                        # keep both (do not collapse)
                        # fall through to default branch below
                        pass
                    # Mixed/unknown: prefer PM
                    if not (_is_workout_like(am_t) and _is_workout_like(pm_t)):
                        return adjust_workout(pmw if pmw is not None else amw)
            # default: keep both and scale each
            if 'am' in w_copy:
                w_copy['am'] = adjust_workout(w_copy['am'])
            if 'pm' in w_copy:
                w_copy['pm'] = adjust_workout(w_copy['pm'])
            return w_copy
        else:
            return adjust_easy_workout(w_copy)
    else:
        return w_copy

def create_day_description(workout, original_workout):
    scaled = workout_to_string_original(workout)
    orig = workout_to_string_original(original_workout)
    return f"{scaled}\n(Original plan: {orig})".strip()

def _is_wu_segment(seg):
    if not isinstance(seg, dict):
        return False
    inten = (seg.get('intensity','') or '').lower()
    if 'warm' in inten:
        return True
    if 'very easy' in inten:
        # Treat leading/trailing very easy as WU/CD; determination handled by caller
        return True
    return False

def _is_cd_segment(seg):
    # Same detection as WU; caller uses position to decide WU vs CD
    return _is_wu_segment(seg)

def _fmt_step_distance_or_duration(step):
    if 'duration' in step:
        return step['duration']
    if 'distance' in step:
        return step['distance']
    return ''

def _fmt_set_simple(s, include_pace=True):
    reps = parse_repetitions(s.get('repetitions', 1))
    ds = s.get('duration') or s.get('distance') or ''
    intensity = s.get('intensity','')
    rec = s.get('recovery',{})
    pace_label = race_pace_label()
    parts = [f"{reps}x {ds}"]
    if intensity:
        if is_rest_intensity(intensity):
            parts.append(ensure_of_hmp(intensity))
        else:
            pct = intensity_to_pct(intensity)
            if include_pace and pct:
                pace = pace_to_string(pace_for_intensity(intensity, pct))
                parts.append(f"{pct:.0f}% of {pace_label} (~{pace})")
            else:
                parts.append(ensure_of_hmp(intensity))
    if isinstance(rec, dict) and (rec.get('duration') or rec.get('distance')):
        rds = rec.get('duration') or rec.get('distance')
        rtyp = (rec.get('type','') or '')
        parts.append(f"w/ {rds} {rtyp}".strip())
    return ' '.join(p for p in parts if p)

def _fmt_sequence_set(s, include_pace=True):
    reps = parse_repetitions(s.get('repetitions', 1))
    pace_label = race_pace_label()
    seq = []
    for step in s.get('sequence', []):
        ds = _fmt_step_distance_or_duration(step)
        intensity = step.get('intensity','')
        step_reps = parse_repetitions(step.get('repetitions', 1))
        rec = step.get('recovery') if isinstance(step.get('recovery'), dict) else None
        rec_desc = ""
        if rec and (rec.get('duration') or rec.get('distance')):
            rds = rec.get('duration') or rec.get('distance')
            rtyp = (rec.get('type','') or '').strip()
            rec_desc = f"{rds} {rtyp}".strip()
        if intensity:
            if is_rest_intensity(intensity):
                text = f"{ds} {ensure_of_hmp(intensity)}".strip()
            else:
                pct = intensity_to_pct(intensity)
                if include_pace and pct:
                    pace = pace_to_string(pace_for_intensity(intensity, pct))
                    text = f"{ds} {pct:.0f}% of {pace_label} (~{pace})"
                else:
                    text = f"{ds} {ensure_of_hmp(intensity)}"
        else:
            text = ds

        text = text.strip()
        if step_reps > 1 and text:
            text = f"{step_reps}x {text}"
        if rec_desc:
            text = f"{text} w/ {rec_desc}".strip()

        if text:
            seq.append(text)
    inner = ' + '.join(seq)
    return f"{reps}x ({inner})"

def workout_to_simplified_string(workout, html=False):
    def simplify_obj(o):
        if not isinstance(o, dict):
            return str(o)
        pace_label = race_pace_label()
        if _is_rest(o):
            return "Rest"
        # AM/PM containers
        if 'am' in o or 'pm' in o:
            parts = []
            if 'am' in o and isinstance(o['am'], (dict, list)):
                parts.append("AM: " + simplify_obj(o['am']))
            if 'pm' in o and isinstance(o['pm'], (dict, list)):
                parts.append("PM: " + simplify_obj(o['pm']))
            return ' | '.join(parts)
        # Segments-based workout
        if 'segments' in o and isinstance(o['segments'], list):
            segs = o['segments']
            tokens = []
            bold_idx = set()
            n = len(segs)
            for i, seg in enumerate(segs):
                if 'sets' in seg:
                    # main work
                    for s in seg['sets']:
                        if 'sequence' in s:
                            tokens.append(_fmt_sequence_set(s, include_pace=True))
                        else:
                            tokens.append(_fmt_set_simple(s, include_pace=True))
                        bold_idx.add(len(tokens)-1)
                else:
                    # simple duration/distance segment
                    tok = _fmt_step_distance_or_duration(seg)
                    if i == 0 and _is_wu_segment(seg):
                        tokens.append(f"{tok} WU")
                    elif i == n-1 and _is_cd_segment(seg):
                        tokens.append(f"{tok} CD")
                    else:
                        # main work
                        intensity = seg.get('intensity','')
                        if is_rest_intensity(intensity):
                            tokens.append(f"{tok} {ensure_of_hmp(intensity)}".strip())
                        else:
                            pct = intensity_to_pct(intensity)
                            if pct:
                                pace = pace_to_string(pace_for_intensity(intensity, pct))
                                tokens.append(f"{tok} {pct:.0f}% of {pace_label} (~{pace})")
                            else:
                                tokens.append(f"{tok} {ensure_of_hmp(intensity)}".strip())
                            bold_idx.add(len(tokens)-1)
                    # recovery attached to seg
                    rec = seg.get('recovery',{})
                    if isinstance(rec, dict) and (rec.get('duration') or rec.get('distance')):
                        rds = rec.get('duration') or rec.get('distance')
                        rtyp = (rec.get('type','') or '')
                        tokens[-1] = (tokens[-1] + f" w/ {rds} {rtyp}".strip())
            if html:
                out = []
                for i, t in enumerate(tokens):
                    out.append(f"<strong>{t}</strong>" if i in bold_idx else t)
                return ' + '.join(out)
            return ' + '.join(tokens)
        # Sets-only workout
        if 'sets' in o and isinstance(o['sets'], list):
            tokens = []
            for s in o['sets']:
                if 'sequence' in s:
                    tokens.append(_fmt_sequence_set(s, include_pace=True))
                else:
                    tokens.append(_fmt_set_simple(s, include_pace=True))
            if html:
                return ' + '.join(f"<strong>{t}</strong>" for t in tokens)
            return ' + '.join(tokens)
        # simple
        ds = o.get('duration') or o.get('distance') or ''
        intensity = o.get('intensity','')
        if is_rest_intensity(intensity):
            return f"{ds} {ensure_of_hmp(intensity)}".strip() if ds else "Rest"
        pct = intensity_to_pct(intensity)
        if not is_rest_intensity(intensity) and pct:
            pace = pace_to_string(pace_for_intensity(intensity, pct))
            return f"{ds} {pct:.0f}% of {pace_label} (~{pace})".strip()
        return f"{ds} {ensure_of_hmp(intensity)}".strip()

    return simplify_obj(workout)

def estimate_total_time_mins(miles, include_wu_cd=False):
    """
    Estimate total time in minutes given miles.
    Uses workout pace if available, otherwise assumes 9:15/mi for WU/CD.
    """
    if miles <= 0:
        return 0.0
    wu_cd_pace = 9.25  # 9:15 min/mi
    if include_wu_cd:
        return miles * wu_cd_pace
    return miles * race_pace_min_per_mile

def format_time_hhmm(total_minutes):
    hrs = int(total_minutes // 60)
    mins = int(round(total_minutes % 60))
    if hrs > 0:
        return f"{hrs}h {mins}m"
    return f"{mins}m"

def is_wu_cd_workout(w):
    """
    Return True if the workout type should include a warmup/cooldown (the same
    types used previously for adding +1.0 mi to mileage).
    """
    def get_type_local(x):
        if isinstance(x, dict):
            if 'type' in x:
                t = x['type'].lower()
                if "very easy" in t:
                    return "very easy"
                return t
            for key in ['am','pm']:
                if key in x:
                    t = x[key].get('type','').lower()
                    if "very easy" in t:
                        return "very easy"
                    if t:
                        return t
        elif isinstance(x, list):
            for xx in x:
                tt = get_type_local(xx)
                if tt:
                    return tt
        return ''
    t = get_type_local(w)
    if t == 'steady':
        # Only treat steady as needing implicit WU/CD when it has explicit structure
        # (segments/sets or embedded workout-like AM/PM). Plain steady runs should not add WU/CD.
        if isinstance(w, dict):
            if 'segments' in w or 'sets' in w:
                return True
            if 'am' in w or 'pm' in w:
                for key in ('am', 'pm'):
                    sub = w.get(key)
                    if isinstance(sub, dict):
                        sub_t = (sub.get('type','') or '').lower()
                        if sub_t in ['interval','tempo','special block','race','kenyan-style progression run','progression','progression run']:
                            return True
        return False
    return t in ['interval','tempo','special block','race','kenyan-style progression run','progression','progression run']

def _has_explicit_wu_cd(w):
    """Return True if workout contains explicit warmup/cooldown segments."""
    def _is_ve(seg):
        if not isinstance(seg, dict):
            return False
        inten = (seg.get('intensity','') or '').strip().lower()
        if not (('very easy' in inten) or ('recovery' in inten) or ('warm' in inten) or ('cool' in inten)):
            return False
        return (seg.get('duration') is not None) or (seg.get('distance') is not None)

    if isinstance(w, dict):
        if 'am' in w or 'pm' in w:
            am = w.get('am')
            pm = w.get('pm')
            return _has_explicit_wu_cd(am) or _has_explicit_wu_cd(pm)
        segs = w.get('segments')
        if isinstance(segs, list) and segs:
            return _is_ve(segs[0]) or _is_ve(segs[-1])
        return False
    if isinstance(w, list):
        return any(_has_explicit_wu_cd(x) for x in w)
    return False

def _expand_progression_run(workout):
    """Expand simple progression runs into explicit segments."""
    if not isinstance(workout, dict):
        return workout
    wt = (workout.get('type','') or '').strip().lower()
    if wt not in ('progression', 'progression run', 'kenyan-style progression run'):
        return workout
    if 'segments' in workout or 'sets' in workout:
        return workout
    ds = workout.get('distance') or workout.get('duration')
    if not ds:
        return workout

    # Default progression: 50% easy, then 20/15/10/5% at MP/HMP/10K/5K
    ratios = [
        ("Easy", 0.50),
        ("Marathon pace", 0.20),
        ("Half marathon pace", 0.15),
        ("10K pace", 0.10),
        ("5K pace", 0.05),
    ]

    dmin = parse_duration_str(ds)
    parsed = parse_distance_str(ds) if dmin is None else None
    segments = []

    if dmin is not None:
        for label, frac in ratios:
            seg_min = max(1.0, dmin * frac)
            segments.append({
                "duration": _fmt_minutes(seg_min),
                "intensity": label
            })
    elif parsed:
        ((low_mi, high_mi), unit, orig) = parsed
        base_mi = high_mi if high_mi else low_mi
        for label, frac in ratios:
            seg_mi = max(0.1, base_mi * frac)
            if unit in ('km','kilometers'):
                amt = seg_mi * 1.60934
            elif unit in ('m','meters'):
                amt = seg_mi * 1609.34
            else:
                amt = seg_mi
            segments.append({
                "distance": _fmt_distance(amt, unit),
                "intensity": label
            })
    else:
        return workout

    new_w = copy.deepcopy(workout)
    new_w.pop('distance', None)
    new_w.pop('duration', None)
    new_w['segments'] = segments
    return new_w

def _implicit_wu_cd_miles_time():
    """Return (total_miles, total_minutes) for implicit WU/CD using overrides."""
    total_mi = None
    total_min = None
    try:
        if implicit_wu_cd_distance_miles is not None:
            val = float(implicit_wu_cd_distance_miles)
            if val > 0:
                total_mi = val
    except Exception:
        total_mi = None
    try:
        if implicit_wu_cd_duration_min is not None:
            val = float(implicit_wu_cd_duration_min)
            if val > 0:
                total_min = val
    except Exception:
        total_min = None

    if total_mi is None and total_min is None:
        total_mi = 1.0
    if total_min is None and total_mi is not None:
        total_min = total_mi * WU_CD_PACE_MIN_PER_MILE
    if total_mi is None and total_min is not None:
        total_mi = total_min / WU_CD_PACE_MIN_PER_MILE
    return total_mi or 0.0, total_min or 0.0

def get_implicit_wu_cd_miles_time():
    return _implicit_wu_cd_miles_time()

def create_event_name(workout, daily_mileage):
    def has_optional(w):
        if isinstance(w, dict):
            if w.get('optional', False):
                return True
            for key in ['am','pm']:
                if key in w and isinstance(w[key], dict) and w[key].get('optional', False):
                    return True
        elif isinstance(w, list):
            for ww in w:
                if has_optional(ww):
                    return True
        return False

    def get_type(w):
        if isinstance(w, dict):
            if 'type' in w:
                t = w['type'].lower()
                if "very easy" in t:
                    return "very easy"
                return t
            for key in ['am','pm']:
                if key in w:
                    t = w[key].get('type','').lower()
                    if "very easy" in t:
                        return "very easy"
                    if t:
                        return t
        elif isinstance(w, list):
            for ww in w:
                t = get_type(ww)
                if t:
                    return t
        return 'easy'

    def pretty_type(t):
        t = (t or '').lower()
        if t == 'interval':
            return 'Interval'
        if t == 'tempo':
            return 'Tempo'
        if t in ['progression','progression run']:
            return 'Progression'
        if t in ['long','long run']:
            return 'Long run'
        if t == 'race':
            return f"Race {race_distance_display()}"
        if t == 'special block':
            return 'Special block'
        if t == 'kenyan-style progression run':
            return 'Kenyan-style progression'
        if t == 'steady':
            return 'Steady'
        if t == 'easy to moderate':
            return 'Easy to moderate'
        if t == 'very easy':
            return 'Very easy'
        if t == 'easy':
            return 'Easy'
        return (t or 'easy').capitalize()

    def cat_label(t):
        # Category label used in AM/PM combined titles
        return pretty_type(t)

    # If doubles present and not collapsed, show AM/PM categories in title
    if isinstance(workout, dict) and ('am' in workout or 'pm' in workout) and not collapse_doubles:
        am_t = (workout.get('am', {}) or {}).get('type','') if isinstance(workout.get('am'), dict) else ''
        pm_t = (workout.get('pm', {}) or {}).get('type','') if isinstance(workout.get('pm'), dict) else ''
        parts = []
        if am_t:
            parts.append(f"AM {cat_label(am_t)}")
        if pm_t:
            parts.append(f"PM {cat_label(pm_t)}")
        if parts:
            return f"{', '.join(parts)} - {round(daily_mileage,1)} mi"

    opt = has_optional(workout)
    wtype = get_type(workout)
    if opt and wtype == 'very easy':
        return f"Very easy ({round(daily_mileage,1)} mi) or Rest"
    if opt and wtype == 'easy':
        return f"Easy ({round(daily_mileage,1)} mi) or Rest"
    if wtype in ['interval','tempo','progression','progression run','long','long run','race','special block','kenyan-style progression run','steady','easy to moderate']:
        return f"{pretty_type(wtype)} - {round(daily_mileage,1)} mi"
    if wtype == 'rest':
        return "Rest"
    if wtype == 'very easy':
        return f"Very easy - {round(daily_mileage,1)} mi"
    if wtype == 'easy':
        return f"Easy - {round(daily_mileage,1)} mi"
    return f"{wtype.capitalize()} - {round(daily_mileage,1)} mi"

def add_weekly_summary(cal, start_of_week, phase_name, week_num, total_miles, original_total_str, total_minutes=None):
    ev = Event()
    label = shorten_phase(phase_name) if phase_name else "Week"
    if total_minutes is not None:
        try:
            tstr = format_time_hhmm(float(total_minutes))
        except Exception:
            tstr = None
    else:
        tstr = None
    extra = f" • ~{tstr}" if tstr else ""
    ev.name = f"{label} - Week {week_num} - Total Miles ~{round(total_miles,1)} mi{extra}"
    ev.begin = datetime(start_of_week.year, start_of_week.month, start_of_week.day, 0, 0, 0)
    ev.end   = ev.begin + timedelta(days=6)
    ev.make_all_day()
    cal.events.add(ev)

def add_race_day_event(cal, race_date):
    ev = Event()
    ev.name = f"Race Day - {race_distance_display()}"
    ev.begin = datetime(race_date.year, race_date.month, race_date.day, 0, 0, 0)
    ev.end   = ev.begin + timedelta(days=1)
    ev.make_all_day()
    cal.events.add(ev)

def get_plan_reference_peak(plan_meta):
    """Return plan reference peak mileage if present; otherwise None."""
    if not isinstance(plan_meta, dict):
        return None
    baseline_peak = plan_meta.get('reference_peak_mileage')
    if baseline_peak is None:
        pm_text = plan_meta.get('peak_mileage', '')
        m = re.search(r"(\\d+(?:\\.\\d+)?)", str(pm_text))
        baseline_peak = float(m.group(1)) if m else None
    return baseline_peak

def _plan_workout_factor(plan_meta, base_factor):
    """Return plan-provided workout factor if present, else fall back to base_factor."""
    wf = None
    if isinstance(plan_meta, dict):
        if 'workout_factor' in plan_meta:
            try:
                wf = float(plan_meta['workout_factor'])
            except Exception:
                wf = None
        if wf is None and 'workout_factor_pct' in plan_meta:
            try:
                wf = float(plan_meta['workout_factor_pct']) / 100.0
            except Exception:
                wf = None
        if wf is None and 'workout_factor_multiplier' in plan_meta:
            try:
                wf = base_factor * float(plan_meta['workout_factor_multiplier'])
            except Exception:
                wf = None
    if wf is None:
        wf = base_factor
    return wf

# -------------------------------
# Preview (HTML) writer

def write_html_preview(cal, preview_path, weekly_totals=None, home_url: str = None, preview_token: str = None):
    """Write a styled HTML preview of the calendar events."""
    try:
        events = sorted(cal.events, key=lambda e: (e.begin.datetime if hasattr(e.begin, 'datetime') else e.begin, e.name or ""))

        def fmt_date(e):
            dt = e.begin.datetime if hasattr(e.begin, 'datetime') else e.begin
            if getattr(e, 'all_day', False):
                return dt.strftime('%a %Y-%m-%d') + " (all day)"
            return dt.strftime('%a %Y-%m-%d %H:%M')

        def _day_anchor_id(dt):
            return f"day-{dt.strftime('%Y-%m-%d')}"

        css = """
        :root {
          --ink: #14201d;
          --ink-soft: #33433f;
          --muted: #66736f;
          --canvas: #f5f4ef;
          --paper: #fffefa;
          --line: #dcded7;
          --line-strong: #c8ccc3;
          --green-950: #102b27;
          --green-900: #163c35;
          --green-800: #1e5148;
          --green-100: #e3eee9;
          --lime: #c9f45a;
          --lime-strong: #a9d83e;
          --lime-soft: #f1f9d9;
          --shadow-sm: 0 1px 2px rgba(16,43,39,.06), 0 8px 24px rgba(16,43,39,.05);
          --shadow-md: 0 18px 50px rgba(16,43,39,.11);
          --radius: 16px;
          --radius-lg: 24px;
          --accent: var(--green-800);
          --accent-2: var(--lime);
          --accent-3: var(--green-950);
        }
        * { box-sizing: border-box; }
        html { scroll-behavior: smooth; }
        body {
          margin: 0;
          min-width: 320px;
          font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          color: var(--ink);
          background:
            radial-gradient(circle at 86% 8%, rgba(201,244,90,.17), transparent 25rem),
            linear-gradient(180deg, #f7f6f1 0, var(--canvas) 38rem, #efefea 100%);
          min-height: 100vh;
          line-height: 1.5;
          text-rendering: optimizeLegibility;
        }
        .page {
          max-width: 1230px;
          margin: 0 auto;
          padding: 40px 32px 72px;
          display: flex;
          flex-direction: column;
          gap: 18px;
        }
        body.embedded { background: transparent; }
        body.embedded .page {
          max-width: none;
          padding: 0;
        }
        body.embedded header.hero { display: none; }
        header.hero {
          display: flex;
          flex-direction: column;
          gap: 14px;
          padding: clamp(26px, 4vw, 48px);
          border: 1px solid var(--line);
          border-radius: var(--radius-lg);
          background:
            radial-gradient(circle at 88% 5%, rgba(201,244,90,.2), transparent 18rem),
            var(--paper);
          box-shadow: var(--shadow-sm);
          position: relative;
          overflow: hidden;
        }
        .brand {
          display: inline-flex;
          width: max-content;
          align-items: center;
          gap: 10px;
          color: var(--ink);
        }
        .brand img {
          width: 40px;
          height: 40px;
          flex: 0 0 auto;
          border-radius: 11px;
          object-fit: cover;
          box-shadow: 0 3px 10px rgba(16,43,39,.2);
        }
        .brand strong, .brand small { display: block; }
        .brand strong {
          font-size: 15px;
          line-height: 1.1;
          letter-spacing: -.02em;
        }
        .brand small {
          margin-top: 3px;
          color: var(--muted);
          font-size: 10px;
          font-weight: 650;
          letter-spacing: .08em;
          text-transform: uppercase;
        }
        .badge {
          padding: 4px 10px;
          border-radius: 999px;
          background: rgba(38,70,83,0.1);
          border: 1px solid rgba(38,70,83,0.15);
        }
        h1 {
          max-width: 850px;
          margin: 18px 0 2px;
          font-family: ui-serif, Georgia, "Times New Roman", serif;
          font-weight: 550;
          font-size: clamp(38px, 6vw, 68px);
          line-height: 1;
          letter-spacing: -.04em;
        }
        .meta {
          color: var(--muted);
          font-size: 15px;
        }
        .stats {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }
        .pill {
          display: inline-flex;
          align-items: center;
          min-height: 30px;
          padding: 6px 11px;
          border: 1px solid rgba(30,81,72,.12);
          border-radius: 999px;
          background: var(--green-100);
          color: var(--accent);
          font-size: 10px;
          font-weight: 750;
          text-decoration: none;
        }
        a.pill:hover { background: var(--lime); color: var(--green-950); }
        .stack {
          display: flex;
          flex-direction: column;
          gap: 18px;
        }
        .card {
          background: rgba(255,254,250,.9);
          border: 1px solid var(--line);
          border-radius: var(--radius-lg);
          padding: clamp(20px, 3vw, 30px);
          box-shadow: var(--shadow-sm);
        }
        .section-title { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
        .section-title h2 {
          margin: 0;
          font-family: ui-serif, Georgia, serif;
          font-size: clamp(24px, 3vw, 34px);
          font-weight: 560;
          letter-spacing: -.035em;
        }
        .hist {
          display: flex;
          gap: 10px;
          align-items: flex-end;
          height: clamp(140px, 24vh, 220px);
          padding: 12px 8px 10px 8px;
          border: 1px solid var(--line);
          border-radius: var(--radius);
          background: linear-gradient(180deg, #fff, #f7f8f3);
          overflow-x: auto;
        }
        .bar { display: flex; flex-direction: column; align-items: center; width: clamp(26px, 6vw, 44px); gap: 6px; }
        .bar-inner {
          width: 100%;
          background: linear-gradient(180deg, var(--green-800), var(--lime-strong));
          border-radius: 8px 8px 4px 4px;
          box-shadow: 0 10px 18px rgba(16,43,39,.14);
        }
        .bar-label { font-size: 11px; color: #3a3a38; }
        .bar-value { font-size: 11px; color: #5a5957; }
        .bar-time { font-size: 10px; color: #7a7976; white-space: nowrap; }
        .bar-link { display: block; width: 100%; }
        .bar a { text-decoration: none; color: inherit; }
        .event {
          border: 1px solid var(--line);
          padding: 16px 18px;
          margin: 10px 0;
          border-radius: var(--radius);
          background: rgba(255,255,255,.82);
          scroll-margin-top: 18px;
          transition: box-shadow 180ms ease, transform 180ms ease;
        }
        .event.is-jump-target {
          box-shadow: 0 0 0 3px rgba(169,216,62,.58), var(--shadow-md);
          transform: translateY(-1px);
        }
        .event.easy { border-left: 4px solid var(--accent); background: rgba(227,238,233,.72); }
        .event.workout { border-left: 4px solid var(--lime-strong); background: var(--lime-soft); }
        .event.rest { border-left: 4px solid var(--line-strong); background: #f5f5f1; }
        .event.week { border-left: 4px solid var(--accent-3); background: var(--green-100); }
        .event.week { cursor: pointer; }
        .week-days { margin-left: 18px; display: none; }
        .week-days.open { display: block; }
        .event.race { border-left: 4px solid var(--lime-strong); background: var(--lime-soft); }
        .event.week .date { display: none; }
        .week-title { font-weight: 600; font-size: 15px; margin-bottom: 6px; color: var(--accent-3); }
        .week-mini {
          margin-top: 10px;
          padding: 12px;
          border-radius: 14px;
          border: 1px solid rgba(34, 30, 24, 0.1);
          background: rgba(255,255,255,0.8);
        }
        .week-mini-grid {
          display: grid;
          grid-template-columns: repeat(7, minmax(0, 1fr));
          gap: 8px;
          align-items: end;
        }
        .week-mini-day {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 4px;
          font-size: 10px;
          color: #5a5957;
          text-decoration: none;
        }
        .week-mini-bar {
          width: 100%;
          min-height: 10px;
          border-radius: 8px;
          background: rgba(38,70,83,0.15);
        }
        .week-mini-day.workout .week-mini-bar { background: linear-gradient(180deg, var(--lime-strong), #cce979); }
        .week-mini-day.easy .week-mini-bar { background: linear-gradient(180deg, rgba(15,106,91,0.85), rgba(15,106,91,0.4)); }
        .week-mini-day.rest .week-mini-bar { background: rgba(38,70,83,0.18); }
        .week-mini-mile { font-size: 10px; color: #2f2f2d; }
        .week-mini-time { font-size: 9px; color: #7a7976; white-space: nowrap; }
        .event-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
        .btn-fit {
          border: none;
          border-radius: 999px;
          padding: 9px 15px;
          background: var(--green-950);
          color: white;
          font-family: inherit;
          font-size: 12px;
          font-weight: 750;
          cursor: pointer;
          box-shadow: var(--shadow-sm);
        }
        .btn-fit.secondary {
          background: var(--green-100);
          color: var(--accent-3);
          border: 1px solid var(--line);
          box-shadow: none;
        }
        .btn-fit:hover { transform: translateY(-1px); }
        .btn-fit:disabled { cursor: wait; opacity: .6; transform: none; }
        .fit-status {
          flex: 1 1 100%;
          min-height: 1.2em;
          color: var(--muted);
          font-size: 11px;
        }
        .chart-wrap {
          position: relative;
          margin-top: 12px;
          padding: 10px;
          border-radius: 14px;
          border: 1px solid rgba(34, 30, 24, 0.1);
          background: rgba(255,255,255,0.78);
        }
        .mini-chart {
          width: 100%;
          height: 140px;
          border-radius: 10px;
          border: 1px solid rgba(34,30,24,0.08);
          background: linear-gradient(180deg, rgba(255,255,255,0.9), rgba(250,247,242,0.85));
        }
        .chart-tooltip {
          position: absolute;
          z-index: 4;
          width: max-content;
          max-width: min(240px, calc(100% - 24px));
          padding: 9px 11px;
          border: 1px solid rgba(201,244,90,.35);
          border-radius: 10px;
          background: var(--green-950);
          color: white;
          box-shadow: 0 12px 28px rgba(16,43,39,.22);
          font-size: 11px;
          line-height: 1.4;
          pointer-events: none;
          transform: translate(-50%, calc(-100% - 10px));
          opacity: 0;
          transition: opacity 100ms ease;
        }
        .chart-tooltip.visible { opacity: 1; }
        .chart-tooltip.below { transform: translate(-50%, 10px); }
        .chart-tooltip strong {
          display: block;
          margin-bottom: 3px;
          color: var(--lime);
          font-size: 12px;
        }
        .event-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
        .date { color: #555; font-size: 12px; }
        .name { font-weight: 600; margin: 4px 0 8px 0; font-size: 15px; }
        .desc { white-space: pre-wrap; color: #333; line-height: 1.5; }
        details { margin-top: 10px; }
        summary { cursor: pointer; font-size: 13px; color: var(--accent-3); }
        pre { background: rgba(255,255,255,0.85); padding: 10px; border-radius: 10px; border: 1px solid rgba(34,30,24,0.08); overflow-x: auto; }
        .backtop { margin-top: 8px; }
        .backtop a { font-size: 12px; color: var(--accent-3); text-decoration: none; }
        @media (max-width: 980px) {
          .hist { gap: 8px; }
        }
        @media (max-width: 600px) {
          .page { padding: 18px 12px 40px; }
          header.hero, .card { border-radius: 18px; }
          .week-days { margin-left: 8px; }
          .section-title { align-items: flex-start; gap: 12px; }
        }
        """

        title = cal.name or "Training Plan"
        week_count = len(weekly_totals) if weekly_totals else 0
        icon_src = "/app-icon.png"
        try:
            icon_path = (
                Path(__file__).resolve().parent / "PromptFitIOS" / "PromptFitIOS"
                / "Assets.xcassets" / "AppIcon.appiconset" / "PromptFitIcon-v2.png"
            )
            icon_src = "data:image/png;base64," + base64.b64encode(icon_path.read_bytes()).decode("ascii")
        except Exception:
            if home_url:
                icon_src = home_url.rstrip("/") + "/app-icon.png"
        html_parts = [
            "<!doctype html>",
            "<html lang='en'>",
            "<head>",
            f"<meta charset='utf-8'><title>{html.escape(title)}</title>",
            "<meta name='viewport' content='width=device-width, initial-scale=1'>",
            f"<style>{css}</style>",
            "</head>",
            "<body>",
            "<div class='page'>",
            "<header class='hero'>",
            f"<div class='brand'><img src='{html.escape(icon_src)}' alt='' width='40' height='40'><span><strong>PromptFit</strong><small>Training plan preview</small></span></div>",
            f"<h1>{html.escape(title)}</h1>",
            "<p class='meta'>Preview your scaled training plan and weekly mileage at a glance.</p>",
            "<div class='stats'>",
            f"<span class='pill'>{len(events)} events</span>",
            f"<span class='pill'>{week_count} weeks</span>",
            "</div>",
            (f"<div class='stats'><a class='pill' href='{html.escape(home_url)}'>Back to web app</a></div>" if home_url else ""),
            "</header>",
            "<main class='stack'>",
            "<section class='card'>",
            "<div class='section-title'><h2>Weekly mileage</h2><span class='pill'>Overview</span></div>",
        ]

        # Weekly totals histogram (if provided)
        if weekly_totals:
            try:
                max_mi = max((wt.get('miles', 0) for wt in weekly_totals), default=0)
                max_mi = max_mi if max_mi > 0 else 1
                max_h = 140  # px
                html_parts.append("<div class='hist' id='top' title='Weekly mileage overview'>")
                for wt in weekly_totals:
                    w = wt.get('week')
                    mi = float(wt.get('miles', 0) or 0)
                    workout_mi = float(wt.get('workout_miles', 0) or 0)
                    easy_mi = float(wt.get('easy_miles', 0) or 0)
                    mins = float(wt.get('minutes', 0) or 0)
                    tstr = format_time_hhmm(mins) if mins > 0 else ""
                    h = int(round((mi / max_mi) * max_h))
                    html_parts.append("  <div class='bar'>")
                    # Clickable bar and label jump to the corresponding week section
                    breakdown_txt = ""
                    denom = workout_mi + easy_mi
                    if denom > 0:
                        breakdown_txt = f" • Workout {workout_mi:.1f} mi • Easy {easy_mi:.1f} mi"
                    title_txt = f"Week {w}: {mi} mi" + (f" • {tstr}" if tstr else "") + breakdown_txt
                    html_parts.append(f"    <a class='bar-link' href='#week-{w}' title='{title_txt}'>")
                    bar_style = f"height:{h}px"
                    if denom > 0:
                        pct = max(0.0, min(100.0, (workout_mi / denom) * 100.0))
                        pct_str = f"{pct:.1f}%"
                        bar_style += f";background: linear-gradient(180deg, var(--accent-2) 0%, var(--accent-2) {pct_str}, var(--accent) {pct_str}, var(--accent) 100%)"
                    html_parts.append(f"      <div class='bar-inner' style='{bar_style}'></div>")
                    html_parts.append( "    </a>")
                    html_parts.append(f"    <div class='bar-label'><a href='#week-{w}' title='{title_txt}'>W{w}</a></div>")
                    html_parts.append(f"    <div class='bar-value'>{mi:.0f} mi</div>")
                    if tstr:
                        html_parts.append(f"    <div class='bar-time'>{tstr}</div>")
                    html_parts.append("  </div>")
                html_parts.append("</div>")
            except Exception:
                pass

        html_parts.append("</section>")
        html_parts.append("<section class='card'>")
        html_parts.append("<div class='section-title'><h2>Plan details</h2><span class='pill'>Daily view</span></div>")

        current_week_anchor = None
        week_days_open = False
        for e in events:
            name = (e.name or "").strip()
            desc = (e.description or "").strip()
            classes = ["event"]
            lowname = name.lower()
            is_all_day = bool(getattr(e, 'all_day', False))
            is_week = is_all_day and ("week" in lowname)
            if week_days_open and is_all_day:
                html_parts.append("</div>")
                week_days_open = False
            if is_week:
                classes.append("week")
            if "race" in lowname:
                classes.append("race")
            # Track current week for grouping
            if is_week:
                try:
                    import re as _re
                    mweek = _re.search(r"week\\s+(\\d+)\\b", lowname)
                    current_week_anchor = f"week-{int(mweek.group(1))}" if mweek else None
                except Exception:
                    current_week_anchor = None
            elif is_all_day:
                current_week_anchor = None
            # easy vs workout coloring
            cat = getattr(e, '_category', None)
            if not cat:
                cat = 'workout' if name.lower().startswith('workout') else ('easy' if 'easy' in name.lower() else '')
            if cat:
                classes.append(cat)
            # If this is a week summary event, assign an anchor id like 'week-3'
            week_id_attr = ""
            week_anchor = None
            try:
                if is_week:
                    import re as _re
                    m = _re.search(r"week\s+(\d+)\b", lowname)
                    if m:
                        week_anchor = f"week-{int(m.group(1))}"
                        week_id_attr = f" id='{week_anchor}'"
            except Exception:
                week_id_attr = ""
            data_attrs = ""
            workout_payload = getattr(e, '_scaled_workout_dict', None)
            is_rest_event = False
            if workout_payload is not None:
                try:
                    payload = json.dumps(workout_payload, separators=(",", ":"), ensure_ascii=True)
                    data_attrs = f" data-workout='{html.escape(payload)}'"
                    try:
                        is_rest_event = _is_rest(workout_payload)
                    except Exception:
                        is_rest_event = False
                except Exception:
                    data_attrs = ""
            fit_name = getattr(e, '_fit_name', None)
            if fit_name:
                data_attrs += f" data-fit-name='{html.escape(str(fit_name))}'"
            fit_graph = getattr(e, '_fit_graph', None)
            if fit_graph:
                try:
                    graph_payload = json.dumps(fit_graph, separators=(",", ":"), ensure_ascii=True)
                    data_attrs += f" data-fit-graph='{html.escape(graph_payload)}'"
                except Exception:
                    pass
            day_id_attr = ""
            try:
                if not is_week:
                    dt = e.begin.datetime if hasattr(e.begin, 'datetime') else e.begin
                    day_id_attr = f" id='{_day_anchor_id(dt)}'"
            except Exception:
                day_id_attr = ""
            if is_week:
                week_data_attr = f" data-week-id='{html.escape(week_anchor)}'" if week_anchor else ""
                html_parts.append(f"<div class='{' '.join(classes)}'" + week_id_attr + week_data_attr + ">")
            else:
                wk_attr = f" data-week-parent='{html.escape(current_week_anchor)}'" if current_week_anchor and not is_all_day else ""
                html_parts.append(f"<div class='{' '.join(classes)}'" + week_id_attr + day_id_attr + wk_attr + data_attrs + ">")
            if is_week:
                html_parts.append(f"  <div class='week-title'>{html.escape(name)}</div>")
                html_parts.append("  <div class='event-actions'>")
                html_parts.append("    <button class='btn-fit' data-action='upload-week'>Upload / replace this week’s FITs</button>")
                html_parts.append("    <span class='fit-status' role='status' aria-live='polite'></span>")
                html_parts.append("  </div>")
                # Mini weekly calendar (daily mileage/time)
                try:
                    import re as _re
                    mweek = _re.search(r"week\s+(\d+)\b", lowname)
                    wk_num = int(mweek.group(1)) if mweek else None
                    wk_data = None
                    if wk_num is not None and weekly_totals:
                        for wtd in weekly_totals:
                            if wtd.get('week') == wk_num:
                                wk_data = wtd
                                break
                    days = wk_data.get('days') if wk_data else None
                    if days:
                        max_mi = max((d.get('miles', 0) for d in days), default=0) or 1
                        html_parts.append("  <div class='week-mini'>")
                        html_parts.append("    <div class='week-mini-grid'>")
                        for d in days:
                            label = d.get('label','')
                            miles = float(d.get('miles', 0) or 0)
                            minutes = float(d.get('minutes', 0) or 0)
                            cat = d.get('category','')
                            h = int(round((miles / max_mi) * 60)) if miles > 0 else 10
                            tstr = format_time_hhmm(minutes) if minutes > 0 else ""
                            day_id = d.get('day_id')
                            html_parts.append(f"      <a class='week-mini-day {html.escape(cat)}' href='#{html.escape(day_id) if day_id else ''}'>")
                            html_parts.append(f"        <div class='week-mini-bar' style='height:{h}px'></div>")
                            html_parts.append(f"        <div>{html.escape(label)}</div>")
                            html_parts.append(f"        <div class='week-mini-mile'>{miles:.1f} mi</div>")
                            if tstr:
                                html_parts.append(f"        <div class='week-mini-time'>{html.escape(tstr)}</div>")
                            html_parts.append("      </a>")
                        html_parts.append("    </div>")
                        html_parts.append("  </div>")
                except Exception:
                    pass
            else:
                html_parts.append("  <div class='event-head'>")
                html_parts.append(f"    <div class='date'>{html.escape(fmt_date(e))}</div>")
                html_parts.append("  </div>")
                html_parts.append(f"  <div class='name'>{html.escape(name)}</div>")
            # Prefer rich HTML description if provided
            rich = getattr(e, '_desc_html', None)
            if rich:
                html_parts.append(f"  <div class='desc'>{rich}</div>")
            elif desc:
                html_parts.append(f"  <div class='desc'>{html.escape(desc)}</div>")
            # Week notes (if present)
            week_notes = getattr(e, '_week_notes', None)
            if week_notes:
                html_parts.append("  <details>")
                html_parts.append("    <summary>Week notes</summary>")
                html_parts.append(f"    <pre>{html.escape(str(week_notes))}</pre>")
                html_parts.append("  </details>")
            # If available, include readable JSON for the workouts
            scaled = getattr(e, '_scaled_workout_dict', None)
            original = getattr(e, '_original_workout_dict', None)
            if scaled is not None or original is not None:
                html_parts.append("  <details>")
                html_parts.append("    <summary>Show workout JSON</summary>")
                if scaled is not None:
                    try:
                        scaled_json = json.dumps(scaled, indent=2, sort_keys=True)
                        html_parts.append("    <div><strong>Scaled</strong></div>")
                        html_parts.append(f"    <pre>{html.escape(scaled_json)}</pre>")
                    except Exception:
                        pass
                if original is not None:
                    try:
                        original_json = json.dumps(original, indent=2, sort_keys=True)
                        html_parts.append("    <div><strong>Original</strong></div>")
                        html_parts.append(f"    <pre>{html.escape(original_json)}</pre>")
                    except Exception:
                        pass
                html_parts.append("  </details>")
            if workout_payload is not None and not ("week" in lowname and getattr(e, 'all_day', False)) and not is_rest_event:
                html_parts.append("  <div class='event-actions'>")
                html_parts.append("    <button class='btn-fit' data-action='download-fit'>Download FIT</button>")
                if fit_name:
                    html_parts.append("    <button class='btn-fit' data-action='upload-day'>Upload / replace on Garmin</button>")
                html_parts.append("    <button class='btn-fit secondary' data-action='toggle-chart'>Toggle chart</button>")
                html_parts.append("    <span class='fit-status' role='status' aria-live='polite'></span>")
                html_parts.append("  </div>")
                html_parts.append("  <div class='chart-wrap' style='display:block;'>")
                html_parts.append("    <canvas class='mini-chart' height='140'></canvas>")
                html_parts.append("  </div>")
            # Back to top
            if week_id_attr:
                html_parts.append("  <div class='backtop'><a href='#top' title='Back to overview'>↑ Back to top</a></div>")
            html_parts.append("</div>")
            if is_week and week_anchor:
                html_parts.append(f"<div class='week-days' data-week-parent='{html.escape(week_anchor)}'>")
                week_days_open = True

        if week_days_open:
            html_parts.append("</div>")

        html_parts.append("</section>")
        html_parts.append("</main>")
        html_parts.append("</div>")
        html_parts.append("<script>")
        html_parts.append(f"const PLAN_HOME = {json.dumps(home_url or '')};")
        html_parts.append(f"const PLAN_PREVIEW_TOKEN = {json.dumps(preview_token or '')};")
        html_parts.append(f"const PLAN_RACE_PACE = {race_pace_min_per_mile:.4f};")
        html_parts.append(f"const PLAN_RACE_DIST_MILES = {race_distance_miles():.5f};")
        html_parts.append(f"const PLAN_INCLUDE_WU_CD = {str(bool(include_implicit_wu_cd)).lower()};")
        html_parts.append(f"const PLAN_WU_CD_DISTANCE = {json.dumps(implicit_wu_cd_distance_miles)};")
        html_parts.append(f"const PLAN_WU_CD_DURATION = {json.dumps(implicit_wu_cd_duration_min)};")
        html_parts.append("""
function parseRangeNumber(text){
  if (!text) return null;
  const s = String(text).trim().replace(/–/g, '-');
  const m = s.match(/(\\d+(?:\\.\\d+)?)(?:\\s*-\\s*(\\d+(?:\\.\\d+)?))?/);
  if (!m) return null;
  const a = parseFloat(m[1]);
  const b = m[2] ? parseFloat(m[2]) : a;
  if (!isFinite(a) || !isFinite(b)) return null;
  return (a + b) / 2;
}
function parseDurationMin(text){
  if (!text) return null;
  const s = String(text).toLowerCase();
  let m = s.match(/(\\d+(?:\\.\\d+)?)(?:\\s*[–-]\\s*(\\d+(?:\\.\\d+)?))?\\s*min/);
  if (m){
    const val = (parseFloat(m[1]) + (m[2] ? parseFloat(m[2]) : parseFloat(m[1]))) / 2;
    return val;
  }
  m = s.match(/(\\d+(?:\\.\\d+)?)(?:\\s*[–-]\\s*(\\d+(?:\\.\\d+)?))?\\s*sec/);
  if (m){
    const val = (parseFloat(m[1]) + (m[2] ? parseFloat(m[2]) : parseFloat(m[1]))) / 2;
    return val / 60;
  }
  return null;
}
function parseDistanceMiles(text){
  if (!text) return null;
  const s = String(text).toLowerCase().replace(/–/g, '-');
  let m = s.match(/(\\d+(?:\\.\\d+)?)(?:\\s*-\\s*(\\d+(?:\\.\\d+)?))?\\s*(mi|miles)\\b/);
  if (m){
    const val = (parseFloat(m[1]) + (m[2] ? parseFloat(m[2]) : parseFloat(m[1]))) / 2;
    return val;
  }
  m = s.match(/(\\d+(?:\\.\\d+)?)(?:\\s*-\\s*(\\d+(?:\\.\\d+)?))?\\s*(km|kilometers)\\b/);
  if (m){
    const val = (parseFloat(m[1]) + (m[2] ? parseFloat(m[2]) : parseFloat(m[1]))) / 2;
    return val * 0.621371;
  }
  m = s.match(/(\\d+(?:\\.\\d+)?)(?:\\s*-\\s*(\\d+(?:\\.\\d+)?))?\\s*(m|meters)\\b/);
  if (m){
    const val = (parseFloat(m[1]) + (m[2] ? parseFloat(m[2]) : parseFloat(m[1]))) / 2;
    return val / 1609.34;
  }
  return null;
}
function intensityToPct(intensity){
  if (!intensity) return 100;
  const norm = String(intensity).toLowerCase();
  const RACE_MILES = (typeof PLAN_RACE_DIST_MILES === 'number' && isFinite(PLAN_RACE_DIST_MILES))
    ? PLAN_RACE_DIST_MILES
    : 6.21371;
  const riegelExp = 1.06;
  const labelDistanceMiles = (label) => {
    if (!label) return null;
    const s = String(label).toLowerCase();
    if (s.includes('race')) return RACE_MILES;
    if (s.includes('5k')) return 3.10686;
    if (s.includes('10k')) return 6.21371;
    if (s.includes('half') || s.includes('hmp')) return 13.1094;
    if (s.includes('marathon')) return 26.2188;
    return null;
  };
  const paceAtDistance = (pace, from, to) => {
    if (!pace || !from || !to) return pace;
    return pace * Math.pow((to / from), (riegelExp - 1));
  };
  const pctFromLabel = (pct, label) => {
    const pctVal = parseFloat(pct);
    if (!isFinite(pctVal)) return pct;
    const labelDist = labelDistanceMiles(label);
    if (!labelDist || !RACE_MILES) return pctVal;
    if (Math.abs(labelDist - RACE_MILES) < 0.01) return pctVal;
    const labelPace = paceAtDistance(PLAN_RACE_PACE, RACE_MILES, labelDist);
    const targetPace = labelPace * (100 / pctVal);
    if (!targetPace) return pctVal;
    return (PLAN_RACE_PACE / targetPace) * 100;
  };

  const labeled = norm.replace(/–/g, '-').match(/(\\d+(?:\\.\\d+)?)(?:\\s*-\\s*(\\d+(?:\\.\\d+)?))?\\s*%\\s*of\\s*([a-z0-9\\s-]+)/);
  if (labeled){
    const a = parseFloat(labeled[1]);
    const b = labeled[2] ? parseFloat(labeled[2]) : a;
    if (isFinite(a) && isFinite(b)) return pctFromLabel((a + b) / 2, labeled[3] || '');
  }
  const pctMatch = norm.replace(/–/g, '-').match(/(\\d+(?:\\.\\d+)?)(?:\\s*-\\s*(\\d+(?:\\.\\d+)?))?\\s*%/);
  if (pctMatch){
    const a = parseFloat(pctMatch[1]);
    const b = pctMatch[2] ? parseFloat(pctMatch[2]) : a;
    if (isFinite(a) && isFinite(b)) return (a + b) / 2;
  }
  const pct10kToRace = (pct10k) => pctFromLabel(pct10k, '10k pace');
  if (norm.includes('very easy') || norm.includes('recovery') || norm.includes('shakeout')) return pct10kToRace(70);
  if (norm.includes('easy to moderate')) return pct10kToRace(79);
  if (norm.includes('moderate') && !norm.includes('easy')) return pct10kToRace(83);
  if (norm.includes('easy') && !norm.includes('very easy')) return pct10kToRace(75);
  if (norm.includes('steady') || norm.includes('lt1')) return pct10kToRace(87);
  if (norm.includes('strong') || norm.includes('marathon pace') || norm.includes('predicted marathon pace')) return pct10kToRace(90);
  if (norm.includes('sub-threshold') || norm.includes('sub threshold')) return pct10kToRace(92);
  if (norm.includes('half marathon') || norm.includes('hm pace')) return pct10kToRace(95);
  if (norm.includes('threshold') || norm.includes('lt2') || norm.includes('t pace') || norm.includes('ssmax')) return pct10kToRace(96.5);
  if (norm.includes('10k') && norm.includes('pace')) return pct10kToRace(100);
  if (norm.includes('8k')) return pct10kToRace(102);
  if (norm.includes('5k')) return pct10kToRace(105);
  if (norm.includes('vvo2') || norm.includes('i pace')) return pct10kToRace(108);
  if (norm.includes('3k')) return pct10kToRace(110);
  if ((norm.includes('mile') && norm.includes('pace')) || norm.includes('r pace')) return pct10kToRace(115);
  return 100;
}
function paceFromIntensity(intensity){
  const p = parsePace(intensity);
  if (p) return p;
  const pct = intensityToPct(intensity);
  if (!pct || !isFinite(pct) || !PLAN_RACE_PACE || !isFinite(PLAN_RACE_PACE)) return null;
  return PLAN_RACE_PACE / (pct / 100);
}
function parsePace(text){
  if (!text) return null;
  const m = String(text).match(/(\\d{1,2}):(\\d{2})\\s*(?:\\/mi|per mile|min\\/mi)?/);
  if (!m) return null;
  const mins = parseInt(m[1], 10);
  const secs = parseInt(m[2], 10);
  return mins + (secs / 60);
}
function isRest(intensity){
  if (!intensity) return false;
  const s = String(intensity).toLowerCase();
  return s.includes('rest') || s.includes('walk');
}
function addSegment(segments, durationMin, intensity){
  if (!durationMin || !isFinite(durationMin)) return;
  const pace = paceFromIntensity(intensity || '');
  segments.push({ duration_s: durationMin * 60, pace_min_per_mi: pace, intensity: intensity || '' });
}
function handleStep(segments, step, reps){
  const count = reps || 1;
  for (let i=0; i<count; i++){
    const ds = step.distance || step.duration || '';
    const intensity = step.intensity || '';
    const dmin = parseDurationMin(ds);
    if (dmin !== null){
      addSegment(segments, dmin, intensity);
    } else {
      const miles = parseDistanceMiles(ds);
      if (miles !== null){
        const pace = paceFromIntensity(intensity);
        addSegment(segments, miles * pace, intensity);
      }
    }
    const rec = step.recovery || null;
    if (rec){
      const recInt = rec.type ? `Rest (${rec.type})` : 'Rest';
      const rds = rec.duration || rec.distance || '';
      const rdmin = parseDurationMin(rds);
      if (rdmin !== null){
        addSegment(segments, rdmin, recInt);
      } else {
        const rmiles = parseDistanceMiles(rds);
        if (rmiles !== null){
          const pace = paceFromIntensity(recInt);
          addSegment(segments, rmiles * pace, recInt);
        }
      }
    }
  }
}
function buildSegments(workout){
  const segments = [];
  const walkObj = (obj) => {
    if (!obj) return;
    if (Array.isArray(obj)){
      obj.forEach(walkObj);
      return;
    }
    if (obj.am || obj.pm){
      if (obj.am) walkObj(obj.am);
      if (obj.pm) walkObj(obj.pm);
      return;
    }
    if (obj.segments && Array.isArray(obj.segments)){
      obj.segments.forEach((seg) => {
        if (seg.sets){
          seg.sets.forEach((set) => {
            const reps = parseRangeNumber(set.repetitions) || 1;
            if (set.sequence){
              for (let r=0; r<reps; r++){
                set.sequence.forEach((step) => {
                  const stepReps = parseRangeNumber(step.repetitions) || 1;
                  handleStep(segments, step, stepReps);
                });
              }
            } else {
              handleStep(segments, set, reps);
            }
          });
        } else {
          handleStep(segments, seg, parseRangeNumber(seg.repetitions) || 1);
        }
      });
      return;
    }
    if (obj.sets){
      obj.sets.forEach((set) => {
        const reps = parseRangeNumber(set.repetitions) || 1;
        if (set.sequence){
          for (let r=0; r<reps; r++){
            set.sequence.forEach((step) => {
              const stepReps = parseRangeNumber(step.repetitions) || 1;
              handleStep(segments, step, stepReps);
            });
          }
        } else {
          handleStep(segments, set, reps);
        }
      });
      return;
    }
    handleStep(segments, obj, parseRangeNumber(obj.repetitions) || 1);
  };
  walkObj(workout);
  return segments;
}
function formatChartDuration(seconds){
  const totalSeconds = Math.max(0, Math.round(Number(seconds) || 0));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const secs = totalSeconds % 60;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes && !secs) return `${minutes}m`;
  if (minutes) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}
function formatChartPace(pace){
  const totalSeconds = Math.max(0, Math.round((Number(pace) || 0) * 60));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = String(totalSeconds % 60).padStart(2, '0');
  return `${minutes}:${seconds}/mi`;
}
function formatChartDistance(segment){
  const meters = Number(segment.distance_m);
  const miles = Number.isFinite(meters) && meters > 0
    ? meters / 1609.344
    : ((Number(segment.duration_s) || 0) / 60) / (Number(segment.pace_min_per_mi) || 1);
  const digits = miles >= 10 ? 1 : 2;
  return `${miles.toFixed(digits).replace(/\.?0+$/, '')} mi`;
}
function attachChartTooltip(canvas){
  if (!canvas || canvas.dataset.tooltipReady === 'true') return;
  const wrap = canvas.closest('.chart-wrap');
  if (!wrap) return;
  const tooltip = document.createElement('div');
  tooltip.className = 'chart-tooltip';
  tooltip.setAttribute('role', 'tooltip');
  wrap.appendChild(tooltip);
  canvas.dataset.tooltipReady = 'true';

  const hide = () => tooltip.classList.remove('visible');
  canvas.addEventListener('pointerleave', hide);
  canvas.addEventListener('pointermove', (event) => {
    const state = canvas._promptFitChart;
    if (!state || !state.ranges || !state.ranges.length) return hide();
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const {pad, width, height, total, ranges} = state;
    if (x < pad.left || x > pad.left + width || y < pad.top || y > pad.top + height) return hide();
    const elapsed = ((x - pad.left) / width) * total;
    const range = ranges.find((item, index) => elapsed < item.end || index === ranges.length - 1);
    if (!range) return hide();
    const segment = range.segment;
    const title = segment.label || segment.intensity || `Leg ${range.index + 1}`;
    tooltip.replaceChildren();
    const strong = document.createElement('strong');
    strong.textContent = title;
    const details = document.createElement('span');
    details.textContent = `${formatChartDuration(segment.duration_s)} · ${formatChartDistance(segment)} · ${formatChartPace(segment.pace_min_per_mi)}`;
    tooltip.append(strong, details);
    tooltip.classList.add('visible');
    tooltip.classList.toggle('below', y < 58);

    const wrapRect = wrap.getBoundingClientRect();
    const halfWidth = (tooltip.offsetWidth || 180) / 2;
    const localX = event.clientX - wrapRect.left;
    const localY = event.clientY - wrapRect.top;
    tooltip.style.left = `${Math.max(halfWidth + 8, Math.min(wrapRect.width - halfWidth - 8, localX))}px`;
    tooltip.style.top = `${localY}px`;
  });
}
function renderChart(canvas, segments){
  if (!canvas || !segments || !segments.length) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);
  const total = segments.reduce((a, s) => a + (s.duration_s || 0), 0);
  if (!total) return;
  const paces = segments.map(s => s.pace_min_per_mi).filter(p => isFinite(p));
  if (!paces.length){
    ctx.clearRect(0,0,rect.width,rect.height);
    ctx.fillStyle = 'rgba(38,70,83,0.6)';
    ctx.font = '12px "Space Grotesk", sans-serif';
    ctx.fillText('Chart unavailable', 10, 20);
    return;
  }
  const observedMin = Math.min(...paces);
  const observedMax = Math.max(...paces);
  const observedRange = observedMax - observedMin;
  // Keep the pace line readable for both steady runs and varied workouts.
  // Bounds snap to 15-second increments and include at least one minute.
  const targetRange = Math.max(1, observedRange * 1.35);
  const centerPace = (observedMin + observedMax) / 2;
  const minP = Math.max(0.25, Math.floor((centerPace - targetRange / 2) * 4) / 4);
  const maxP = Math.max(minP + 1, Math.ceil((centerPace + targetRange / 2) * 4) / 4);
  const pad = { left: 40, right: 10, top: 10, bottom: 22 };
  const w = rect.width - pad.left - pad.right;
  const h = rect.height - pad.top - pad.bottom;
  const xAt = (t) => pad.left + (t / total) * w;
  const yAt = (p) => pad.top + ((p - minP) / (maxP - minP || 1)) * h;
  ctx.clearRect(0,0,rect.width,rect.height);
  ctx.strokeStyle = 'rgba(38,70,83,0.18)';
  ctx.lineWidth = 1;
  for (let i=0;i<=3;i++){
    const y = pad.top + (i/3)*h;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(pad.left + w, y);
    ctx.stroke();
  }
  // Axis labels
  ctx.fillStyle = 'rgba(28,28,28,0.6)';
  ctx.font = '11px "Space Grotesk", sans-serif';
  const paceLabel = (p) => {
    const totalSeconds = Math.max(0, Math.round(p * 60));
    const mins = Math.floor(totalSeconds / 60);
    const secs = totalSeconds % 60;
    const ss = String(secs).padStart(2, '0');
    return `${mins}:${ss}`;
  };
  for (let i=0;i<=3;i++){
    const y = pad.top + (i/3)*h;
    const pace = minP + ((maxP - minP) * (i/3));
    ctx.fillText(paceLabel(pace), 4, y + 4);
  }
  const xTicks = 4;
  for (let i=0;i<=xTicks;i++){
    const t = (total / xTicks) * i;
    const x = xAt(t);
    const m = Math.round((t / 60));
    ctx.fillText(`${m}m`, x - 8, rect.height - 6);
  }
  let t = 0;
  const points = [];
  const ranges = [];
  segments.forEach((seg, index) => {
    const dur = seg.duration_s || 0;
    if (!dur) return;
    const pace = seg.pace_min_per_mi || maxP;
    const x0 = xAt(t);
    const x1 = xAt(t + dur);
    const y = yAt(pace);
    points.push([x0, y], [x1, y]);
    ranges.push({index, segment: seg, start: t, end: t + dur});
    t += dur;
  });
  if (!points.length) return;
  ctx.beginPath();
  points.forEach(([x, y], idx) => idx === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y));
  ctx.lineTo(xAt(total), pad.top + h);
  ctx.lineTo(xAt(0), pad.top + h);
  ctx.closePath();
  ctx.fillStyle = 'rgba(169,216,62,0.28)';
  ctx.fill();
  ctx.beginPath();
  points.forEach(([x, y], idx) => idx === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y));
  ctx.strokeStyle = '#a9d83e';
  ctx.lineWidth = 2.5;
  ctx.stroke();
  canvas._promptFitChart = {pad, width: w, height: h, total, ranges};
  attachChartTooltip(canvas);
}
function parseWorkout(card){
  if (!card) return null;
  const raw = card.dataset.workout || '';
  if (!raw) return null;
  try{ return JSON.parse(raw); }catch(e){ return null; }
}
function renderChartsFor(container){
  if (!container) return;
  container.querySelectorAll('.event[data-workout]').forEach((card) => {
    const canvas = card.querySelector('canvas.mini-chart');
    let fitGraph = null;
    if (card.dataset.fitGraph){
      try{ fitGraph = JSON.parse(card.dataset.fitGraph); }catch(e){}
    }
    const workout = parseWorkout(card);
    const segments = (fitGraph && fitGraph.segments) ? fitGraph.segments : (workout ? buildSegments(workout) : []);
    if (segments && segments.length) renderChart(canvas, segments);
  });
}
async function downloadFit(button, workout, fitName){
  const bases = [];
  if (PLAN_HOME) bases.push(PLAN_HOME);
  if (window.location && window.location.origin && window.location.origin.startsWith('http')) {
    if (!bases.includes(window.location.origin)) bases.push(window.location.origin);
  }
  if (!bases.length) {
    bases.push('http://localhost:8000', 'http://127.0.0.1:8000');
  }
  const payload = {
    workout: workout,
    race_pace: PLAN_RACE_PACE,
    targets: true,
    target_mode: 'pace',
    target_margin: 30,
    include_wu_cd: PLAN_INCLUDE_WU_CD,
    wu_cd_distance: PLAN_WU_CD_DISTANCE,
    wu_cd_duration: PLAN_WU_CD_DURATION,
    name: button.closest('.event')?.querySelector('.name')?.textContent || 'workout'
  };
  try{
    button.disabled = true;
    let res = null;
    let lastErr = '';
    for (const base of bases){
      const primaryUrl = (fitName && PLAN_PREVIEW_TOKEN)
        ? base + '/api/plan-fit/' + encodeURIComponent(PLAN_PREVIEW_TOKEN) + '/' + encodeURIComponent(fitName)
        : '';
      const fallbackUrl = base + '/api/plan-workout-fit';
      if (primaryUrl){
        try{
          res = await fetch(primaryUrl);
          if (res && res.ok) break;
        }catch(e){
          lastErr = e && e.message ? e.message : String(e);
        }
      }
      if (workout){
        try{
          res = await fetch(fallbackUrl, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
          if (res && res.ok) break;
          if (res) lastErr = await res.text();
        }catch(e){
          lastErr = e && e.message ? e.message : String(e);
        }
      }
    }
    if (!res || !res.ok){
      throw new Error(lastErr || 'request failed');
    }
    const blob = await res.blob();
    const disp = res.headers.get('Content-Disposition') || '';
    let filename = fitName || 'workout.fit';
    const part = disp.split('filename=')[1];
    if (part) filename = part.replace(/\"/g,'');
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
  }catch(e){
    const msg = (e && e.message) ? e.message : String(e);
    alert('FIT download failed. If you opened the HTML from disk, open the preview from the web app instead.\\n' + msg);
  }
  button.disabled = false;
}
function planApiBases(){
  const bases = [];
  if (PLAN_HOME) bases.push(PLAN_HOME);
  if (window.location && window.location.origin && window.location.origin.startsWith('http')){
    if (!bases.includes(window.location.origin)) bases.push(window.location.origin);
  }
  if (!bases.length) bases.push('http://localhost:8000', 'http://127.0.0.1:8000');
  return bases;
}
function isoDateFromCard(card){
  const match = String((card && card.id) || '').match(/^day-(\d{4}-\d{2}-\d{2})$/);
  return match ? match[1] : '';
}
async function uploadPlanFits(button, fitNames, startDate, endDate){
  const status = button.closest('.event')?.querySelector('.fit-status');
  const names = Array.from(new Set((fitNames || []).filter(Boolean)));
  if (!PLAN_PREVIEW_TOKEN){
    if (status) status.textContent = 'Open this preview from the web app to upload.';
    return;
  }
  if (!names.length){
    if (status) status.textContent = 'This section has no generated workout FITs.';
    return;
  }
  button.disabled = true;
  if (status) status.textContent = `Refreshing ${names.length} Garmin workout${names.length === 1 ? '' : 's'}…`;
  try{
    let response = null;
    let lastError = '';
    for (const base of planApiBases()){
      try{
        response = await fetch(base + '/api/garmin/plan-upload/' + encodeURIComponent(PLAN_PREVIEW_TOKEN), {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            fit_names: names,
            start_date: startDate || undefined,
            end_date: endDate || undefined,
            replace: true
          })
        });
        if (response.ok) break;
        const problem = await response.json().catch(() => ({}));
        lastError = problem.detail || problem.message || 'Garmin upload failed';
      }catch(error){
        lastError = error && error.message ? error.message : String(error);
      }
    }
    if (!response || !response.ok) throw new Error(lastError || 'Garmin upload failed');
    const report = await response.json();
    const scheduled = Number(report.scheduled || 0);
    const replaced = Number(report.replaced || 0);
    if (status){
      status.textContent = `${scheduled} scheduled${replaced ? `; ${replaced} earlier PromptFit workout${replaced === 1 ? '' : 's'} replaced` : ''}.`;
    }
  }catch(error){
    if (status) status.textContent = error && error.message ? error.message : String(error);
  }finally{
    button.disabled = false;
  }
}
window.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.event[data-workout]').forEach((card) => {
    let workout = parseWorkout(card);
    const canvas = card.querySelector('canvas.mini-chart');
    let fitGraph = null;
    if (card.dataset.fitGraph){
      try{ fitGraph = JSON.parse(card.dataset.fitGraph); }catch(e){}
    }
    const segments = (fitGraph && fitGraph.segments) ? fitGraph.segments : (workout ? buildSegments(workout) : []);
    if (segments && segments.length) renderChart(canvas, segments);
    const fitBtn = card.querySelector('button[data-action="download-fit"]');
    if (fitBtn){
      const fitName = card.dataset.fitName || '';
      fitBtn.addEventListener('click', () => downloadFit(fitBtn, workout, fitName));
    }
    const uploadBtn = card.querySelector('button[data-action="upload-day"]');
    if (uploadBtn){
      const fitName = card.dataset.fitName || '';
      const day = isoDateFromCard(card);
      uploadBtn.addEventListener('click', () => uploadPlanFits(uploadBtn, [fitName], day, day));
    }
    const toggleBtn = card.querySelector('button[data-action="toggle-chart"]');
    if (toggleBtn && canvas){
      toggleBtn.addEventListener('click', () => {
        const wrap = canvas.closest('.chart-wrap');
        if (!wrap) return;
        wrap.style.display = wrap.style.display === 'none' ? 'block' : 'none';
      });
    }
  });

});
window.addEventListener('resize', () => {
  renderChartsFor(document);
});
// Week collapse/expand
window.addEventListener('DOMContentLoaded', () => {
  const weeks = document.querySelectorAll('.event.week');
  weeks.forEach((week) => {
    const id = week.getAttribute('id');
    if (!id) return;
    let wrap = week.nextElementSibling;
    if (!(wrap && wrap.classList && wrap.classList.contains('week-days'))){
      const days = Array.from(document.querySelectorAll(`.event[data-week-parent="${id}"]`));
      if (!days.length) return;
      wrap = document.createElement('div');
      wrap.className = 'week-days';
      week.insertAdjacentElement('afterend', wrap);
      days.forEach((d) => wrap.appendChild(d));
    }
    const uploadWeekBtn = week.querySelector('button[data-action="upload-week"]');
    if (uploadWeekBtn){
      const datedCards = Array.from(wrap.querySelectorAll('.event[id^="day-"]'));
      const fitNames = datedCards.map((card) => card.dataset.fitName || '').filter(Boolean);
      const dates = datedCards.map(isoDateFromCard).filter(Boolean).sort();
      if (!fitNames.length){
        uploadWeekBtn.disabled = true;
        uploadWeekBtn.title = 'No generated workout FITs in this week';
      }else{
        uploadWeekBtn.addEventListener('click', () => {
          uploadPlanFits(uploadWeekBtn, fitNames, dates[0] || '', dates[dates.length - 1] || '');
        });
      }
    }
    week.querySelectorAll('.week-mini-day[href^="#day-"]').forEach((link) => {
      link.addEventListener('click', (event) => {
        event.preventDefault();
        const selector = link.getAttribute('href');
        const target = selector ? document.querySelector(selector) : null;
        if (!target) return;
        wrap.classList.add('open');
        renderChartsFor(wrap);
        try{ history.replaceState(null, '', selector); }catch(error){}
        window.requestAnimationFrame(() => {
          target.scrollIntoView({behavior: 'smooth', block: 'center'});
          target.classList.add('is-jump-target');
          window.setTimeout(() => target.classList.remove('is-jump-target'), 1400);
        });
      });
    });
    week.addEventListener('click', (e) => {
      const skip = e && e.target && e.target.closest
        ? e.target.closest('a, button, summary, details')
        : null;
      if (skip) return;
      wrap.classList.toggle('open');
      if (wrap.classList.contains('open')){
        renderChartsFor(wrap);
      }
    });
  });
});
""")
        html_parts.append("</script>")
        html_parts.append("</body></html>")

        with open(preview_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(html_parts))
    except Exception as ex:
        # If anything goes wrong, don't fail the main flow
        print(f"[preview] Failed to write HTML preview: {ex}")

# -------------------------------
# Rest-day selection and redistribution helpers

def _extract_type(w):
    if isinstance(w, dict):
        if 'type' in w and w['type']:
            return w['type'].lower()
        for k in ('am','pm'):
            if k in w and isinstance(w[k], dict):
                t = (w[k].get('type','') or '').lower()
                if t:
                    return t
    elif isinstance(w, list):
        for x in w:
            t = _extract_type(x)
            if t:
                return t
    return ''

def _is_rest(w):
    return _extract_type(w) == 'rest'

def _is_easy_like_type(t):
    return t in ('easy','very easy','steady','easy to moderate','moderate')

def _is_workout_like_type(t):
    return t in ['interval','tempo','progression','progression run','long','long run','race','special block','kenyan-style progression run']

def _quality_threshold_pct():
    try:
        return intensity_to_pct("85% of 10k pace")
    except Exception:
        return 85.0

def _max_intensity_pct(workout):
    max_pct = None
    if isinstance(workout, dict):
        inten = workout.get('intensity','')
        if inten:
            try:
                pct = intensity_to_pct(inten)
                if pct is not None:
                    max_pct = pct if max_pct is None else max(max_pct, pct)
            except Exception:
                pass
        if 'am' in workout or 'pm' in workout:
            for key in ('am','pm'):
                sub = workout.get(key)
                if sub is not None:
                    sub_pct = _max_intensity_pct(sub)
                    if sub_pct is not None:
                        max_pct = sub_pct if max_pct is None else max(max_pct, sub_pct)
        if 'segments' in workout and isinstance(workout['segments'], list):
            for seg in workout['segments']:
                sub_pct = _max_intensity_pct(seg)
                if sub_pct is not None:
                    max_pct = sub_pct if max_pct is None else max(max_pct, sub_pct)
        if 'sets' in workout and isinstance(workout['sets'], list):
            for st in workout['sets']:
                sub_pct = _max_intensity_pct(st)
                if sub_pct is not None:
                    max_pct = sub_pct if max_pct is None else max(max_pct, sub_pct)
        if 'sequence' in workout and isinstance(workout['sequence'], list):
            for step in workout['sequence']:
                sub_pct = _max_intensity_pct(step)
                if sub_pct is not None:
                    max_pct = sub_pct if max_pct is None else max(max_pct, sub_pct)
        if 'recovery' in workout and isinstance(workout['recovery'], dict):
            sub_pct = _max_intensity_pct(workout['recovery'])
            if sub_pct is not None:
                max_pct = sub_pct if max_pct is None else max(max_pct, sub_pct)
    elif isinstance(workout, list):
        for item in workout:
            sub_pct = _max_intensity_pct(item)
            if sub_pct is not None:
                max_pct = sub_pct if max_pct is None else max(max_pct, sub_pct)
    return max_pct

def _is_quality_steady(workout):
    pct = _max_intensity_pct(workout)
    return pct is not None and pct >= _quality_threshold_pct()

def _compute_workout_easy_miles(workout):
    """Return (workout_miles, easy_miles) for a workout object."""
    if not workout:
        return 0.0, 0.0
    if isinstance(workout, list):
        w_total = 0.0
        e_total = 0.0
        for item in workout:
            w, e = _compute_workout_easy_miles(item)
            w_total += w
            e_total += e
        return w_total, e_total
    if isinstance(workout, dict):
        if 'am' in workout or 'pm' in workout:
            w_total = 0.0
            e_total = 0.0
            for key in ('am', 'pm'):
                sub = workout.get(key)
                if sub:
                    w, e = _compute_workout_easy_miles(sub)
                    w_total += w
                    e_total += e
            return w_total, e_total
        t = _extract_type(workout)
        miles = compute_day_mileage(workout)
        if _is_rest(workout):
            return 0.0, 0.0
        if _is_workout_like_type(t) or t == 'race':
            return miles, 0.0
        if t == 'steady' and _is_quality_steady(workout):
            return miles, 0.0
        if _is_easy_like_type(t):
            return 0.0, miles
        # Default unknown types to easy mileage for visualization
        return 0.0, miles
    return 0.0, 0.0

def _iter_easy_candidates(w):
    """Yield references to easy-like leaf sessions we can lengthen.
    Returns tuples (container, key) where container[key] is the object to mutate.
    Handles AM/PM containers.
    """
    if not isinstance(w, dict):
        return
    if 'am' in w or 'pm' in w:
        if 'am' in w and isinstance(w['am'], dict) and _is_easy_like_type((w['am'].get('type','') or '').lower()):
            yield (w, 'am')
        if 'pm' in w and isinstance(w['pm'], dict) and _is_easy_like_type((w['pm'].get('type','') or '').lower()):
            yield (w, 'pm')
        return
    t = (w.get('type','') or '').lower()
    if _is_easy_like_type(t):
        yield (None, None)  # indicates mutate w itself

def _add_miles_to_easy_obj(obj, add_miles):
    """Mutate an easy-like object to add mileage by extending duration or distance.
    Prefer extending duration; fall back to distance.
    """
    if not isinstance(obj, dict) or add_miles <= 0:
        return
    intensity = obj.get('intensity','')
    pct = intensity_to_pct(intensity) if intensity else 80.0
    pace = pace_for_intensity(intensity, pct)
    add_min = add_miles * pace
    # If duration present, extend
    if 'duration' in obj and isinstance(obj['duration'], str):
        cur = parse_duration_str(obj['duration']) or 0.0
        obj['duration'] = _fmt_minutes(cur + add_min)
        return
    # Else, extend distance with original unit if present
    ds = obj.get('distance','')
    parsed = parse_distance_str(ds) if isinstance(ds, str) else None
    if parsed:
        ((low_mi, high_mi), unit, (orig_low, orig_high)) = parsed
        # Use high as base if range
        base_mi = high_mi if high_mi else low_mi
        new_mi = base_mi + add_miles
        # Convert to unit
        if unit in ('km','kilometers'):
            amt = new_mi * 1.60934
        elif unit in ('m','meters'):
            amt = new_mi * 1609.34
        else:
            amt = new_mi
        obj['distance'] = _fmt_distance(amt, unit)
    else:
        # If neither, add duration
        obj['duration'] = _fmt_minutes(add_min)

def _add_minutes_to_easy_obj(obj, add_min):
    """Mutate an easy-like object by adding minutes. Prefer duration extension; otherwise
    translate minutes into distance using the entry's intensity to keep units coherent.
    """
    if not isinstance(obj, dict) or add_min <= 0:
        return
    # If this easy-like object is a container with segments, distribute added time
    # across its simple segments proportionally to their current time contribution.
    if 'segments' in obj and isinstance(obj['segments'], list):
        # Build a list of simple segment refs that we can extend
        seg_refs = []  # each item: (segment_dict, kind, current_time_min, pace_min_per_mi)
        total_time = 0.0
        for seg in obj['segments']:
            if 'sets' in seg:
                # Skip complex interval-like structures in easy containers
                continue
            # Determine segment time and pace context
            ds = seg.get('duration') or seg.get('distance')
            intensity = seg.get('intensity', '')
            pct = intensity_to_pct(intensity) if intensity else 80.0
            pace = pace_for_intensity(intensity, pct)
            dmin = parse_duration_str(ds) if isinstance(ds, str) else None
            if dmin is not None:
                seg_time = dmin
                seg_refs.append((seg, 'duration', seg_time, pace))
                total_time += seg_time
            else:
                parsed = parse_distance_str(ds) if isinstance(ds, str) else None
                if parsed:
                    ((l, h), u, o) = parsed
                    miles = h
                    seg_time = miles * pace
                    seg_refs.append((seg, 'distance', seg_time, pace))
                    total_time += seg_time
        if seg_refs and total_time > 0:
            # Distribute add_min proportionally by current time
            for seg, kind, seg_time, pace in seg_refs:
                add_here = add_min * (seg_time / total_time)
                if kind == 'duration':
                    cur = parse_duration_str(seg.get('duration', '0 min')) or 0.0
                    seg['duration'] = _fmt_minutes(cur + add_here)
                else:  # distance
                    ds = seg.get('distance', '')
                    parsed = parse_distance_str(ds)
                    if not parsed:
                        # Fallback: add as duration if distance parsing fails
                        seg['duration'] = _fmt_minutes(add_here)
                    else:
                        ((low_mi, high_mi), unit, (orig_low, orig_high)) = parsed
                        base_mi = high_mi if high_mi else low_mi
                        add_miles = add_here / pace
                        new_mi = base_mi + add_miles
                        if unit in ('km','kilometers'):
                            amt = new_mi * 1.60934
                        elif unit in ('m','meters'):
                            amt = new_mi * 1609.34
                        else:
                            amt = new_mi
                        seg['distance'] = _fmt_distance(amt, unit)
            return
    # Extend duration directly when present
    if 'duration' in obj and isinstance(obj['duration'], str):
        cur = parse_duration_str(obj['duration']) or 0.0
        obj['duration'] = _fmt_minutes(cur + add_min)
        return
    # Otherwise, convert minutes to miles using intensity pace and extend distance
    intensity = obj.get('intensity','')
    pct = intensity_to_pct(intensity) if intensity else 80.0
    pace = pace_for_intensity(intensity, pct)
    add_miles = add_min / pace
    ds = obj.get('distance','')
    parsed = parse_distance_str(ds) if isinstance(ds, str) else None
    if parsed:
        ((low_mi, high_mi), unit, (orig_low, orig_high)) = parsed
        base_mi = high_mi if high_mi else low_mi
        new_mi = base_mi + add_miles
        if unit in ('km','kilometers'):
            amt = new_mi * 1.60934
        elif unit in ('m','meters'):
            amt = new_mi * 1609.34
        else:
            amt = new_mi
        obj['distance'] = _fmt_distance(amt, unit)
    else:
        obj['duration'] = _fmt_minutes(add_min)

def apply_rest_days_and_redistribute(week_entries, target_rest, do_redistribute=True):
    """
    week_entries: list of dicts with keys { 'day_name','date','original','scaled','miles' }
    Mutates 'scaled' in place to convert selected days to rest and optionally
    redistributes removed mileage across remaining easy-like days.
    """
    if target_rest <= 0:
        return
    # Count built-in rest
    builtin = [i for i,e in enumerate(week_entries) if _is_rest(e['scaled']) or _is_rest(e['original'])]
    need = target_rest - len(builtin)
    if need <= 0:
        return
    # Prefer removing easy-like, lowest-mileage days; never remove workouts
    candidates = []
    for i,e in enumerate(week_entries):
        if i in builtin:
            continue
        wt = _extract_type(e['scaled'])
        if _is_workout_like_type(wt) or wt == 'race':
            continue
        if not _is_easy_like_type(wt):
            continue
        candidates.append((e['miles'], i))
    candidates.sort(key=lambda x: x[0])
    pick = [idx for _, idx in candidates[:max(0,need)]]
    if not pick:
        return

    removed_minutes = 0.0
    for idx in pick:
        # Use estimated total time to accurately split minutes when redistributing
        removed_minutes += max(0.0, compute_day_estimated_time_mins(week_entries[idx]['scaled']))
        week_entries[idx]['scaled'] = {'type':'rest'}

    if do_redistribute and removed_minutes > 0.0:
        # Collect easy-like candidates to extend
        recv = []
        for i,e in enumerate(week_entries):
            if i in pick:
                continue
            # Work with copy reference directly
            s = e['scaled']
            for cont,key in _iter_easy_candidates(s):
                if cont is None:
                    recv.append((s, None))
                else:
                    recv.append((cont, key))
        if recv:
            per_min = removed_minutes / len(recv)
            for cont, key in recv:
                if key is None:
                    _add_minutes_to_easy_obj(cont, per_min)
                else:
                    _add_minutes_to_easy_obj(cont[key], per_min)

def _minutes_for_obj(o):
    """Helper: estimated minutes for a single workout object."""
    try:
        return max(0.0, compute_day_estimated_time_mins(o))
    except Exception:
        return 0.0

def redistribute_minutes_from_collapsed_doubles(week_entries):
    """
    If doubles were collapsed (AM/PM -> single session) and an easy session was dropped,
    accumulate the dropped easy minutes (after scaling) and distribute evenly across
    all easy-like sessions in the same week.
    """
    if not redistribute_collapsed_double_minutes:
        return
    removed_minutes = 0.0
    for e in week_entries:
        orig = e.get('original')
        sc = e.get('scaled')
        if not isinstance(orig, dict) or not (('am' in orig) or ('pm' in orig)):
            continue
        # If scaled still has am/pm, doubles were not collapsed
        if isinstance(sc, dict) and (('am' in sc) or ('pm' in sc)):
            continue
        kept_type = _extract_type(sc)
        if not kept_type:
            continue
        # Determine if an easy session was dropped in favor of a workout-like session
        am = orig.get('am') if isinstance(orig.get('am'), dict) else None
        pm = orig.get('pm') if isinstance(orig.get('pm'), dict) else None
        am_t = (am.get('type','') or '').lower() if am else ''
        pm_t = (pm.get('type','') or '').lower() if pm else ''
        if _is_workout_like_type(kept_type):
            # If we kept a workout, add minutes of the easy session that was present originally
            dropped = None
            if am and _is_easy_like_type(am_t):
                dropped = am
            if pm and _is_easy_like_type(pm_t):
                # If both AM and PM are easy-like, then doubles likely were not collapsed by this rule
                # (or combined elsewhere). Prefer counting none in that edge case.
                if dropped is not None:
                    dropped = None
                else:
                    dropped = pm
            if dropped is not None:
                # Use scaled minutes for the dropped easy by running through adjust_workout
                try:
                    scaled_dropped = adjust_workout(dropped)
                except Exception:
                    scaled_dropped = dropped
                removed_minutes += _minutes_for_obj(scaled_dropped)
    if removed_minutes <= 0.0:
        return
    # Distribute evenly across all easy-like recipients in week
    recv = []
    for e in week_entries:
        s = e['scaled']
        for cont,key in _iter_easy_candidates(s):
            if cont is None:
                recv.append((s, None))
            else:
                recv.append((cont, key))
    if not recv:
        return
    per_min = removed_minutes / len(recv)
    for cont, key in recv:
        if key is None:
            _add_minutes_to_easy_obj(cont, per_min)
        else:
            _add_minutes_to_easy_obj(cont[key], per_min)

def _compute_week_miles_with_base_pace(week_entries, base_pace_min_per_mile):
    global pace_override_min_per_mile
    prev = pace_override_min_per_mile
    try:
        pace_override_min_per_mile = base_pace_min_per_mile
        total = 0.0
        for e in week_entries:
            total += compute_day_mileage(e['scaled'])
        return total
    finally:
        pace_override_min_per_mile = prev

def normalize_weekly_miles(week_entries):
    """Scale easy-like sessions proportionally to hit the target weekly total.
    Workouts are preserved; easy-like runs expand/shrink to fit the target."""
    if not normalize_weekly_to_reference:
        return

    def _parse_week_total_miles(week_entries):
        # Prefer explicit week total if present on entries
        total_str = None
        for e in week_entries:
            if e.get('week_meta_total'):
                total_str = e.get('week_meta_total')
                break
        if total_str:
            parsed = parse_distance_str(str(total_str))
            if parsed:
                ((low, high), unit, orig) = parsed
                return (low + high) / 2.0
        # Otherwise compute from original workouts at reference pace
        if reference_hmp_min_per_mile is not None:
            return _compute_week_miles_with_base_pace(
                [{'scaled': e['original']} for e in week_entries],
                reference_hmp_min_per_mile
            )
        return sum(compute_day_mileage(e['original']) for e in week_entries)

    target_base = _parse_week_total_miles(week_entries)
    if target_base is None:
        return
    target_total = target_base * factor

    user_total = sum(compute_day_mileage(e['scaled']) for e in week_entries)
    if user_total <= 0:
        return

    easy_total = 0.0
    other_total = 0.0
    for e in week_entries:
        s = e['scaled']
        t = _extract_type(s)
        miles = compute_day_mileage(s)
        if _is_easy_like_type(t):
            easy_total += miles
        else:
            other_total += miles

    if easy_total <= 0:
        return
    allowed_easy = target_total - other_total
    ratio = allowed_easy / easy_total
    if ratio < 0:
        ratio = 0.0
    mode = (globals().get('normalize_weekly_mode') or "both").strip().lower()
    if mode in ("reduce", "reduce_only", "down", "down_only") and ratio > 1.0:
        # Reduce-only mode: never scale easy up
        return
    if mode in ("increase", "increase_only", "up", "up_only") and ratio < 1.0:
        # Increase-only mode: never scale easy down
        return
    allow_reduce = normalize_reduce_for_fast or workout_scale_factor > easy_scale_factor or mode in ("reduce", "reduce_only", "down", "down_only")
    if ratio < 1.0 and not allow_reduce:
        # Avoid reducing easy for fast athletes unless explicitly enabled,
        # unless workouts are scaled up relative to easy runs.
        return
    if abs(ratio - 1.0) < 0.01:
        return

    def _scale_easy_obj(obj, ratio_val):
        if not isinstance(obj, dict):
            return
        if 'segments' in obj and isinstance(obj['segments'], list):
            for seg in obj['segments']:
                if 'sets' in seg:
                    continue
                ds = seg.get('distance') or seg.get('duration')
                if not ds:
                    continue
                dmin = parse_duration_str(ds) if isinstance(ds, str) else None
                if dmin is not None:
                    new_min = max(1.0, dmin * ratio_val)
                    seg['duration'] = _fmt_minutes(new_min)
                    seg.pop('distance', None)
                else:
                    parsed = parse_distance_str(ds) if isinstance(ds, str) else None
                    if parsed:
                        ((low_mi, high_mi), unit, orig) = parsed
                        new_low = low_mi * ratio_val
                        new_high = high_mi * ratio_val
                        seg['distance'] = format_distance_in_original_unit(new_low, new_high, unit, orig)
                        seg.pop('duration', None)
            return
        ds = obj.get('distance') or obj.get('duration')
        if not ds:
            return
        dmin = parse_duration_str(ds) if isinstance(ds, str) else None
        if dmin is not None:
            new_min = max(1.0, dmin * ratio_val)
            obj['duration'] = _fmt_minutes(new_min)
            obj.pop('distance', None)
        else:
            parsed = parse_distance_str(ds) if isinstance(ds, str) else None
            if parsed:
                ((low_mi, high_mi), unit, orig) = parsed
                new_low = low_mi * ratio_val
                new_high = high_mi * ratio_val
                obj['distance'] = format_distance_in_original_unit(new_low, new_high, unit, orig)
                obj.pop('duration', None)

    for e in week_entries:
        s = e['scaled']
        if _is_easy_like_type(_extract_type(s)):
            _scale_easy_obj(s, ratio)
        elif isinstance(s, dict) and ('am' in s or 'pm' in s):
            for key in ('am', 'pm'):
                sub = s.get(key)
                if isinstance(sub, dict) and _is_easy_like_type((sub.get('type','') or '').lower()):
                    _scale_easy_obj(sub, ratio)

def _remove_minutes_from_easy_obj(obj, sub_min):
    """Decrease an easy-like session by sub_min minutes, with safety floors.
    - For duration entries: do not go below 10 minutes.
    - For distance entries: reduce distance based on intensity pace; do not go below 0.5 mi (or 1 km / 800 m equivalent).
    """
    if not isinstance(obj, dict) or sub_min <= 0:
        return
    # If container with segments, distribute removal proportionally by time
    if 'segments' in obj and isinstance(obj['segments'], list):
        seg_refs = []
        total_time = 0.0
        for seg in obj['segments']:
            if 'sets' in seg:
                continue
            ds = seg.get('duration') or seg.get('distance')
            intensity = seg.get('intensity', '')
            pct = intensity_to_pct(intensity) if intensity else 80.0
            pace = pace_for_intensity(intensity, pct)
            dmin = parse_duration_str(ds) if isinstance(ds, str) else None
            if dmin is not None:
                seg_time = dmin
                seg_refs.append((seg, 'duration', seg_time, pace))
                total_time += seg_time
            else:
                parsed = parse_distance_str(ds) if isinstance(ds, str) else None
                if parsed:
                    ((l, h), u, o) = parsed
                    miles = h
                    seg_time = miles * pace
                    seg_refs.append((seg, 'distance', seg_time, pace))
                    total_time += seg_time
        if seg_refs and total_time > 0:
            for seg, kind, seg_time, pace in seg_refs:
                take_here = sub_min * (seg_time / total_time)
                if kind == 'duration':
                    cur = parse_duration_str(seg.get('duration', '0 min')) or 0.0
                    seg['duration'] = _fmt_minutes(max(10.0, cur - take_here))
                else:
                    ds = seg.get('distance', '')
                    parsed = parse_distance_str(ds)
                    if not parsed:
                        # If we cannot parse distance, switch to duration floor handling
                        seg['duration'] = _fmt_minutes(max(10.0, 0.0 - take_here))
                    else:
                        ((low_mi, high_mi), unit, (orig_low, orig_high)) = parsed
                        base_mi = high_mi if high_mi else low_mi
                        sub_miles = take_here / pace
                        new_mi = max(0.5, base_mi - sub_miles)
                        if unit in ('km','kilometers'):
                            amt = new_mi * 1.60934
                        elif unit in ('m','meters'):
                            amt = max(800, new_mi * 1609.34)
                        else:
                            amt = new_mi
                        seg['distance'] = _fmt_distance(amt, unit)
            return
    # If duration present
    if 'duration' in obj and isinstance(obj['duration'], str):
        cur = parse_duration_str(obj['duration']) or 0.0
        new_val = max(10.0, cur - sub_min)
        obj['duration'] = _fmt_minutes(new_val)
        return
    # Else distance-based
    intensity = obj.get('intensity','')
    pct = intensity_to_pct(intensity) if intensity else 80.0
    pace = pace_for_intensity(intensity, pct)
    sub_miles = sub_min / pace
    ds = obj.get('distance','')
    parsed = parse_distance_str(ds) if isinstance(ds, str) else None
    if parsed:
        ((low_mi, high_mi), unit, (orig_low, orig_high)) = parsed
        base_mi = high_mi if high_mi else low_mi
        new_mi = max(0.5, base_mi - sub_miles)
        if unit in ('km','kilometers'):
            amt = new_mi * 1.60934
        elif unit in ('m','meters'):
            amt = max(800, new_mi * 1609.34)
        else:
            amt = new_mi
        obj['distance'] = _fmt_distance(amt, unit)


def next_serial_paths(base_name: str, out_dir: str):
    """Return unique serial filepaths for .ics and .html in out_dir.
    Files are named like '<base_name>_001.ics' and '.html'.
    """
    os.makedirs(out_dir, exist_ok=True)
    # Find existing serials
    pattern = re.compile(rf"^{re.escape(base_name)}_(\d+)\.ics$", re.IGNORECASE)
    max_n = 0
    try:
        for fname in os.listdir(out_dir):
            m = pattern.match(fname)
            if m:
                try:
                    n = int(m.group(1))
                    max_n = max(max_n, n)
                except ValueError:
                    pass
    except FileNotFoundError:
        pass
    n = max_n + 1
    stem = f"{base_name}_{n:03d}"
    return os.path.join(out_dir, stem + ".ics"), os.path.join(out_dir, stem + ".html")

# -------------------------------
# Calendar generation

def main(*, return_paths: bool = False, open_browser: bool = True, home_url: str = None,
         preview_token: str = None, generate_fits: bool = False,
         include_easy_fits: bool = False, fit_targets_enabled: bool = True,
         fit_target_mode: str = "pace", fit_target_margin: int = 30,
         return_fit_files: bool = False, return_fit_schedule: bool = False):
    global CURRENT_PHASE
    global factor
    global easy_scale_factor
    global workout_scale_factor
    
    with open(input_json_file, "r") as f:
        data = json.load(f)

    fit_files = []
    fit_schedule = {}
    fit_name_counts = {}

    # Determine baseline reference peak from the plan itself (if provided),
    # otherwise fall back to 50 mi reference.
    plan_meta = data.get('plan_meta', {})
    baseline_peak = get_plan_reference_peak(plan_meta)
    if baseline_peak is None:
        baseline_peak = 50.0
    factor = peak_mileage / float(baseline_peak)
    easy_scale_factor = factor
    # Workout factor: mode decides which source of truth to use
    wf = None
    mode = (workout_factor_mode or "same").strip().lower()
    if mode in ("same", "plan"):
        # Keep current plan/base behavior
        wf = _plan_workout_factor(plan_meta, factor)
    elif mode in ("normalize", "peak", "base"):
        # Normalize workouts to peak mileage ratio
        wf = factor
    elif mode in ("custom", "override"):
        # Custom multiplier of original plan (1.0 = original)
        if workout_factor_override is not None:
            try:
                wf = float(workout_factor_override)
            except Exception:
                wf = None
        if wf is None and workout_factor_multiplier is not None:
            try:
                wf = float(workout_factor_multiplier)
            except Exception:
                wf = None
        if wf is None:
            wf = factor
    elif mode in ("mult", "user"):
        # Legacy: multiplier on base factor
        if workout_factor_multiplier is not None:
            try:
                wf = factor * float(workout_factor_multiplier)
            except Exception:
                wf = None
        if wf is None and workout_factor_override is not None:
            try:
                wf = float(workout_factor_override)
            except Exception:
                wf = None
        if wf is None:
            wf = factor
    elif mode == "original":
        # Keep workouts at original plan scale (no scaling)
        wf = 1.0
    else:
        wf = factor
    workout_scale_factor = wf

    # Resolve race distance (GUI override wins; else plan meta; else default)
    plan_race = normalize_race_distance(
        plan_meta.get('race_distance') or plan_meta.get('race') or plan_meta.get('race_length') or plan_meta.get('goal')
    )
    resolved_race = normalize_race_distance(globals().get('race_distance')) or plan_race or _DEFAULT_RACE_DISTANCE
    globals()['race_distance'] = resolved_race

    # Establish reference race pace baseline for cross-pace scaling/normalization.
    # Default baseline: 5:10/mi (5.1667 min/mi). Plan meta can override via
    # 'reference_race_pace' / 'current_race_pace' or legacy 'reference_HMP' / 'current_HMP'.
    dist_key = race_distance_key(resolved_race)
    ref_pace_text = (
        plan_meta.get('reference_race_pace')
        or plan_meta.get('reference_pace')
        or plan_meta.get(f'reference_{dist_key}')
        or plan_meta.get('reference_HMP')
        or plan_meta.get('current_race_pace')
        or plan_meta.get(f'current_{dist_key}')
        or plan_meta.get('current_HMP')
        or "5:10/mi"
    )
    ref_parsed = parse_pace_str(str(ref_pace_text)) if ref_pace_text else None
    globals()['reference_hmp_min_per_mile'] = ref_parsed if ref_parsed else (5 + 10/60)

    # Optional easy-pace override from plan meta (only if not set by user/GUI)
    if globals().get('easy_pace_min_per_mile') is None:
        easy_text = plan_meta.get('easy_pace') or plan_meta.get('easy_pace_min_per_mile')
        easy_parsed = _parse_pace_value(easy_text)
        if easy_parsed:
            globals()['easy_pace_min_per_mile'] = easy_parsed

    # Plan-level override for weekly normalization
    norm_override = plan_meta.get('normalize_weekly_to_reference')
    if norm_override is True or norm_override is False:
        globals()['normalize_weekly_to_reference'] = bool(norm_override)
    # Plan-level override for normalization mode (both/reduce_only/increase_only)
    norm_mode = plan_meta.get('normalize_weekly_mode')
    if isinstance(norm_mode, str) and norm_mode.strip():
        globals()['normalize_weekly_mode'] = norm_mode.strip().lower()
    # Plan-level override for reducing easy mileage when normalizing
    norm_reduce = plan_meta.get('normalize_reduce_for_fast')
    if norm_reduce is True or norm_reduce is False:
        globals()['normalize_reduce_for_fast'] = bool(norm_reduce)
    # Plan-level override for range distance preference (high/midpoint/low)
    range_pref = plan_meta.get('range_distance_preference') or plan_meta.get('range_distance_mode')
    if isinstance(range_pref, str) and range_pref.strip():
        globals()['range_distance_preference'] = range_pref.strip().lower()

    # Optionally auto-disable implicit WU/CD when plan says warmups are explicit
    explicit_wu = plan_meta.get('explicit_warmups')
    if explicit_wu is True:
        # turn off implicit WU/CD additions when plan encodes WU/CD
        globals()['include_implicit_wu_cd'] = False

    # Plan-level override to scale explicit warmups/cooldowns
    if plan_meta.get('scale_warmups') is True:
        globals()['scale_wu_cd_segments'] = True

    # Collapse doubles if requested at plan level
    if plan_meta.get('collapse_doubles') is True or plan_meta.get('disable_doubles') is True:
        globals()['collapse_doubles'] = True

    # Compute dynamic start_date so that Week 1 Monday aligns relative to the final Saturday race
    weeks_count = max(1, len(data.get('weeks', [])))
    days_back = (weeks_count - 1) * 7 + 5
    # shadow the global with a local binding for this run
    start_date = race_date - timedelta(days=days_back)

    cal = Calendar()
    race_label = race_distance_display(resolved_race)
    pace_label = race_pace_label(resolved_race)
    cal_title = f"{race_label} training / {peak_mileage} mi peak / {race_pace_min_per_mile:.2f} min per mi {pace_label}"
    cal.name = cal_title
    cal.extra.append(ContentLine(
        name="X-WR-CALNAME",
        params={},
        value=cal_title
    ))
    # Calendar-level metadata for downstream tools
    try:
        cal.extra.append(ContentLine(name="X-RACE-DIST", params={}, value=str(resolved_race)))
        cal.extra.append(ContentLine(name="X-RACE-PACE", params={}, value=f"{race_pace_min_per_mile:.5f}"))
    except Exception:
        pass

    weekly_overview = []
    local_tz = datetime.now().astimezone().tzinfo

    for w in data['weeks']:
        week_num = w['week']
        phase = get_phase(week_num)
        CURRENT_PHASE = phase  # Set global phase for intelligent scaling
        week_start = start_date + timedelta(days=(week_num - 1) * 7)
        week_notes = w.get('notes', '')
        # Prepare all days first so we can choose rest days and redistribute
        weekday_map = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
                       "Friday": 4, "Saturday": 5, "Sunday": 6,
                       "Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
        week_entries = []
        for d in w['days']:
            day_name = d['day']
            wday_idx = weekday_map[day_name]
            day_date = week_start + timedelta(days=wday_idx)
            original_workout = d.get('workout')
            if original_workout is None:
                original_workout = {'type': 'rest'}

            # Enable optional doubles from plan meta
            if plan_meta.get('enable_optional_doubles') is True:
                globals()['enable_optional_doubles'] = True

            effective_workout = maybe_split_optional_double(original_workout)
            scaled_workout = adjust_workout(effective_workout)
            daily_miles = compute_day_mileage(scaled_workout)
            week_entries.append({
                'day_name': day_name,
                'date': day_date,
                'original': original_workout,
                'scaled': scaled_workout,
                'miles': daily_miles,
                'week_meta_total': w.get('total_mileage', ''),
            })

        # Apply rest-day rules and redistribution
        apply_rest_days_and_redistribute(week_entries, rest_days_per_week_target, do_redistribute=redistribute_removed_load)

        # If doubles were collapsed and an easy session dropped, redistribute its minutes
        redistribute_minutes_from_collapsed_doubles(week_entries)

        # Normalize weekly miles across paces by adding time to easy runs if needed
        normalize_weekly_miles(week_entries)

        weekly_total = 0.0
        weekly_minutes_total = 0.0
        weekly_workout_miles = 0.0
        weekly_easy_miles = 0.0
        for e in week_entries:
            day_name = e['day_name']
            day_date = e['date']
            original_workout = e['original']
            scaled_workout = e['scaled']
            daily_miles = compute_day_mileage(scaled_workout)
            day_workout_miles, day_easy_miles = _compute_workout_easy_miles(scaled_workout)
            if daily_miles > 0 and abs((day_workout_miles + day_easy_miles) - daily_miles) > 0.01:
                day_easy_miles += (daily_miles - (day_workout_miles + day_easy_miles))
            if day_easy_miles < 0:
                day_workout_miles = max(0.0, day_workout_miles + day_easy_miles)
                day_easy_miles = 0.0

            # Build simplified description for scaled, plus original plan
            scaled_simple_txt = workout_to_simplified_string(scaled_workout, html=False)
            scaled_simple_html = workout_to_simplified_string(scaled_workout, html=True)
            orig_txt = workout_to_string_original(original_workout)
            desc = f"{scaled_simple_txt}\n(Original plan: {orig_txt})"

            include_wu = include_implicit_wu_cd and is_wu_cd_workout(scaled_workout)
            if include_wu:
                desc += "\nNote: estimated time includes ~1.0 mi warmup/cooldown at 9:15/mi"

            ev = Event()
            total_time_min = compute_day_estimated_time_mins(scaled_workout)
            if total_time_min <= 0.0:
                total_time_min = estimate_total_time_mins(daily_miles, include_wu_cd=include_wu)
            time_str = format_time_hhmm(total_time_min)

            ev.name = f"{create_event_name(scaled_workout, daily_mileage=daily_miles)} (~{time_str})"
            ev.begin = datetime(day_date.year, day_date.month, day_date.day, 18, 0, 0, tzinfo=local_tz)
            ev.end   = datetime(day_date.year, day_date.month, day_date.day, 19, 0, 0, tzinfo=local_tz)
            ev.description = f"{desc}\nEstimated total time: {time_str}"
            try:
                ev._scaled_workout_dict = scaled_workout
                ev._original_workout_dict = original_workout
                ev._week_notes = week_notes
                wu_note_html = "<br/>Note: estimated time includes ~1.0 mi warmup/cooldown at 9:15/mi" if include_wu else ""
                ev._desc_html = (
                    f"{scaled_simple_html}{wu_note_html}<br/>" +
                    html.escape(f"(Original plan: {orig_txt})") +
                    f"<br/>Estimated total time: {time_str}"
                )
                # Embed structured workout JSON so we can reconstruct steps later from ICS
                try:
                    payload = json.dumps(scaled_workout, separators=(",", ":"), ensure_ascii=True)
                    ev.extra.append(ContentLine(name="X-WORKOUT", params={}, value=payload))
                    # Legacy + new metadata for pace/race distance
                    ev.extra.append(ContentLine(name="X-HMP", params={}, value=f"{race_pace_min_per_mile:.5f}"))
                    ev.extra.append(ContentLine(name="X-RACE-PACE", params={}, value=f"{race_pace_min_per_mile:.5f}"))
                    ev.extra.append(ContentLine(name="X-RACE-DIST", params={}, value=str(resolved_race)))
                except Exception:
                    pass
                wtype = _extract_type(scaled_workout)
                if wtype:
                    if _is_workout_like_type(wtype):
                        ev._category = 'workout'
                    elif wtype == 'steady' and _is_quality_steady(scaled_workout):
                        ev._category = 'workout'
                    elif _is_easy_like_type(wtype):
                        ev._category = 'easy'
                if _is_rest(scaled_workout):
                    ev._category = 'rest'
            except Exception:
                pass
            try:
                if generate_fits:
                    wtype = _extract_type(scaled_workout)
                    if not _is_rest(scaled_workout) and (not _is_easy_like_type(wtype) or include_easy_fits):
                        fit_title = create_event_name(scaled_workout, daily_mileage=daily_miles)
                        fit_name = _safe_fit_filename(day_date, fit_title, fit_name_counts)
                        fit_bytes = _export_fit_bytes(
                            os.path.splitext(fit_name)[0],
                            scaled_workout,
                            targets_enabled=fit_targets_enabled,
                            target_mode=fit_target_mode,
                            target_margin=fit_target_margin,
                            pace_min_per_mile=race_pace_min_per_mile,
                            include_implicit_wu_cd=include_implicit_wu_cd,
                        )
                        fit_files.append((fit_name, fit_bytes))
                        fit_schedule[fit_name] = day_date
                        fit_graph = _fit_graph_bytes(fit_bytes)
                        ev._fit_name = fit_name
                        if fit_graph:
                            ev._fit_graph = fit_graph
            except Exception:
                pass
            cal.events.add(ev)

            weekly_total += daily_miles
            weekly_minutes_total += float(total_time_min or 0.0)
            weekly_workout_miles += float(day_workout_miles or 0.0)
            weekly_easy_miles += float(day_easy_miles or 0.0)

        add_weekly_summary(cal, week_start, phase, week_num, weekly_total, w.get('total_mileage', ""), total_minutes=weekly_minutes_total)
        try:
            last_ev = list(cal.events)[-1]
            if isinstance(last_ev, Event):
                last_ev._week_notes = week_notes
        except Exception:
            pass
        # Build per-day summaries for the mini week calendar
        day_summaries = []
        weekday_order = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
        def _cat_for(w):
            t = _extract_type(w)
            if _is_rest(w):
                return "rest"
            if _is_workout_like_type(t) or t == "race":
                return "workout"
            if t == "steady" and _is_quality_steady(w):
                return "workout"
            if _is_easy_like_type(t):
                return "easy"
            return "easy"
        def _day_anchor_id_local(dt):
            try:
                return f"day-{dt.strftime('%Y-%m-%d')}"
            except Exception:
                return ""
        for dname in weekday_order:
            match = next((e for e in week_entries if e['day_name'] == dname), None)
            if not match:
                continue
            miles = round(compute_day_mileage(match['scaled']), 1)
            minutes = round(compute_day_estimated_time_mins(match['scaled']), 1)
            day_id = _day_anchor_id_local(match['date'])
            day_summaries.append({
                "label": dname[:3],
                "miles": miles,
                "minutes": minutes,
                "category": _cat_for(match['scaled']),
                "day_id": day_id,
            })
        weekly_overview.append({
            'week': week_num,
            'miles': round(weekly_total, 1),
            'minutes': round(weekly_minutes_total, 1),
            'workout_miles': round(weekly_workout_miles, 1),
            'easy_miles': round(weekly_easy_miles, 1),
            'days': day_summaries
        })

    add_race_day_event(cal, race_date)

    # Resolve output directory 'Calendars' next to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(script_dir, 'Calendars')

    # Derive base name from configured output_ics_file, then pick next serial
    base_name = Path(output_ics_file).stem
    ics_path, html_path = next_serial_paths(base_name, out_dir)

    # Write ICS
    with open(ics_path, "w") as f:
        f.writelines(cal)

    # Write HTML preview and auto-open it in the default browser
    write_html_preview(cal, html_path, weekly_overview, home_url=home_url, preview_token=preview_token)
    print(f"Wrote ICS: {ics_path}\nWrote preview: {html_path}")
    if open_browser:
        try:
            webbrowser.open_new_tab('file://' + os.path.abspath(html_path))
        except Exception as ex:
            print(f"[preview] Could not auto-open preview: {ex}")
    if return_fit_files and return_fit_schedule:
        return ics_path, html_path, fit_files, fit_schedule
    if return_fit_files:
        return ics_path, html_path, fit_files
    if return_paths:
        return ics_path, html_path

if __name__ == "__main__":
    main()
