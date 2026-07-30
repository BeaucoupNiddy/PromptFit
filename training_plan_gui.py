import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime, date
import os
import webbrowser
import subprocess
import sys

# Import the generator module (uses globals and a main() entry)
import hm_plan_calendar as gen


def parse_date(s: str) -> date:
    s = (s or "").strip()
    # Accept YYYY-MM-DD or MM/DD/YYYY
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return date(dt.year, dt.month, dt.day)
        except ValueError:
            pass
    raise ValueError("Enter date as YYYY-MM-DD or MM/DD/YYYY")


def parse_pace(s: str) -> float:
    # Try to reuse generator's parsing if available
    p = gen.parse_pace_str(s)
    if p is not None:
        return float(p)
    # Fallback: plain float minutes per mile
    try:
        return float(s)
    except Exception:
        pass
    raise ValueError("Enter pace like 7:10/mi or 7.17")


class PlanGUI(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=12)
        self.master.title("Training Plan Generator")
        self.master.minsize(720, 540)
        self.grid(sticky="nsew")
        self.columnconfigure(0, weight=1)
        self._plan_peak_mileage = None

        self._build()

    def _build(self):
        # Plan source
        lf_src = ttk.LabelFrame(self, text="Plan Source", padding=10)
        lf_src.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        lf_src.columnconfigure(1, weight=1)

        ttk.Label(lf_src, text="Input JSON").grid(row=0, column=0, sticky="w")
        self.var_json = tk.StringVar(value=os.path.abspath(gen.input_json_file))
        ent_json = ttk.Entry(lf_src, textvariable=self.var_json)
        ent_json.grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(lf_src, text="Browse…", command=self._pick_json).grid(row=0, column=2, padx=(6, 0))
        ttk.Button(lf_src, text="?", width=2, command=self._info_input_json).grid(row=0, column=3, padx=(6,0))
        try:
            with open(self.var_json.get(), "r") as f:
                data = json.load(f)
            plan_meta = data.get("plan_meta", {}) if isinstance(data, dict) else {}
            self._plan_peak_mileage = gen.get_plan_reference_peak(plan_meta)
        except Exception:
            self._plan_peak_mileage = None

        # Targets
        lf_targets = ttk.LabelFrame(self, text="Targets", padding=10)
        lf_targets.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        for c in range(6):
            lf_targets.columnconfigure(c, weight=1)

        ttk.Label(lf_targets, text="Race Date").grid(row=0, column=0, sticky="w")
        self.var_date = tk.StringVar(value=gen.race_date.isoformat())
        ttk.Entry(lf_targets, textvariable=self.var_date, width=16).grid(row=0, column=1, sticky="w", padx=(6, 12))

        ttk.Label(lf_targets, text="Race Pace (min/mi)").grid(row=0, column=2, sticky="w")
        self.var_pace = tk.StringVar(value=f"{gen.race_pace_min_per_mile:.2f}")
        ttk.Entry(lf_targets, textvariable=self.var_pace, width=10).grid(row=0, column=3, sticky="w", padx=(6, 12))
        ttk.Button(lf_targets, text="?", width=2, command=self._info_targets).grid(row=0, column=6, sticky="e")

        ttk.Label(lf_targets, text="Peak Mileage (mpw)").grid(row=0, column=4, sticky="w")
        self.var_peak = tk.StringVar(value=str(gen.peak_mileage))
        ttk.Entry(lf_targets, textvariable=self.var_peak, width=8).grid(row=0, column=5, sticky="w", padx=(6, 0))

        ttk.Label(lf_targets, text="Race Distance").grid(row=1, column=0, sticky="w", pady=(6, 0))
        default_race = gen.normalize_race_distance(getattr(gen, "race_distance", None)) or "half marathon"
        self.var_race_distance = tk.StringVar(value=default_race)
        ttk.Combobox(
            lf_targets,
            textvariable=self.var_race_distance,
            state="readonly",
            values=["5k", "10k", "half marathon", "marathon"],
            width=16,
        ).grid(row=1, column=1, sticky="w", padx=(6, 12), pady=(6, 0))

        ttk.Label(lf_targets, text="Easy Pace (min/mi)").grid(row=1, column=2, sticky="w", pady=(6, 0))
        easy_default = ""
        try:
            if getattr(gen, "easy_pace_min_per_mile", None):
                easy_default = f"{float(gen.easy_pace_min_per_mile):.2f}"
        except Exception:
            easy_default = ""
        self.var_easy_pace = tk.StringVar(value=easy_default)
        ttk.Entry(lf_targets, textvariable=self.var_easy_pace, width=10).grid(row=1, column=3, sticky="w", padx=(6, 12), pady=(6, 0))

        # Scaling factors
        lf_scale = ttk.LabelFrame(self, text="Scaling", padding=10)
        lf_scale.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        for c in range(8):
            lf_scale.columnconfigure(c, weight=1)

        ttk.Label(lf_scale, text="Output Base Name").grid(row=0, column=0, sticky="w")
        base = os.path.splitext(os.path.basename(gen.output_ics_file))[0]
        self.var_base = tk.StringVar(value=base)
        ttk.Entry(lf_scale, textvariable=self.var_base, width=20).grid(row=0, column=1, sticky="w", padx=(6, 20))
        ttk.Button(lf_scale, text="?", width=2, command=self._info_scaling_overview).grid(row=0, column=7, sticky="e")

        self.var_scale_wu = tk.BooleanVar(value=bool(gen.scale_wu_cd_segments))
        ttk.Checkbutton(lf_scale, text="Scale explicit WU/CD segments", variable=self.var_scale_wu).grid(row=0, column=2, sticky="w")
        ttk.Button(lf_scale, text="?", width=2, command=self._info_scale_wu_cd).grid(row=0, column=4, sticky="w")

        self.var_implicit_wu = tk.BooleanVar(value=bool(gen.include_implicit_wu_cd))
        ttk.Checkbutton(lf_scale, text="Include implicit WU/CD (~1mi)", variable=self.var_implicit_wu).grid(row=0, column=3, sticky="w")
        ttk.Button(lf_scale, text="?", width=2, command=self._info_implicit_wu).grid(row=0, column=5, sticky="e")

        # Workout vs Easy factors
        ttk.Separator(lf_scale).grid(row=1, column=0, columnspan=6, sticky="ew", pady=6)
        ttk.Label(lf_scale, text="Workout Factor Mode").grid(row=2, column=0, sticky="w")
        self.var_wf_mode = tk.StringVar(value="same")
        frm_modes = ttk.Frame(lf_scale)
        frm_modes.grid(row=2, column=1, columnspan=5, sticky="w")
        ttk.Radiobutton(frm_modes, text="Same as base", value="same", variable=self.var_wf_mode, command=self._on_wf_mode).grid(row=0, column=0, padx=(0, 12))
        ttk.Radiobutton(frm_modes, text="Normalize to peak mileage", value="normalize", variable=self.var_wf_mode, command=self._on_wf_mode).grid(row=0, column=1, padx=(0, 12))
        ttk.Radiobutton(frm_modes, text="Custom multiplier", value="custom", variable=self.var_wf_mode, command=self._on_wf_mode).grid(row=0, column=2)
        ttk.Button(lf_scale, text="?", width=2, command=self._info_workout_factor).grid(row=2, column=7, sticky="w")

        self.var_wf_custom = tk.StringVar(value="1.00")
        self.ent_wf_custom = ttk.Entry(lf_scale, textvariable=self.var_wf_custom, width=8, state='disabled')
        ttk.Label(lf_scale, text="Custom multiplier (of original plan):").grid(row=3, column=0, sticky="e", padx=(0, 6))
        self.ent_wf_custom.grid(row=3, column=1, sticky="w")
        self.var_wf_peak_note = tk.StringVar(value="Peak ratio: —")
        ttk.Label(lf_scale, textvariable=self.var_wf_peak_note).grid(row=4, column=0, columnspan=5, sticky="w", pady=(4, 0))

        # Doubles / options
        lf_opts = ttk.LabelFrame(self, text="Options", padding=10)
        lf_opts.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        for c in range(6):
            lf_opts.columnconfigure(c, weight=1)

        # Optional doubles removed from plan format; no GUI toggle needed

        self.var_collapse_doubles = tk.BooleanVar(value=bool(gen.collapse_doubles))
        ttk.Checkbutton(lf_opts, text="Disable doubles (collapse AM/PM into one; prefer workout over easy)", variable=self.var_collapse_doubles).grid(row=0, column=1, sticky="w")
        self.var_consolidate_two_workouts = tk.BooleanVar(value=bool(getattr(gen, 'consolidate_two_workout_doubles', False)))
        ttk.Checkbutton(lf_opts, text="If both AM&PM are workouts: consolidate into one session", variable=self.var_consolidate_two_workouts).grid(row=0, column=2, columnspan=2, sticky="w")

        ttk.Label(lf_opts, text="Rest days per week").grid(row=1, column=0, sticky="w", pady=(6,0))
        self.var_rest_days = tk.StringVar(value=str(gen.rest_days_per_week_target))
        ttk.Entry(lf_opts, textvariable=self.var_rest_days, width=6).grid(row=1, column=1, sticky="w", padx=(6, 12), pady=(6,0))
        self.var_redistribute = tk.BooleanVar(value=bool(gen.redistribute_removed_load))
        ttk.Checkbutton(lf_opts, text="Redistribute removed mileage to easy days", variable=self.var_redistribute).grid(row=1, column=2, sticky="w", pady=(6,0))
        ttk.Button(lf_opts, text="?", width=2, command=self._info_rest_days).grid(row=1, column=3, sticky="w", pady=(6,0))

        # Normalization option
        self.var_normalize = tk.BooleanVar(value=bool(getattr(gen, 'normalize_weekly_to_reference', True)))
        ttk.Checkbutton(
            lf_opts,
            text="Normalize weekly miles to plan's reference (adjust easy runs)",
            variable=self.var_normalize
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6,0))
        ttk.Button(lf_opts, text="?", width=2, command=self._info_normalize).grid(row=2, column=3, sticky="w", pady=(6,0))
        self.var_norm_reduce = tk.BooleanVar(value=bool(getattr(gen, 'normalize_reduce_for_fast', False)))
        ttk.Checkbutton(lf_opts, text="Also reduce easy time for fast athletes (strict match)", variable=self.var_norm_reduce).grid(row=3, column=0, columnspan=3, sticky="w")

        # Actions
        frm_actions = ttk.Frame(self, padding=(0, 6, 0, 0))
        frm_actions.grid(row=4, column=0, sticky="ew")
        frm_actions.columnconfigure(0, weight=1)
        self.btn_gen = ttk.Button(frm_actions, text="Generate Plan", command=self._on_generate)
        self.btn_gen.grid(row=0, column=1, sticky="e")

        # FIT export (in-process) section to avoid interpreter mismatches
        lf_fit = ttk.LabelFrame(self, text="FIT Export (Spec-Compliant)", padding=10)
        lf_fit.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        lf_fit.columnconfigure(1, weight=1)
        ttk.Label(lf_fit, text="FIT Output Dir").grid(row=0, column=0, sticky="w")
        self.var_fit_out = tk.StringVar(value="fit_out_gui")
        ttk.Entry(lf_fit, textvariable=self.var_fit_out).grid(row=0, column=1, sticky="ew", padx=(6, 6))
        ttk.Button(lf_fit, text="Browse…", command=self._pick_fit_outdir).grid(row=0, column=2)
        # Target options
        # Default to no targets to mirror working files and improve compatibility
        self.var_targets = tk.BooleanVar(value=False)
        ttk.Checkbutton(lf_fit, text="Enable pace targets", variable=self.var_targets).grid(row=1, column=0, sticky="w", pady=(6,0))
        ttk.Label(lf_fit, text="Mode:").grid(row=1, column=1, sticky="w", padx=(6,0))
        self.var_target_mode = tk.StringVar(value="pace")
        ttk.Combobox(lf_fit, textvariable=self.var_target_mode, state="readonly", values=["pace", "speed"], width=8).grid(row=1, column=1, sticky="w", padx=(56,0))
        ttk.Label(lf_fit, text="± sec/mile:").grid(row=1, column=1, sticky="w", padx=(140,0))
        self.var_target_margin = tk.StringVar(value="30")
        ttk.Entry(lf_fit, textvariable=self.var_target_margin, width=6).grid(row=1, column=1, sticky="w", padx=(226,0))
        self.var_target_wu_cd = tk.BooleanVar(value=False)
        ttk.Checkbutton(lf_fit, text="Include WU/CD targets", variable=self.var_target_wu_cd).grid(row=1, column=2, sticky="w")

        # Actions
        btn_row = ttk.Frame(lf_fit)
        btn_row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        btn_row.columnconfigure(0, weight=1)
        ttk.Button(btn_row, text="Generate FITs (spec)", command=self._on_generate_fits).grid(row=0, column=1, sticky="e")
        ttk.Button(btn_row, text="Parse Output", command=self._on_parse_fits).grid(row=0, column=2, sticky="e", padx=(8,0))
        ttk.Button(btn_row, text="How to load to watch", command=self._info_sideload).grid(row=0, column=3, sticky="e", padx=(8,0))

        # Parse output text area
        frm_out = ttk.Frame(lf_fit)
        frm_out.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(6,0))
        lf_fit.rowconfigure(3, weight=1)
        lf_fit.columnconfigure(1, weight=1)
        frm_out.columnconfigure(0, weight=1)
        self.txt_parse = tk.Text(frm_out, height=18)
        self.txt_parse.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(frm_out, orient=tk.VERTICAL, command=self.txt_parse.yview)
        yscroll.grid(row=0, column=1, sticky=tk.NS)
        self.txt_parse["yscrollcommand"] = yscroll.set
        self.var_peak.trace_add("write", lambda *_: self._update_wf_peak_label())
        self._update_wf_peak_label()

    # --- Info boxes ---
    def _info_scaling_overview(self):
        message = (
            "Scaling overview\n\n"
            "• Base factor: peak_mileage / plan reference peak.\n"
            "• Easy-like runs (easy, very easy, steady, easy to moderate) scale by base factor\n"
            "  and adjust time for athlete pace vs plan’s reference pace to keep weekly miles reasonable.\n"
            "• Workouts (intervals, tempos, long, race, special block, etc.) can use a separate factor.\n"
            "• Races/tune-ups are not scaled.\n"
            "• Explicit WU/CD segments scale only if that option is checked."
        )
        messagebox.showinfo("Scaling Overview", message)

    def _info_scale_wu_cd(self):
        message = (
            "Scale explicit WU/CD segments\n\n"
            "When enabled, time-based warmups/cooldowns inside workouts will be scaled using the \n"
            "workout/easy factor for that workout type. Otherwise, their durations remain as-is."
        )
        messagebox.showinfo("Scale Explicit WU/CD", message)

    def _info_input_json(self):
        message = (
            "Input JSON\n\n"
            "Select the training plan file (weeks/days/workouts). The generator uses fields like\n"
            "type, duration/distance, intensity, sets/segments, and optional plan_meta for defaults.\n"
            "Output calendar respects doubles, warmups, and race alignment."
        )
        messagebox.showinfo("Input JSON", message)

    def _info_targets(self):
        message = (
            "Targets\n\n"
            "• Race Date: The plan aligns Week 1 Monday so the final Saturday is your race day.\n"
            "• Race Distance: Select 5k, 10k, half marathon, or marathon for labeling and metadata.\n"
            "• Race Pace: Enter minutes per mile (e.g., 7:10/mi or 7.17).\n"
            "• Easy Pace: Optional override used to estimate mileage from time-based easy runs.\n"
            "• Peak Mileage: Sets the base scale factor relative to the plan's reference peak."
        )
        messagebox.showinfo("Targets", message)

    def _info_implicit_wu(self):
        message = (
            "Include implicit WU/CD (~1mi)\n\n"
            "Adds ~1.0 mi (at ~9:15/mi) to certain workout types for time estimation and weekly totals \n"
            "when the plan JSON doesn’t encode WU/CD explicitly."
        )
        messagebox.showinfo("Implicit WU/CD", message)

    def _info_workout_factor(self):
        message = (
            "Workout factor options\n\n"
            "• Same as base: keep the current plan/base behavior for workouts.\n"
            "• Normalize to peak mileage: set workout scaling to the peak ratio (target peak / plan peak).\n"
            "• Custom multiplier: multiply the original plan (1.00 = original scale).\n\n"
            "Notes:\n"
            "• Easy-like runs always use the base factor (with pace compensation).\n"
            "• Workout factor affects reps and distances inside workouts; workouts still obey other rules \n"
            "  like race/tune-up no-scaling and minimums where applicable."
        )
        messagebox.showinfo("Workout Factor", message)

    def _info_rest_days(self):
        message = (
            "Rest days per week\n\n"
            "Set a target number of weekly rest days. We count built-in rest days first, then, if needed,\n"
            "convert the easiest/lowest-mileage days to rest (prefer easy runs; never races). If 'Redistribute',\n"
            "we spread the removed load across remaining easy days by lengthening them to keep weekly miles closer\n"
            "to your target."
        )
        messagebox.showinfo("Rest Days", message)

    def _info_normalize(self):
        message = (
            "Normalize weekly miles\n\n"
            "Time-based workouts yield more miles for faster athletes. When enabled, the generator\n"
            "adds time to easy runs for slower athletes so the week's total miles match what the plan\n"
            "would produce at the plan's reference pace. Workouts are unchanged; only easy-like runs\n"
            "receive the added time."
        )
        messagebox.showinfo("Normalize Weekly Miles", message)

    def _pick_json(self):
        path = filedialog.askopenfilename(
            title="Select Plan JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir=os.path.dirname(self.var_json.get() or os.getcwd()),
        )
        if path:
            self.var_json.set(path)
            # Try to detect race distance from plan meta
            try:
                import json as _json
                with open(path, "r") as f:
                    data = _json.load(f)
                plan_meta = data.get("plan_meta", {}) if isinstance(data, dict) else {}
                race_meta = (
                    plan_meta.get("race_distance")
                    or plan_meta.get("race")
                    or plan_meta.get("race_length")
                    or plan_meta.get("goal")
                )
                race_norm = gen.normalize_race_distance(race_meta)
                if race_norm:
                    self.var_race_distance.set(race_norm)
                easy_meta = plan_meta.get("easy_pace") or plan_meta.get("easy_pace_min_per_mile")
                if easy_meta and not self.var_easy_pace.get().strip():
                    try:
                        easy_val = parse_pace(str(easy_meta))
                        self.var_easy_pace.set(f"{easy_val:.2f}")
                    except Exception:
                        pass
                try:
                    self._plan_peak_mileage = gen.get_plan_reference_peak(plan_meta)
                except Exception:
                    self._plan_peak_mileage = None
                self._update_wf_peak_label()
            except Exception:
                pass

    def _update_wf_peak_label(self):
        plan_peak = self._plan_peak_mileage
        user_peak = None
        try:
            user_peak = float(self.var_peak.get())
        except Exception:
            user_peak = None
        if plan_peak and user_peak and user_peak > 0:
            ratio = user_peak / plan_peak
            pct = ratio * 100.0
            self.var_wf_peak_note.set(
                f"Peak ratio: {user_peak:g} / {plan_peak:g} = {ratio:.2f} ({pct:.0f}%)"
            )
        elif plan_peak:
            self.var_wf_peak_note.set(f"Plan peak: {plan_peak:g} mi (enter target peak to see ratio)")
        else:
            self.var_wf_peak_note.set("Peak ratio: —")

    def _on_wf_mode(self):
        mode = self.var_wf_mode.get()
        self.ent_wf_custom.config(state='normal' if mode == 'custom' else 'disabled')
        self._update_wf_peak_label()

    def _on_generate(self):
        try:
            # Always reload generator to pick up latest scaling logic while GUI stays open
            import importlib
            importlib.reload(gen)
            # Parse inputs
            in_json = self.var_json.get().strip()
            if not in_json:
                raise ValueError("Please select an input JSON file")
            if not os.path.isfile(in_json):
                raise ValueError("Input JSON file not found")

            dt = parse_date(self.var_date.get())
            pace = parse_pace(self.var_pace.get())
            easy_pace_val = None
            if self.var_easy_pace.get().strip():
                easy_pace_val = parse_pace(self.var_easy_pace.get())
            peak = float(self.var_peak.get())
            if peak <= 0:
                raise ValueError("Peak mileage must be > 0")
            base_name = self.var_base.get().strip() or "training_plan_hmp"

            # Apply to generator globals
            gen.input_json_file = in_json
            gen.output_ics_file = base_name + ".ics"
            gen.race_date = dt
            gen.race_pace_min_per_mile = pace
            gen.race_distance = gen.normalize_race_distance(self.var_race_distance.get()) or self.var_race_distance.get().strip().lower()
            gen.easy_pace_min_per_mile = easy_pace_val
            gen.peak_mileage = peak
            gen.include_implicit_wu_cd = bool(self.var_implicit_wu.get())
            gen.scale_wu_cd_segments = bool(self.var_scale_wu.get())
            gen.collapse_doubles = bool(self.var_collapse_doubles.get())
            gen.consolidate_two_workout_doubles = bool(self.var_consolidate_two_workouts.get())
            # Rest-day settings
            try:
                gen.rest_days_per_week_target = int(self.var_rest_days.get())
            except Exception:
                raise ValueError("Rest days per week must be an integer >= 0")
            gen.redistribute_removed_load = bool(self.var_redistribute.get())
            # Weekly normalization
            gen.normalize_weekly_to_reference = bool(self.var_normalize.get())
            gen.normalize_reduce_for_fast = bool(self.var_norm_reduce.get())

            # Workout factor knobs
            mode = self.var_wf_mode.get()
            gen.workout_factor_mode = mode
            if mode == 'custom':
                try:
                    gen.workout_factor_override = float(self.var_wf_custom.get())
                except Exception:
                    raise ValueError("Provide a numeric custom multiplier for workout factor")
                gen.workout_factor_multiplier = None
            else:
                gen.workout_factor_override = None
                gen.workout_factor_multiplier = None

            # Run generation
            gen.main()
            messagebox.showinfo("Done", "Plan generated. The preview should open automatically.\nOutputs saved under 'Calendars/'.")
        except Exception as ex:
            messagebox.showerror("Error", str(ex))

    def _open_converter(self):
        try:
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ics_to_fit_gui.py')
            if not os.path.isfile(script):
                raise FileNotFoundError("ics_to_fit_gui.py not found next to this script")
            subprocess.Popen([sys.executable, script])
        except Exception as ex:
            messagebox.showerror("Error", f"Could not launch ICS→FIT converter: {ex}")

    def _pick_fit_outdir(self):
        path = filedialog.askdirectory(title="Select FIT output directory")
        if path:
            self.var_fit_out.set(path)

    def _find_latest_ics(self, base_name: str) -> str:
        """Find the latest generated ICS for the given base name in Calendars/."""
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            cal_dir = os.path.join(script_dir, 'Calendars')
            if not os.path.isdir(cal_dir):
                return ""
            candidates = []
            import re
            for name in os.listdir(cal_dir):
                if not name.lower().endswith('.ics'):
                    continue
                if not name.startswith(base_name + "_"):
                    continue
                m = re.search(r"_(\d+)\.ics$", name, re.IGNORECASE)
                num = int(m.group(1)) if m else -1
                candidates.append((num, os.path.join(cal_dir, name)))
            if not candidates:
                return ""
            candidates.sort()
            return candidates[-1][1]
        except Exception:
            return ""

    def _on_generate_fits(self):
        try:
            base_name = self.var_base.get().strip() or "training_plan_hmp"
            ics_path = self._find_latest_ics(base_name)
            if not ics_path:
                raise RuntimeError("Could not find a generated ICS. Generate the plan first.")
            out_dir = self.var_fit_out.get().strip() or "fit_out_gui"
            os.makedirs(out_dir, exist_ok=True)

            # Import and run conversion in-process to avoid interpreter mismatch
            from ics_to_fit_gui import convert_ics_to_fit
            generated, skipped, errors = convert_ics_to_fit(
                ics_path=ics_path,
                out_dir=out_dir,
                hmp=None,  # use X-RACE-PACE / X-HMP embedded in ICS
                include_wu_cd=bool(self.var_implicit_wu.get()),
                targets_enabled=bool(self.var_targets.get()),
                target_mode=(self.var_target_mode.get() or "pace").strip().lower(),
                target_margin=int(self.var_target_margin.get() or 30),
                targets_wu_cd=bool(self.var_target_wu_cd.get()),
                start_date=None,
                end_date=None,
            )
            msg = f"FIT export complete. Generated: {generated}, skipped: {skipped}.\nOutput: {out_dir}"
            if errors:
                msg += "\nErrors:\n- " + "\n- ".join(errors[:6])
            messagebox.showinfo("FIT Export", msg)
        except Exception as ex:
            messagebox.showerror("FIT Export", str(ex))

    def _append_parse_line(self, s: str) -> None:
        try:
            self.txt_parse.insert(tk.END, s + "\n")
            self.txt_parse.see(tk.END)
        except Exception:
            pass

    def _parse_fit_file(self, path: str, max_steps: int = 5) -> str:
        """Return a brief text summary of a FIT workout file (file_id, workout, first steps)."""
        lines = [f"File: {os.path.basename(path)}"]
        try:
            from fitparse import FitFile
        except Exception as e:
            return f"File: {os.path.basename(path)}\n  ERROR: fitparse not installed ({e})"

        try:
            ff = FitFile(path)
            def fields_dict(msg):
                return {f.name: f.value for f in msg.fields if getattr(f, 'value', None) is not None}

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
                # Pretty values
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

    def _on_parse_fits(self) -> None:
        out_dir = self.var_fit_out.get().strip() or "fit_out_gui"
        if not os.path.isdir(out_dir):
            messagebox.showerror("Parse FITs", "Select a valid FIT output directory")
            return
        # Clear view
        try:
            self.txt_parse.delete("1.0", tk.END)
        except Exception:
            pass
        count = 0
        for name in sorted(os.listdir(out_dir)):
            if not name.lower().endswith('.fit'):
                continue
            p = os.path.join(out_dir, name)
            self._append_parse_line(self._parse_fit_file(p, max_steps=5))
            self._append_parse_line("")
            count += 1
        if count == 0:
            self._append_parse_line("No .fit files found in the selected directory.")
        else:
            self._append_parse_line(f"Parsed {count} FIT files.")

    def _info_sideload(self):
        message = (
            "Sideload FIT workouts to your Garmin watch\n\n"
            "1) Connect the watch to your computer via USB.\n"
            "2) Open the watch storage. Depending on model, copy .fit files to:\n"
            "   • GARMIN/Workouts  (preferred), or\n"
            "   • GARMIN/NEWFILES  (auto-import on disconnect).\n"
            "3) Safely eject the device. The workouts appear under Training → Workouts.\n\n"
            "Notes:\n"
            "• This app writes targets per step (pace/speed) using the FIT spec.\n"
            "• If you don’t want targets on warmup/cooldown, leave that box unchecked.\n"
            "• If a device ignores pace display, set Mode to 'speed'."
        )
        messagebox.showinfo("Sideload FIT Workouts", message)


def main():
    root = tk.Tk()
    # Use ttk theme
    try:
        style = ttk.Style(root)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
    except Exception:
        pass
    root.rowconfigure(0, weight=1)
    root.columnconfigure(0, weight=1)
    app = PlanGUI(root)
    # Add a simple menu to launch the ICS→FIT converter for a smoother workflow
    try:
        menubar = tk.Menu(root)
        tools = tk.Menu(menubar, tearoff=0)
        tools.add_command(label="Open ICS→FIT Converter", command=app._open_converter)
        menubar.add_cascade(label="Tools", menu=tools)
        root.config(menu=menubar)
    except Exception:
        pass
    root.mainloop()


if __name__ == "__main__":
    main()
