#!/usr/bin/env python3
"""
Final specification-compliant FIT export based on official Garmin FIT Guide
and analysis of working files. This addresses all identified issues.
"""

import os
import json
import re
from datetime import datetime
import re
from typing import List, Dict, Any, Optional
import zlib

FIT_TOOL_AVAILABLE = False
WorkoutStepIntensity = None  # optional, not present in some versions
try:
    from fit_tool.fit_file_builder import FitFileBuilder
    from fit_tool.profile.messages.file_id_message import FileIdMessage, FileType
    from fit_tool.profile.messages.sport_message import SportMessage
    from fit_tool.profile.messages.workout_message import WorkoutMessage
    from fit_tool.profile.messages.workout_step_message import (
        WorkoutStepMessage,
        WorkoutStepDuration,
        WorkoutStepTarget,
    )
    try:
        # Optional in some fit_tool versions; we fall back to raw ints if missing
        from fit_tool.profile.messages.workout_step_message import WorkoutStepIntensity as _WSI
        WorkoutStepIntensity = _WSI
    except Exception:
        WorkoutStepIntensity = None
    FIT_TOOL_AVAILABLE = True
except Exception:
    FIT_TOOL_AVAILABLE = False


def _sanitize_ascii(text: Optional[str], max_len: int) -> str:
    """Return ASCII-only text trimmed to max_len. Replaces unsupported chars with simple equivalents."""
    if not text:
        return ""
    # Replace common unicode dashes and quotes
    repl = (
        ("\u2013", "-"),  # en dash
        ("\u2014", "-"),  # em dash
        ("\u00A0", " "),  # nbsp
        ("\u2018", "'"), ("\u2019", "'"),  # quotes
        ("\u201C", '"'), ("\u201D", '"'),
        ("\u223C", "~"),  # tilde
        ("\u2264", "<="), ("\u2265", ">="),
    )
    s = str(text)
    for a, b in repl:
        s = s.replace(a, b)
    # Strip to ASCII only
    s = s.encode("ascii", "ignore").decode("ascii")
    # Collapse excessive whitespace
    s = " ".join(s.split())
    return s[:max_len]


def _fit_simple_name(text: Optional[str], max_len: int = 32) -> str:
    """Simplify workout name for device compatibility.

    - ASCII only (via _sanitize_ascii)
    - Drop parentheses and their contents
    - Remove tildes and most punctuation; allow letters, digits, spaces, hyphens
    - Collapse spaces and trim to max_len
    """
    base = _sanitize_ascii(text or "", 128)
    # Drop parentheses content anywhere
    base = re.sub(r"\s*\([^)]*\)", "", base)
    # Remove tildes
    base = base.replace("~", "")
    # Allow only letters, digits, space, hyphen, decimal point
    base = re.sub(r"[^A-Za-z0-9 \-.]", "", base)
    # Collapse whitespace
    base = " ".join(base.split())
    return base[:max_len] if base else "Workout"


def _mps_to_mmss(mps: float) -> str:
    """Convert meters/second to mm:ss string per mile."""
    if not mps or mps <= 0:
        return ""
    min_per_mile = 26.8224 / float(mps)
    mins = int(min_per_mile)
    secs = int(round((min_per_mile - mins) * 60))
    if secs == 60:
        mins += 1
        secs = 0
    return f"{mins}:{secs:02d}"


def export_spec_compliant_fit_workout(
    workout_name: str,
    steps: List[Dict[str, Any]],
    out_path: str,
    estimated_miles: Optional[float] = None,
    deterministic: bool = False,
    deterministic_seed: Optional[str] = None,
    deterministic_time_created: Optional[int] = None,
) -> None:
    """Export a FIT workout file that exactly matches Garmin specification and working examples."""
    if not FIT_TOOL_AVAILABLE:
        raise RuntimeError("fit_tool not installed. Install with: pip install fit_tool")

    b = FitFileBuilder(auto_define=True, min_string_size=32)

    # File ID message - EXACTLY like working files
    fid = FileIdMessage()
    fid.type = FileType.WORKOUT  # This should be 5 (workout) not 4
    fid.manufacturer = 1  # Garmin
    
    # Use CONNECT product ID exactly like working files
    try:
        fid.product = 65534  # CONNECT product ID
        # Some versions use garmin_product field
        setattr(fid, 'garmin_product', 65534)
    except Exception:
        try:
            fid.product = 65534
        except Exception:
            pass
    
    # Set creation time + serial number.
    # Deterministic mode is useful when users want byte-stable exports for unchanged inputs.
    if deterministic:
        try:
            if deterministic_seed is not None:
                seed_text = str(deterministic_seed)
            else:
                seed_text = json.dumps(
                    {"workout_name": workout_name, "steps": steps},
                    sort_keys=True,
                    separators=(",", ":"),
                )
            fid.serial_number = int(zlib.crc32(seed_text.encode("utf-8")) & 0xFFFFFFFF)
        except Exception:
            try:
                fid.serial_number = 1
            except Exception:
                pass
        try:
            fid.time_created = int(
                deterministic_time_created
                if deterministic_time_created is not None
                else 946684800
            )
        except Exception:
            pass
    else:
        try:
            fid.time_created = int(datetime.now().timestamp())
        except Exception:
            pass
        try:
            fid.serial_number = abs(hash(out_path)) % (10**9)
        except Exception:
            pass
    
    b.add(fid)

    # Minimal message set for Workout files per spec: FileId, Workout, WorkoutStep(s).
    # Some devices are picky about unexpected messages; omit optional ones for compatibility.

    # Workout message - ensure all fields match working files
    wm = WorkoutMessage()
    # CRITICAL: Use a very simple ASCII-only name without punctuation/parentheses
    clean_name = _fit_simple_name(workout_name, 32)
    wm.wkt_name = clean_name
    # Some fit_tool versions expose this as 'workout_name' – set both
    try:
        setattr(wm, 'workout_name', clean_name)
    except Exception:
        pass
    wm.num_valid_steps = len(steps)
    
    # Set sport fields exactly like working files
    try:
        wm.sport = 1  # running
        wm.sub_sport = 0  # generic
    except Exception:
        pass
    
    # Set capabilities exactly like working files
    try:
        from fit_tool.profile.messages.workout_message import WorkoutCapabilities
        wm.capabilities = WorkoutCapabilities.TCX.value  # Should be 32
    except Exception:
        try:
            wm.capabilities = 32  # TCX capability
        except Exception:
            pass
    
    b.add(wm)

    # Workout steps - fix ALL issues based on working file analysis
    for i, st in enumerate(steps, start=1):
        ws = WorkoutStepMessage()

        # Message index
        try:
            ws.message_index = i - 1
        except Exception:
            pass

        repeat_meta = None
        if isinstance(st, dict):
            if "repeat" in st and isinstance(st["repeat"], dict):
                repeat_meta = dict(st["repeat"])
            elif (st.get("type") or "").lower() == "repeat":
                repeat_meta = {}

        if repeat_meta is not None:
            block_len = int(repeat_meta.get("block_len") or repeat_meta.get("steps") or 0)
            repeat_count = int(repeat_meta.get("count") or repeat_meta.get("repeat_count") or 0)
            start_index = repeat_meta.get("start_index")
            if start_index is None:
                # Default to repeating the immediately preceding block.
                start_index = max(0, (i - 1) - (block_len if block_len > 0 else 1))
            if block_len <= 0:
                block_len = max(1, (i - 1) - start_index)
            repeat_count = max(1, repeat_count)

            try:
                ws.duration_type = WorkoutStepDuration.REPEAT_UNTIL_STEPS_CMPLT
            except Exception:
                ws.duration_type = 6

            try:
                ws.duration_step = int(start_index)
            except Exception:
                pass

            try:
                ws.target_repeat_steps = int(repeat_count)
            except Exception:
                # As a fallback write raw target_value
                try:
                    ws.target_value = int(repeat_count)
                except Exception:
                    pass

            b.add(ws)
            continue

        # Keep steps minimal; avoid setting per-step names for compatibility

        # Intensity mapping FIRST so target overrides for WU/CD/Rest are reliable
        step_type = (st.get('type') or '').lower()
        desc = (st.get('description') or '').lower()
        blob = step_type + ' ' + desc
        if WorkoutStepIntensity is not None:
            try:
                if 'warmup' in blob or ('warm ' in blob and 'down' not in blob):
                    ws.intensity = WorkoutStepIntensity.WARMUP
                elif 'cooldown' in blob or ('cool ' in blob and 'down' in blob):
                    ws.intensity = WorkoutStepIntensity.COOLDOWN
                elif any(k in blob for k in ['recovery', 'rest', 'jog']):
                    ws.intensity = WorkoutStepIntensity.REST
                else:
                    ws.intensity = WorkoutStepIntensity.ACTIVE
            except Exception:
                # Fall through to raw ints
                pass
        if getattr(ws, 'intensity', None) is None:
            if 'warmup' in blob or ('warm ' in blob and 'down' not in blob):
                ws.intensity = 2
            elif 'cooldown' in blob or ('cool ' in blob and 'down' in blob):
                ws.intensity = 3
            elif any(k in blob for k in ['recovery', 'rest', 'jog']):
                ws.intensity = 1
            else:
                ws.intensity = 0

        # Duration mapping - CRITICAL FIX based on working files
        end_type = (st.get('endCondition') or '').upper()
        if end_type == 'TIME':
            ws.duration_type = WorkoutStepDuration.TIME
            # Assign milliseconds so decode shows seconds (matches working files)
            duration_seconds = float(st.get('endConditionValue') or 0)
            ws.duration_time = duration_seconds * 1000.0
        elif end_type == 'DISTANCE':
            ws.duration_type = WorkoutStepDuration.DISTANCE
            # Assign meters/10 so raw=meters*100 and decode shows meters
            meters = float(st.get('endConditionValue') or 0)
            ws.duration_distance = meters / 10.0
        else:
            ws.duration_type = WorkoutStepDuration.OPEN

        # Target mapping - CRITICAL FIX based on working files
        ttype = (st.get('targetType') or 'NO_TARGET').upper()

        def _speed_to_raw(val: Optional[float]) -> Optional[int]:
            """Return FIT raw speed (int, m/s * 1000) from various inputs.

            Accepts:
            - m/s (typical: 1.0–8.0) → raw = m/s*1000
            - accidental tiny values (e.g., 0.003 m/s) → treat as m/s/1000 and fix: raw = val*1e6
            - accidental raw-like inputs (e.g., 2900) → pass-through if > 50
            """
            if val is None:
                return None
            try:
                v = float(val)
            except Exception:
                return None
            if v <= 0:
                return None
            # If looks like already raw (e.g., > 50), accept as-is
            if v > 50.0:
                return int(round(v))
            # If unreasonably tiny (e.g., 0.003), assume m/s was scaled by 1/1000 upstream
            if v < 0.5:
                return int(round(v * 1_000_000.0))  # (v*1000 m/s) * 1000 raw
            # Normal m/s → raw
            return int(round(v * 1000.0))
        if ttype == 'SPEED':
            ws.target_type = WorkoutStepTarget.SPEED
            low = st.get('targetValueLow')
            high = st.get('targetValueHigh')
            if low is not None and high is not None:
                low_raw = _speed_to_raw(low)
                high_raw = _speed_to_raw(high)
                if low_raw is not None and high_raw is not None and high_raw >= low_raw:
                    ws.custom_target_speed_low = low_raw
                    ws.custom_target_speed_high = high_raw
                    # Explicitly mark custom range via zone 0 on id=4
                    try:
                        # Preferred dynamic alias if available
                        setattr(ws, 'target_speed_zone', 0)
                    except Exception:
                        try:
                            ws.target_value = 0
                        except Exception:
                            pass
                else:
                    # Fallback to open target if bounds invalid
                    ws.target_type = WorkoutStepTarget.OPEN
                    try:
                        ws.target_value = 0
                    except Exception:
                        pass
                # Avoid setting target_speed_zone for maximal compatibility
            else:
                ws.target_type = WorkoutStepTarget.OPEN
                try:
                    ws.target_value = 0
                except Exception:
                    pass
        elif ttype == 'PACE':
            ws.target_type = WorkoutStepTarget.SPEED
            low = st.get('targetValueLow')
            high = st.get('targetValueHigh')
            if low is not None and high is not None:
                # Convert sec/km to m/s and then to RAW units (m/s * 1000)
                try:
                    ms_low = 1000.0 / float(low)
                    ms_high = 1000.0 / float(high)
                    low_raw = _speed_to_raw(ms_low)
                    high_raw = _speed_to_raw(ms_high)
                except Exception:
                    low_raw = None
                    high_raw = None
                if low_raw is not None and high_raw is not None and high_raw >= low_raw:
                    ws.custom_target_speed_low = low_raw
                    ws.custom_target_speed_high = high_raw
                    # Explicitly mark custom via zone 0
                    try:
                        setattr(ws, 'target_speed_zone', 0)
                    except Exception:
                        try:
                            ws.target_value = 0
                        except Exception:
                            pass
                else:
                    ws.target_type = WorkoutStepTarget.OPEN
                    try:
                        ws.target_value = 0
                    except Exception:
                        pass
                # Avoid setting target_speed_zone for maximal compatibility
            else:
                ws.target_type = WorkoutStepTarget.OPEN
                try:
                    ws.target_value = 0
                except Exception:
                    pass
        else:
            # Working files show target_type: open (raw: 2)
            ws.target_type = WorkoutStepTarget.OPEN
            try:
                ws.target_value = 0
            except Exception:
                pass

        # Force open targets for warmup/cooldown/recovery to mimic working files
        try:
            intensity_val = getattr(ws, 'intensity', None)
            # Enum-safe check: WARMUP=2, COOLDOWN=3, REST=1 in most profiles
            if intensity_val in (1, 2, 3) or (
                hasattr(intensity_val, 'name') and intensity_val.name in ('REST', 'WARMUP', 'COOLDOWN')
            ):
                ws.target_type = WorkoutStepTarget.OPEN
                try:
                    ws.target_value = 0
                except Exception:
                    pass
                # Clear any custom targets if previously set
                for fld in (
                    'custom_target_speed_low', 'custom_target_speed_high',
                    'custom_target_value_low', 'custom_target_value_high'
                ):
                    try:
                        setattr(ws, fld, None)
                    except Exception:
                        pass
        except Exception:
            pass

        # Add short pace notes based on target speeds (device-friendly)
        try:
            # If custom speed bounds exist, compute a single target pace (midpoint)
            lo_raw = getattr(ws, 'custom_target_speed_low', None)
            hi_raw = getattr(ws, 'custom_target_speed_high', None)
            def _to_mps(x):
                if x is None:
                    return None
                val = float(x)
                return val / 1000.0 if val > 50.0 else val
            lo_mps = _to_mps(lo_raw)
            hi_mps = _to_mps(hi_raw)
            note = ""
            if lo_mps and hi_mps:
                mid = (lo_mps + hi_mps) / 2.0
                note = _mps_to_mmss(mid)
            elif lo_mps:
                note = _mps_to_mmss(lo_mps)
            elif hi_mps:
                note = _mps_to_mmss(hi_mps)
            if note:
                try:
                    ws.notes = _sanitize_ascii(note, 32)
                except Exception:
                    pass
        except Exception:
            pass

        # Keep step payload minimal to mimic working files (no extra notes/weight fields)

        b.add(ws)

    # Build and write the file
    fit_file = b.build()
    with open(out_path, 'wb') as f:
        f.write(fit_file.to_bytes())


def generate_final_fit_files():
    """Generate final specification-compliant FIT files."""
    
    # Import the main script functions
    import sys
    sys.path.append('.')
    from hm_plan_to_garmin import (
        main, workout_to_garmin_steps, compute_obj_miles_minutes,
        adjust_workout, get_phase, RACE_DATE, INPUT_JSON_FILE
    )
    
    # Load the plan
    with open(INPUT_JSON_FILE, "r") as f:
        data = json.load(f)
    
    # Set up globals
    import hm_plan_to_garmin as hm
    hm.FACTOR = 1.0  # Use default scaling
    
    from datetime import date, timedelta
    
    weeks_count = max(1, len(data.get('weeks', [])))
    days_back = (weeks_count - 1) * 7 + 5
    start_date = RACE_DATE - timedelta(days=days_back)
    
    spec_dir = "fit_out_spec_compliant"
    os.makedirs(spec_dir, exist_ok=True)
    
    count = 0
    for w in data['weeks']:
        week_num = int(w['week'])
        hm.CURRENT_PHASE = get_phase(week_num)
        week_start_date = start_date + timedelta(days=(week_num - 1) * 7)
        
        for d in w['days']:
            day_name = d['day']
            weekday_map = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, 
                          "Friday": 4, "Saturday": 5, "Sunday": 6,
                          "Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
            wday_idx = weekday_map[day_name]
            day_date = week_start_date + timedelta(days=wday_idx)

            original_workout = d['workout']
            scaled_workout = adjust_workout(original_workout)

            # Handle AM/PM sessions
            sessions = []
            if isinstance(scaled_workout, dict) and ('am' in scaled_workout or 'pm' in scaled_workout):
                if 'am' in scaled_workout:
                    name = f"W{week_num} {day_name} AM"
                    sessions.append((name, scaled_workout['am']))
                if 'pm' in scaled_workout:
                    name = f"W{week_num} {day_name} PM"
                    sessions.append((name, scaled_workout['pm']))
            else:
                name = f"W{week_num} {day_name}"
                workout_obj = scaled_workout if isinstance(scaled_workout, dict) else {"type": "easy", "duration": "45 min"}
                sessions.append((name, workout_obj))

            for ses_name, ses_obj in sessions:
                steps = workout_to_garmin_steps(ses_obj)
                # Skip empty/rest days
                if not steps:
                    continue
                mi, mins = compute_obj_miles_minutes(ses_obj)

                # Sanitize file name component (no parentheses/tilde/commas, safe chars only)
                def _safe_file_component(text: str, max_len: int = 80) -> str:
                    s = (text or "").strip()
                    s = re.sub(r"\s*\([^)]*\)", "", s)  # drop parentheses content
                    s = s.replace("~", "").replace(",", "")
                    s = s.replace("/", "-").replace(" ", "_")
                    s = re.sub(r"[^A-Za-z0-9._-]", "", s)
                    s = re.sub(r"[_-]{2,}", lambda m: m.group(0)[0], s)
                    s = s.strip("-_")
                    return s[:max_len] or "Workout"

                safe_name = _safe_file_component(ses_name)
                fname = f"{day_date.isoformat()}_{safe_name}.fit"
                out_path = os.path.join(spec_dir, fname)

                try:
                    export_spec_compliant_fit_workout(ses_name, steps, out_path, estimated_miles=mi)
                    count += 1
                    print(f"Generated: {fname}")
                except Exception as e:
                    print(f"Error generating {fname}: {e}")
    
    print(f"\nGenerated {count} specification-compliant FIT files in {spec_dir}/")
    print("These files should now be fully compatible with Garmin devices.")


if __name__ == "__main__":
    generate_final_fit_files()
