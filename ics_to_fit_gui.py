#!/usr/bin/env python3
"""
GUI: Convert ICS produced by hm_plan_calendar.py into spec-compliant FIT workouts.

Workflow:
  1) Generate the calendar via training_plan_gui.py (or hm_plan_calendar.py).
     The generator embeds an X-WORKOUT property with JSON for each event.
  2) Use this tool to select the .ics and export .fit files.

Dependencies:
  pip install ics fit_tool fitparse
"""

import os
import json
import threading
from datetime import datetime, date, timedelta
import re
from typing import List, Optional

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


def _try_imports():
    missing = []
    try:
        import ics  # noqa: F401
    except Exception:
        missing.append("ics")
    try:
        import fit_tool  # noqa: F401
    except Exception:
        missing.append("fit_tool")
    try:
        import fitparse  # noqa: F401
    except Exception:
        missing.append("fitparse")
    return missing


def _parse_date(s: str) -> date:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return date(dt.year, dt.month, dt.day)
        except ValueError:
            pass
    raise ValueError("Enter date as YYYY-MM-DD or MM/DD/YYYY")


def parse_fit_file(path: str, max_steps: int = 5) -> str:
    from fitparse import FitFile

    def fields_dict(msg):
        return {f.name: f.value for f in msg.fields if getattr(f, "value", None) is not None}

    lines: List[str] = []
    lines.append(f"File: {os.path.basename(path)}")
    try:
        ff = FitFile(path)
        for m in ff.get_messages("file_id"):
            d = fields_dict(m)
            lines.append(
                f"  file_id: type={d.get('type')}, manuf={d.get('manufacturer')}, garmin_product={d.get('garmin_product')}"
            )
            break
        w_hdr = None
        for m in ff.get_messages("workout"):
            d = fields_dict(m)
            w_hdr = d
            break
        if w_hdr:
            name = w_hdr.get('wkt_name') or w_hdr.get('workout_name')
            lines.append(
                f"  workout: name={name}, sport={w_hdr.get('sport')}, sub_sport={w_hdr.get('sub_sport')}, steps={w_hdr.get('num_valid_steps')}"
            )
        steps = list(ff.get_messages("workout_step"))
        show_n = min(max_steps, len(steps))
        for i, m in enumerate(steps[:show_n]):
            d = fields_dict(m)
            ordered = {}
            for k in (
                "message_index",
                "wkt_step_name",
                "intensity",
                "duration_type",
                "duration_value",
                "duration_time",
                "duration_distance",
                "target_type",
                "target_value",
                "custom_target_value_low",
                "custom_target_value_high",
                "custom_target_speed_low",
                "custom_target_speed_high",
            ):
                if k in d:
                    ordered[k] = d.pop(k)
            ordered.update(d)
            lines.append(f"    step[{i}]: {ordered}")
            # Raw diagnostics
            try:
                raw_parts = []
                for f in m.fields:
                    name = getattr(f, 'name', '')
                    val = getattr(f, 'value', None)
                    raw = getattr(f, 'raw_value', None)
                    units = getattr(f, 'units', '')
                    defnum = getattr(f, 'def_num', None)
                    raw_parts.append(f"{name}={val} (raw={raw}, units={units}, id={defnum})")
                if raw_parts:
                    lines.append("      raw: " + "; ".join(raw_parts))
            except Exception:
                pass
        if len(steps) > show_n:
            lines.append(f"    ... ({len(steps) - show_n} more steps)")
    except Exception as e:
        lines.append(f"  ERROR: {e}")
    return "\n".join(lines)


def _get_extra_value(ev, name: str) -> Optional[str]:
    try:
        for line in getattr(ev, 'extra', []) or []:
            # ics.ContentLine has .name and .value
            if getattr(line, 'name', '').upper() == name.upper():
                return getattr(line, 'value', None)
    except Exception:
        pass
    return None


def _safe_file_component(text: str, max_len: int = 80) -> str:
    """Return a filesystem-safe component from an event name.

    Rules:
    - Drop parentheses and their contents
    - Remove tildes and commas
    - Replace slashes with '-' and spaces with '_'
    - Keep only [A-Za-z0-9._-]
    - Collapse repeats and trim
    """
    s = (text or "").strip()
    # Remove parentheses content
    s = re.sub(r"\s*\([^)]*\)", "", s)
    # Remove tildes and commas
    s = s.replace("~", "").replace(",", "")
    # Basic replacements
    s = s.replace("/", "-").replace(" ", "_")
    # Keep only safe characters
    s = re.sub(r"[^A-Za-z0-9._-]", "", s)
    # Collapse duplicate separators
    s = re.sub(r"[_-]{2,}", lambda m: m.group(0)[0], s)
    s = s.strip("-_")
    return s[:max_len] or "Workout"


class ICS2FITApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("ICS → FIT Converter")
        self.geometry("980x680")
        self._build_ui()
        self._check_requirements()

    def _build_ui(self) -> None:
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)
        r = 0

        # Inputs: ICS and output dir
        ttk.Label(frm, text="ICS File:").grid(row=r, column=0, sticky=tk.W)
        self.ics_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.ics_var, width=70).grid(row=r, column=1, sticky=tk.EW, padx=6)
        ttk.Button(frm, text="Browse", command=self._choose_ics).grid(row=r, column=2, sticky=tk.W)
        frm.grid_columnconfigure(1, weight=1)

        r += 1
        ttk.Label(frm, text="Output Dir:").grid(row=r, column=0, sticky=tk.W)
        self.out_dir_var = tk.StringVar(value="fit_out_gui")
        ttk.Entry(frm, textvariable=self.out_dir_var, width=70).grid(row=r, column=1, sticky=tk.W, padx=6)
        ttk.Button(frm, text="Choose", command=self._choose_outdir).grid(row=r, column=2, sticky=tk.W)

        # Filters row
        r += 1
        ttk.Label(frm, text="Start Date (optional)").grid(row=r, column=0, sticky=tk.W)
        self.start_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.start_var, width=16).grid(row=r, column=0, sticky=tk.W, padx=(150, 0))
        ttk.Label(frm, text="End Date (optional)").grid(row=r, column=1, sticky=tk.W)
        self.end_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.end_var, width=16).grid(row=r, column=1, sticky=tk.W, padx=(120, 0))

        # Step/target options
        r += 1
        self.flag_include_wu = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="Include implicit WU/CD (~1mi)", variable=self.flag_include_wu).grid(row=r, column=0, sticky=tk.W)

        ttk.Label(frm, text="Race pace (min/mi):").grid(row=r, column=1, sticky=tk.W)
        self.hmp_var = tk.StringVar(value="")
        ttk.Entry(frm, textvariable=self.hmp_var, width=10).grid(row=r, column=1, sticky=tk.W, padx=(110, 0))

        r += 1
        self.flag_targets = tk.BooleanVar(value=False)
        self.flag_targets_wu_cd = tk.BooleanVar(value=False)
        self.target_mode = tk.StringVar(value="pace")
        self.target_margin = tk.StringVar(value="30")
        ttk.Checkbutton(frm, text="Enable targets", variable=self.flag_targets).grid(row=r, column=0, sticky=tk.W)
        ttk.Label(frm, text="Mode:").grid(row=r, column=1, sticky=tk.W)
        ttk.Combobox(frm, textvariable=self.target_mode, state="readonly", values=["pace", "speed"], width=8).grid(row=r, column=1, sticky=tk.W, padx=(50, 0))
        ttk.Label(frm, text="± sec/mile:").grid(row=r, column=1, sticky=tk.W, padx=(140, 0))
        ttk.Entry(frm, textvariable=self.target_margin, width=6).grid(row=r, column=1, sticky=tk.W, padx=(225, 0))
        ttk.Checkbutton(frm, text="Include WU/CD targets", variable=self.flag_targets_wu_cd).grid(row=r, column=1, sticky=tk.W, padx=(300, 0))

        # Buttons
        r += 1
        btns = ttk.Frame(frm)
        btns.grid(row=r, column=0, columnspan=3, sticky=tk.W, pady=(8, 4))
        self.run_btn = ttk.Button(btns, text="Generate FITs", command=self._on_generate)
        self.run_btn.pack(side=tk.LEFT)
        ttk.Button(btns, text="Parse Output", command=self._on_parse).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Save Text", command=self._on_save_text).pack(side=tk.LEFT)

        # Output text
        r += 1
        self.text = tk.Text(frm, height=26)
        self.text.grid(row=r, column=0, columnspan=3, sticky=tk.NSEW, pady=(6, 0))
        frm.grid_rowconfigure(r, weight=1)
        yscroll = ttk.Scrollbar(frm, orient=tk.VERTICAL, command=self.text.yview)
        yscroll.grid(row=r, column=3, sticky=tk.NS)
        self.text['yscrollcommand'] = yscroll.set

        # Status bar
        r += 1
        self.status = tk.StringVar(value="Ready")
        ttk.Label(frm, textvariable=self.status).grid(row=r, column=0, columnspan=3, sticky=tk.W, pady=(4, 0))

    def _check_requirements(self) -> None:
        missing = _try_imports()
        if missing:
            messagebox.showwarning(
                "Missing packages",
                "The following packages are required and were not found:\n"
                + ", ".join(missing)
                + "\nInstall with: pip install " + " ".join(missing),
            )

    def _choose_ics(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("ICS", "*.ics"), ("All", "*.*")])
        if path:
            self.ics_var.set(path)
            # Try to read X-RACE-PACE or X-HMP default
            try:
                from ics import Calendar
                with open(path, 'r') as f:
                    cal = Calendar(f.read())
                # Find first event with X-RACE-PACE / X-HMP
                for ev in sorted(cal.events, key=lambda e: (getattr(getattr(e, 'begin', None), 'datetime', None) or datetime.max)):
                    hmp = _get_extra_value(ev, 'X-RACE-PACE') or _get_extra_value(ev, 'X-HMP')
                    if hmp:
                        self.hmp_var.set(hmp)
                        break
            except Exception:
                pass

    def _choose_outdir(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.out_dir_var.set(path)

    def _append_text(self, s: str) -> None:
        self.text.insert(tk.END, s + "\n")
        self.text.see(tk.END)

    def _run_generate(self) -> None:
        ics_path = self.ics_var.get().strip()
        out_dir = self.out_dir_var.get().strip() or "fit_out_gui"
        if not ics_path or not os.path.isfile(ics_path):
            messagebox.showerror("Error", "Select a valid ICS file")
            return
        os.makedirs(out_dir, exist_ok=True)

        # Parse optional filters
        start_date: Optional[date] = None
        end_date: Optional[date] = None
        if self.start_var.get().strip():
            try:
                start_date = _parse_date(self.start_var.get())
            except Exception as e:
                messagebox.showerror("Error", f"Invalid start date: {e}")
                return
        if self.end_var.get().strip():
            try:
                end_date = _parse_date(self.end_var.get())
            except Exception as e:
                messagebox.showerror("Error", f"Invalid end date: {e}")
                return

        # Prefer GUI race-pace override
        gui_hmp = None
        try:
            gui_hmp = float(self.hmp_var.get()) if self.hmp_var.get().strip() else None
        except Exception:
            gui_hmp = None

        self.status.set("Generating FIT files…")
        self.run_btn.configure(state=tk.DISABLED)
        try:
            from ics_to_fit_gui import convert_ics_to_fit  # self import safe
            generated, skipped, errors = convert_ics_to_fit(
                ics_path=ics_path,
                out_dir=out_dir,
                hmp=gui_hmp,
                include_wu_cd=bool(self.flag_include_wu.get()),
                targets_enabled=bool(self.flag_targets.get()),
                target_mode=(self.target_mode.get() or "pace").strip().lower(),
                target_margin=int(self.target_margin.get()) if self.target_margin.get().strip() else 30,
                targets_wu_cd=bool(self.flag_targets_wu_cd.get()),
                start_date=start_date,
                end_date=end_date,
            )
            self.status.set(f"Done. Generated {generated}, skipped {skipped}.")
            if errors and any('fit_tool not installed' in e for e in errors):
                self._append_text("Dependency missing: install with 'pip install fit_tool fitparse ics' in your active env.")
            if generated == 0 and not errors:
                self._append_text("No FIT files generated. Ensure ICS has X-WORKOUT metadata.")
        except Exception as e:
            self.status.set("Generation failed.")
            messagebox.showerror("Error", f"Generation failed: {e}")
        finally:
            self.run_btn.configure(state=tk.NORMAL)

    def _on_generate(self) -> None:
        threading.Thread(target=self._run_generate, daemon=True).start()

    def _on_parse(self) -> None:
        out_dir = self.out_dir_var.get().strip()
        if not out_dir or not os.path.isdir(out_dir):
            messagebox.showerror("Error", "Select a valid FIT output directory")
            return
        self.text.delete("1.0", tk.END)
        self.status.set("Parsing FIT files…")
        count = 0
        for name in sorted(os.listdir(out_dir)):
            if not name.lower().endswith('.fit'):
                continue
            p = os.path.join(out_dir, name)
            self._append_text(parse_fit_file(p, max_steps=5))
            self._append_text("")
            count += 1
        self.status.set(f"Parsed {count} FIT files.")
        if count == 0:
            self._append_text("No .fit files found in the selected directory.")

    def _on_save_text(self) -> None:
        content = self.text.get("1.0", tk.END)
        if not content.strip():
            messagebox.showinfo("Info", "Nothing to save.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text", "*.txt")])
        if not path:
            return
        try:
            with open(path, 'w') as f:
                f.write(content)
            self.status.set(f"Saved: {path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed saving file: {e}")


if __name__ == "__main__":
    ICS2FITApp().mainloop()

# -------------------------------
# Non-GUI conversion entry for reuse

def convert_ics_to_fit(
    ics_path: str,
    out_dir: str,
    hmp: Optional[float] = None,
    include_wu_cd: bool = False,
    targets_enabled: bool = False,
    target_mode: str = "pace",
    target_margin: int = 30,
    targets_wu_cd: bool = False,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> tuple[int, int, List[str]]:
    """Headless ICS→FIT conversion. Returns (generated_count, skipped_count, errors)."""
    errors: List[str] = []
    try:
        import importlib
        import hm_plan_to_garmin as hm
        # Ensure latest logic during long-running GUI sessions
        hm = importlib.reload(hm)
    except Exception as e:
        raise RuntimeError(f"Could not import hm_plan_to_garmin: {e}")
    try:
        from ics import Calendar
    except Exception as e:
        raise RuntimeError(f"Missing dependency 'ics': {e}")
    # Prefer spec-compliant exporter when fit_tool is available; else fallback
    export_fit = None
    try:
        import importlib
        import final_spec_compliant_fix as _spec
        _spec = importlib.reload(_spec)
        if getattr(_spec, 'FIT_TOOL_AVAILABLE', False):
            export_fit = _spec.export_spec_compliant_fit_workout
    except Exception:
        export_fit = None
    if export_fit is None:
        try:
            from hm_plan_to_garmin import export_fit_workout as export_fit  # type: ignore
        except Exception as e:
            raise RuntimeError(f"No FIT exporter available: {e}")

    # Configure hm target options
    hm.TARGETS_ENABLED = bool(targets_enabled)
    hm.TARGET_MODE = target_mode if target_mode in ("pace", "speed") else "pace"
    hm.TARGET_MARGIN_SEC = int(target_margin)
    hm.TARGET_INCLUDE_WU_CD = bool(targets_wu_cd)
    hm.INCLUDE_IMPLICIT_WU_CD = bool(include_wu_cd)

    with open(ics_path, 'r') as f:
        cal = Calendar(f.read())
    events = sorted(
        cal.events,
        key=lambda e: (getattr(getattr(e, 'begin', None), 'datetime', None) or datetime.max, e.name or "")
    )
    # Compute Week1 Monday from earliest event for naming (01w03d)
    start_monday = None
    for ev in events:
        ev_dt = getattr(getattr(ev, 'begin', None), 'datetime', None)
        if ev_dt is None:
            continue
        d = ev_dt.date()
        monday = d - timedelta(days=d.weekday())
        if start_monday is None or monday < start_monday:
            start_monday = monday
    generated = 0
    skipped = 0
    for ev in events:
        # Skip all-day and summaries
        if getattr(ev, 'all_day', False):
            continue
        ev_dt = getattr(getattr(ev, 'begin', None), 'datetime', None)
        if ev_dt is None:
            skipped += 1
            continue
        if start_date and ev_dt.date() < start_date:
            continue
        if end_date and ev_dt.date() > end_date:
            continue
        raw = _get_extra_value(ev, 'X-WORKOUT')
        if not raw:
            skipped += 1
            continue
        try:
            workout = json.loads(raw)
        except Exception:
            skipped += 1
            continue

        # Pace selection
        eff_hmp = hmp
        if eff_hmp is None:
            try:
                eff_hmp = float(_get_extra_value(ev, 'X-RACE-PACE') or _get_extra_value(ev, 'X-HMP') or '')
            except Exception:
                eff_hmp = None
        hm.RACE_PACE_MIN_PER_MILE = float(eff_hmp) if eff_hmp else 6.6667

        steps = hm.workout_to_garmin_steps(workout)
        if not steps:
            skipped += 1
            continue
        # Mileage/time estimates (used for filename and for simple-run time fallback)
        try:
            mi, mins = hm.compute_obj_miles_minutes(workout)
        except Exception:
            mi, mins = (0.0, 0.0)

        # Compatibility tweak: convert single-step distance easy-like runs
        # to a time-based warmup step (mirrors confirmed working files).
        try:
            if len(steps) == 1:
                st0 = steps[0]
                endc = (st0.get('endCondition') or '').upper()
                typ = (st0.get('type') or '').strip().lower()
                # Simple/easy run heuristics
                easy_like = any(k in typ for k in (
                    'easy', 'very easy', 'steady', 'moderate', 'kenyan', 'recovery'
                )) or typ in ('run', 'long', 'tempo')
                if endc == 'DISTANCE' and easy_like and mins and mins > 0:
                    st0['endCondition'] = 'TIME'
                    st0['endConditionValue'] = float(mins) * 60.0
                    # Force warmup intensity mapping for broad device compatibility
                    st0['type'] = 'warmup'
                    # Nudge description to be simple and ASCII-safe
                    st0['description'] = f"{int(round(mins))} min easy"
                    # Clear any accidental targets on simple runs
                    for k in ('targetType','targetValueLow','targetValueHigh'):
                        if k in st0:
                            st0.pop(k, None)
        except Exception:
            # Non-fatal: fall through with original steps
            pass

        # Safe filename
        ymd = ev_dt.date().isoformat()
        base_name = _safe_file_component(ev.name or "Workout")
        # Append compact time token to aid sorting/searching (e.g., 29m or 1h05m)
        def _time_token(total_minutes):
            try:
                m = int(round(float(total_minutes)))
            except Exception:
                return ""
            if m <= 0:
                return ""
            h = m // 60
            mm = m % 60
            return f"{h}h{mm:02d}m" if h > 0 else f"{m}m"
        tkn = _time_token(mins)
        # Prefix for watch display name: 01w03d (week/day since first Monday)
        disp_name = ev.name or "Workout"
        try:
            if start_monday is not None:
                delta_days = (ev_dt.date() - start_monday).days
                week_num = 1 + (delta_days // 7)
                day_idx = 1 + ev_dt.date().weekday()
                prefix = f"{week_num:02d}w{day_idx:02d}d"
                disp_name = f"{prefix} {disp_name}"
        except Exception:
            pass
        # Append time token to Garmin workout title; keep concise (~32 chars)
        if tkn:
            try:
                max_len = 32
                base_disp = f"{disp_name} - {tkn}"
                if len(base_disp) > max_len:
                    # Try to preserve: prefix + middle (trimmed) + distance suffix + time
                    import re as _re
                    parts = disp_name.split(' ', 1)
                    pref, rest = (parts[0], parts[1]) if len(parts) == 2 else (disp_name, '')
                    # Extract distance suffix like ' - 6.8 mi' if present
                    m = _re.search(r"\s-\s\d+(?:\.\d+)?\s*(?:mi|km|m)\b", rest)
                    suffix = m.group(0) if m else ''
                    # Reserve space for: pref + space + trimmed middle + suffix + ' - ' + tkn
                    reserve = len(pref) + 1 + len(suffix) + 3 + len(tkn)
                    avail = max_len - reserve
                    if avail < 4:
                        # If too tight, keep just prefix + suffix (if any) + time
                        mid = ''
                    else:
                        # Trim the middle description to available
                        mid = rest[:len(rest)-len(suffix)] if suffix else rest
                        mid = (mid[:avail]).rstrip()
                        if mid:
                            mid = ' ' + mid
                    disp_name = f"{pref}{mid}{suffix} - {tkn}".strip()
                    # If still too long, hard trim
                    if len(disp_name) > max_len:
                        disp_name = disp_name[:max_len]
                else:
                    disp_name = base_disp
            except Exception:
                disp_name = f"{disp_name} - {tkn}"
        fname = f"{ymd}_{base_name}_{tkn}.fit" if tkn else f"{ymd}_{base_name}.fit"
        outp = os.path.join(out_dir, fname)
        try:
            export_fit(disp_name, steps, outp, estimated_miles=mi)
            generated += 1
        except Exception as e:
            # If spec exporter complained about missing fit_tool, fall back once to hm exporter
            msg = str(e)
            if 'fit_tool not installed' in msg:
                try:
                    from hm_plan_to_garmin import export_fit_workout as _fallback_export  # type: ignore
                    _fallback_export(disp_name, steps, outp, estimated_miles=mi)
                    generated += 1
                    continue
                except Exception as e2:
                    errors.append(str(e2))
            errors.append(msg)

    return generated, skipped, errors
