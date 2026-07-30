from __future__ import annotations

import io
import os
import json
import tempfile
import zipfile
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any
import re
import uuid
import time
import threading

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from webapp.llm import parse_prompt_to_plan, parse_plan_text_to_json
from webapp import garmin_connect as gc_opt
from webapp.studio import STUDIO_HTML
from webapp.pace_knowledge import (
    estimate_anchor_pace,
    normalize_pace_profile,
    normalize_race_distance as normalize_pace_race_distance,
)
try:
    import keyring  # type: ignore
except Exception:
    keyring = None
import platform
import glob
import shutil


app = FastAPI(title="Prompt -> FIT Converter")
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_PLAN_PREVIEW_STORE: Dict[str, Dict[str, Any]] = {}
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PLAN_PRESET_DIR = _PROJECT_ROOT / "Running_Plans"
_GARMIN_PENDING_UPLOADS: Dict[str, Dict[str, Any]] = {}
_GARMIN_PENDING_TTL_SECONDS = 10 * 60
_PLAN_GARMIN_UPLOAD_LOCK = threading.Lock()
_GARMIN_MANAGED_LOCK = threading.Lock()


def _garmin_managed_path() -> Path:
    return _garmin_tokenstore_path().parent / "managed_workouts.json"


def _read_garmin_managed() -> List[Dict[str, Any]]:
    path = _garmin_managed_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _write_garmin_managed(rows: List[Dict[str, Any]]) -> None:
    path = _garmin_managed_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.chmod(0o600)
    temp_path.replace(path)


def _remember_plan_uploads(token: str, report: Dict[str, Any]) -> None:
    additions = []
    for result in report.get("results") or []:
        if not result.get("ok") or not result.get("scheduled"):
            continue
        schedule_date = str(result.get("scheduleDate") or result.get("requestedScheduleDate") or "")
        workout_id = result.get("workoutId")
        if not schedule_date or not workout_id:
            continue
        additions.append({
            "plan_token": token,
            "source_id": result.get("sourceId") or result.get("source"),
            "schedule_date": schedule_date,
            "workout_id": workout_id,
            "workout_schedule_id": result.get("workoutScheduleId"),
            "workout_name": result.get("workoutName") or result.get("source"),
            "uploaded_at": _garmin_checked_at(),
        })
    if not additions:
        return
    with _GARMIN_MANAGED_LOCK:
        rows = _read_garmin_managed()
        rows.extend(additions)
        _write_garmin_managed(rows)


def _remove_managed_garmin_range(client: Any, start: date, end: date) -> Dict[str, Any]:
    """Remove only PromptFit uploads tracked locally within an inclusive date range."""
    removed: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    with _GARMIN_MANAGED_LOCK:
        rows = _read_garmin_managed()
        keep: List[Dict[str, Any]] = []
        for row in rows:
            try:
                scheduled_for = _parse_date_input(str(row.get("schedule_date") or ""))
            except Exception:
                keep.append(row)
                continue
            if scheduled_for < start or scheduled_for > end:
                keep.append(row)
                continue
            retry_row = dict(row)
            try:
                schedule_id = row.get("workout_schedule_id")
                if schedule_id:
                    gc_opt.unschedule_workout(client, schedule_id)
                    retry_row["workout_schedule_id"] = None
                gc_opt.delete_workout(client, row.get("workout_id"))
                removed.append(row)
            except Exception as exc:
                keep.append(retry_row)
                errors.append({**retry_row, "error": str(exc)})
        _write_garmin_managed(keep)
    return {"removed": removed, "errors": errors}

def _normalize_race_distance(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    s = s.replace("_", " ").replace("-", " ")
    s = re.sub(r"\\s+", " ", s)
    if "half" in s or "13.1" in s or "21.1" in s or "21.097" in s:
        return "half marathon"
    if "marathon" in s or "26.2" in s or "42.1" in s or "42.195" in s:
        return "marathon"
    if "10k" in s or "10 km" in s or "10000" in s or "10,000" in s:
        return "10k"
    if "5k" in s or "5 km" in s or "5000" in s or "5,000" in s:
        return "5k"
    return None

def _race_label(value: Optional[str]) -> str:
    if value == "5k":
        return "5K"
    if value == "10k":
        return "10K"
    if value == "marathon":
        return "Marathon"
    if value == "half marathon":
        return "Half Marathon"
    return ""

def _parse_peak_mileage(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"(\\d+(?:\\.\\d+)?)", str(value))
    if m:
        try:
            return float(m.group(1))
        except Exception:
            return None
    return None

def _short_summary(text: str, max_len: int = 120) -> str:
    s = (text or "").strip().replace("\\n", " ")
    s = re.sub(r"\\s+", " ", s)
    if len(s) <= max_len:
        return s
    cut = s[:max_len].rsplit(" ", 1)[0]
    return (cut or s[:max_len]).strip() + "..."

def _list_plan_presets() -> List[Dict[str, Any]]:
    presets: List[Dict[str, Any]] = []
    if not _PLAN_PRESET_DIR.exists():
        return presets
    for path in sorted(_PLAN_PRESET_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            meta = data.get("plan_meta", {}) if isinstance(data, dict) else {}
            title = (meta.get("title") or meta.get("name") or path.stem).strip()
            summary = meta.get("summary") or meta.get("description") or meta.get("notes") or ""
            summary = _short_summary(summary)
            race_raw = (
                meta.get("race_distance")
                or meta.get("goal")
                or meta.get("race")
                or meta.get("race_length")
            )
            race_norm = _normalize_race_distance(race_raw)
            race_label = _race_label(race_norm)
            peak = _parse_peak_mileage(meta.get("reference_peak_mileage") or meta.get("peak_mileage"))
            weeks = len(data.get("weeks", [])) if isinstance(data, dict) else None
            source_title = str(meta.get("source_title") or "").strip()
            family_haystack = f"{title} {source_title}".lower()
            if "pfitz" in family_haystack or "advanced marathoning" in family_haystack or "faster road racing" in family_haystack:
                family = "Pfitzinger"
            elif "hanson" in family_haystack:
                family = "Hansons"
            elif "daniels" in family_haystack or "vdot" in family_haystack:
                family = "Daniels"
            elif "davis" in family_haystack:
                family = "Davis"
            elif "marathon excellence" in family_haystack:
                family = "Marathon Excellence"
            else:
                family = "Other"
            label_parts = []
            if summary:
                label_parts.append(summary)
            if race_label:
                label_parts.append(race_label)
            if peak:
                label_parts.append(f"peak {peak:g} mi")
            if weeks:
                label_parts.append(f"{weeks} wks")
            label = title
            if label_parts:
                label = f"{title} — " + " · ".join(label_parts)
            presets.append({
                "id": path.name,
                "title": title,
                "summary": summary,
                "race_distance": race_norm,
                "race_label": race_label,
                "reference_peak_mileage": peak,
                "weeks": weeks,
                "family": family,
                "official_schedule": meta.get("official_schedule"),
                "label": label,
            })
        except Exception:
            continue
    return presets

def _resolve_plan_preset_path(plan_id: str) -> Optional[Path]:
    if not plan_id:
        return None
    try:
        candidate = (_PLAN_PRESET_DIR / plan_id).resolve()
    except Exception:
        return None
    if not candidate.is_file() or candidate.suffix.lower() != ".json":
        return None
    if _PLAN_PRESET_DIR not in candidate.parents:
        return None
    return candidate


# Serve a minimal HTML UI
INDEX_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Prompt → FIT</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    @import url("https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=Space+Grotesk:wght@400;500;600&display=swap");

    :root {
      --bg: #f6f1ea;
      --bg-2: #f1ebe4;
      --ink: #1c1c1c;
      --muted: #5a5957;
      --card: rgba(255,255,255,0.78);
      --stroke: rgba(31, 28, 24, 0.12);
      --accent: #0f6a5b;
      --accent-2: #e76f51;
      --accent-3: #264653;
      --shadow: 0 24px 60px rgba(20, 20, 18, 0.15);
      --radius: 18px;
      --chart-line: #e76f51;
      --chart-fill: rgba(231,111,81,0.22);
      --chart-grid: rgba(38,70,83,0.16);
      --chart-axis: rgba(28,28,28,0.55);
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Space Grotesk", "Trebuchet MS", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(1200px 600px at 15% 10%, rgba(231,111,81,0.15), transparent 55%),
        radial-gradient(1000px 500px at 90% 5%, rgba(38,70,83,0.2), transparent 60%),
        linear-gradient(180deg, var(--bg), var(--bg-2));
      min-height: 100vh;
    }

    .page {
      max-width: 1200px;
      margin: 0 auto;
      padding: 40px 24px 64px;
      display: flex;
      flex-direction: column;
      gap: 28px;
      animation: fade-in 0.6s ease-out;
    }

    header.hero {
      display: flex;
      flex-direction: column;
      gap: 16px;
      padding: 18px 22px;
      border-radius: 24px;
      background: linear-gradient(140deg, rgba(255,255,255,0.9), rgba(255,255,255,0.6));
      border: 1px solid var(--stroke);
      box-shadow: var(--shadow);
      position: relative;
      overflow: hidden;
    }

    .hero::after {
      content: "";
      position: absolute;
      right: -120px;
      top: -160px;
      width: 360px;
      height: 360px;
      border-radius: 50%;
      background: radial-gradient(circle at 30% 30%, rgba(15,106,91,0.25), transparent 65%);
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      font-weight: 600;
      letter-spacing: 0.02em;
      text-transform: uppercase;
      color: var(--accent-3);
      font-size: 12px;
    }

    .brand .badge {
      padding: 4px 10px;
      border-radius: 999px;
      background: rgba(38,70,83,0.1);
      border: 1px solid rgba(38,70,83,0.15);
    }

    h1 {
      font-family: "Fraunces", "Times New Roman", serif;
      font-weight: 700;
      font-size: clamp(24px, 2.6vw, 38px);
      margin: 0;
    }

    .hero p {
      margin: 0;
      color: var(--muted);
      max-width: 720px;
      line-height: 1.6;
    }

    .layout {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 20px;
      min-width: 0;
    }

    .hero-links {
      display: flex;
      gap: 12px;
      align-items: center;
      margin-top: 8px;
    }

    .hero-links a {
      text-decoration: none;
      color: var(--accent-3);
      font-size: 13px;
      font-weight: 600;
      padding: 6px 12px;
      border-radius: 999px;
      background: rgba(38,70,83,0.12);
    }

    .card {
      background: var(--card);
      border: 1px solid var(--stroke);
      border-radius: var(--radius);
      padding: 22px;
      box-shadow: 0 16px 30px rgba(20, 18, 16, 0.08);
      backdrop-filter: blur(12px);
      animation: rise 0.6s ease-out;
      min-width: 0;
    }

    .composer { grid-column: span 7; }
    .outputs { grid-column: span 5; display: flex; flex-direction: column; gap: 16px; }
    .connections { grid-column: span 12; display: grid; grid-template-columns: repeat(12, 1fr); gap: 16px; }
    .connections .card { grid-column: span 6; }

    .section-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 16px;
    }

    .section-title h2 {
      font-size: 18px;
      margin: 0;
    }

    .field {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-bottom: 14px;
      font-size: 13px;
      color: var(--muted);
    }

    textarea,
    input[type=text],
    input[type=password],
    input[type=search],
    input[type=date],
    input[type=file],
    select {
      width: 100%;
      padding: 12px 14px;
      border-radius: 12px;
      border: 1px solid rgba(34, 30, 24, 0.18);
      background: rgba(255,255,255,0.9);
      font-family: inherit;
      font-size: 14px;
      color: var(--ink);
    }

    textarea { min-height: 160px; resize: vertical; }

    .grid-2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
    .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }

    .inline {
      display: flex;
      align-items: center;
      gap: 10px;
      color: var(--muted);
      font-size: 13px;
    }

    .inline input[type=checkbox] { transform: translateY(1px); }

    .actions {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }

    button {
      border: none;
      border-radius: 999px;
      padding: 10px 18px;
      font-weight: 600;
      font-family: inherit;
      cursor: pointer;
      transition: transform 0.2s ease, box-shadow 0.2s ease, background 0.2s ease;
    }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.5;
      transform: none !important;
      box-shadow: none !important;
    }

    .btn-primary {
      background: var(--accent);
      color: white;
      box-shadow: 0 12px 30px rgba(15,106,91,0.25);
    }

    .btn-primary:hover { transform: translateY(-1px); box-shadow: 0 16px 32px rgba(15,106,91,0.3); }
    .btn-ghost { background: rgba(38,70,83,0.12); color: var(--accent-3); }
    .btn-ghost:hover { background: rgba(38,70,83,0.2); }
    .btn-soft { background: rgba(231,111,81,0.16); color: #8e3c28; }

    .pill {
      display: inline-flex;
      padding: 6px 12px;
      border-radius: 999px;
      background: rgba(15,106,91,0.1);
      color: var(--accent);
      font-size: 12px;
    }

    .log {
      white-space: pre-wrap;
      font-family: "Space Grotesk", "Trebuchet MS", sans-serif;
      background: rgba(255,255,255,0.8);
      padding: 12px;
      border-radius: 12px;
      min-height: 100px;
      border: 1px solid rgba(34, 30, 24, 0.1);
      color: #1f1f1f;
    }

    .garmin-connection-detail,
    .garmin-upload-status {
      display: flex;
      gap: 12px;
      padding: 14px;
      border: 1px solid rgba(34, 30, 24, 0.12);
      border-radius: 14px;
      background: rgba(255,255,255,0.78);
    }

    .garmin-state-icon {
      display: grid;
      place-items: center;
      flex: 0 0 30px;
      width: 30px;
      height: 30px;
      border-radius: 50%;
      background: rgba(38,70,83,0.1);
      color: var(--accent-3);
      font-weight: 700;
    }

    .garmin-state-copy { min-width: 0; flex: 1; }
    .garmin-state-copy strong { display: block; font-size: 14px; }
    .garmin-state-copy span { display: block; margin-top: 3px; color: var(--muted); font-size: 12px; line-height: 1.45; }
    .garmin-state-copy .garmin-state-meta { font-size: 11px; opacity: 0.8; }

    .garmin-connection-detail.is-success,
    .garmin-upload-status.is-success { border-color: rgba(15,106,91,0.3); background: rgba(15,106,91,0.07); }
    .garmin-connection-detail.is-success .garmin-state-icon,
    .garmin-upload-status.is-success .garmin-state-icon { background: var(--accent); color: white; }
    .garmin-connection-detail.is-warning,
    .garmin-upload-status.is-warning { border-color: rgba(198,125,34,0.35); background: rgba(244,174,65,0.09); }
    .garmin-connection-detail.is-warning .garmin-state-icon,
    .garmin-upload-status.is-warning .garmin-state-icon { background: #c67d22; color: white; }
    .garmin-connection-detail.is-error,
    .garmin-upload-status.is-error { border-color: rgba(180,59,45,0.32); background: rgba(231,111,81,0.09); }
    .garmin-connection-detail.is-error .garmin-state-icon,
    .garmin-upload-status.is-error .garmin-state-icon { background: #b43b2d; color: white; }

    .garmin-upload-status { margin-top: 12px; }
    .garmin-upload-results { display: grid; gap: 8px; margin-top: 10px; }
    .garmin-upload-result { display: grid; grid-template-columns: 22px minmax(0, 1fr); gap: 8px; align-items: start; }
    .garmin-upload-result b { overflow-wrap: anywhere; font-size: 12px; }
    .garmin-upload-result span { margin-top: 1px; }
    .garmin-upload-result-icon { font-weight: 700; color: var(--accent); }
    .garmin-upload-result.is-error .garmin-upload-result-icon { color: #b43b2d; }

    .garmin-selection-count {
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }

    .dropzone {
      border: 2px dashed rgba(38,70,83,0.25);
      border-radius: 16px;
      padding: 14px;
      background: rgba(255,255,255,0.7);
    }

    .dropzone.dragover {
      border-color: var(--accent);
      background: rgba(15,106,91,0.08);
    }

    .fit-chart {
      margin-top: 12px;
      padding: 12px;
      border-radius: 16px;
      border: 1px solid rgba(34, 30, 24, 0.12);
      background: rgba(255,255,255,0.72);
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .fit-chart.hidden { display: none; }

    .fit-chart-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }

    .fit-select {
      border-radius: 999px;
      border: 1px solid rgba(34, 30, 24, 0.2);
      padding: 6px 10px;
      font-size: 12px;
      background: rgba(255,255,255,0.9);
      color: var(--ink);
    }

    .fit-select.hidden { display: none; }

    #fit_chart {
      width: 100%;
      height: 220px;
      border-radius: 12px;
      border: 1px solid rgba(34, 30, 24, 0.12);
      background: linear-gradient(180deg, rgba(255,255,255,0.9), rgba(250,247,242,0.9));
    }

    .footnote {
      font-size: 12px;
      color: var(--muted);
    }

    @keyframes fade-in {
      from { opacity: 0; transform: translateY(8px); }
      to { opacity: 1; transform: translateY(0); }
    }

    @keyframes rise {
      from { opacity: 0; transform: translateY(12px); }
      to { opacity: 1; transform: translateY(0); }
    }

    @media (max-width: 980px) {
      .layout { grid-template-columns: repeat(1, 1fr); }
      .composer, .outputs, .connections, .connections .card { grid-column: span 1; min-width: 0; }
      .connections { grid-template-columns: 1fr; }
    }

    @media (max-width: 600px) {
      .page { padding: 20px 12px 40px; gap: 18px; }
      .card { padding: 18px 14px; }
      .grid-2, .grid-3 { grid-template-columns: 1fr; }
      .hero-links { align-items: flex-start; flex-wrap: wrap; }
      .section-title { gap: 10px; }
    }
  </style>
  </head>
  <body>
    <div class="page">
      <header class="hero">
        <div class="brand">
          <span class="badge">Local-first</span>
          Prompt → FIT Studio
        </div>
        <h1>Turn raw training notes into clean Garmin workouts.</h1>
        <p>Paste your plan, choose a race pace, and generate FIT workouts instantly. Everything stays on your machine unless you opt into Garmin Connect.</p>
        <div class="actions">
          <span class="pill">Spec-compliant FIT output</span>
          <span class="pill">Targets + repeats included</span>
          <span class="pill">macOS Keychain support</span>
        </div>
        <div class="hero-links">
          <a href="/plan">Open Plan Builder →</a>
          <a href="/fit-editor">Open FIT Editor →</a>
          <a href="#garmin-connect">Phone FIT upload →</a>
        </div>
      </header>

      <main class="layout">
        <section class="card composer">
          <div class="section-title">
            <h2>Workout Composer</h2>
            <span class="pill">Prompt → Plan → FIT</span>
          </div>
          <label class="field">
            Workout prompt
            <textarea id="prompt" placeholder="e.g., 4 × 2 km @ 92–94% with 2 min walk. 10 min rest, then 5 × 300m @ 110–112% with 2–3 min walk."></textarea>
          </label>

          <div class="grid-2">
            <label class="field">
              Provider
              <select id="provider">
                <option value="auto">auto</option>
                <option value="openai">openai</option>
                <option value="openrouter">openrouter</option>
              </select>
            </label>
            <label class="field">
              Race distance
              <select id="race_distance">
                <option value="5k">5k</option>
                <option value="10k">10k</option>
                <option value="half marathon" selected>half marathon</option>
                <option value="marathon">marathon</option>
              </select>
            </label>
          </div>

          <div class="grid-2">
            <label class="field">
              OpenAI API key
              <input id="openai_api_key" type="password" placeholder="sk-..." autocomplete="off">
            </label>
            <label class="field">
              OpenRouter API key
              <input id="openrouter_api_key" type="password" placeholder="or-..." autocomplete="off">
            </label>
          </div>
          <label class="field">
            OpenAI model (optional)
            <input id="openai_model" type="text" placeholder="e.g., gpt-4o-mini">
          </label>
          <label class="field">
            OpenRouter model (optional)
            <input id="openrouter_model" type="text" placeholder="e.g., openrouter/auto or anthropic/claude-3.5-sonnet">
          </label>

          <div class="grid-3">
            <label class="field">
              Race pace (min/mi)
              <input id="hmp" type="text" placeholder="6:30 or 6.50">
            </label>
            <label class="field">
              Target margin (sec/mi)
              <input id="margin" type="text" value="30">
            </label>
            <label class="field">
              Target mode
              <select id="tmode">
                <option>pace</option>
                <option>speed</option>
              </select>
            </label>
          </div>

          <div class="inline">
            <label class="inline"><input id="targets" type="checkbox"> Enable targets</label>
            <label class="inline"><input id="sideload" type="checkbox"> Auto-sideload to watch</label>
          </div>

          <div class="actions" style="margin-top:16px;">
            <button class="btn-primary" onclick="run()">Generate FIT</button>
            <button class="btn-ghost" onclick="previewPlan()">Preview Plan JSON</button>
          </div>
        </section>

        <section class="card outputs">
          <div class="section-title">
            <h2>Outputs</h2>
            <span class="pill">Live logs + parser</span>
          </div>
          <div class="field">
            FIT Parser
            <div id="fit_dropzone" class="dropzone">
              <div class="inline" style="margin-bottom:8px;">
                <input id="fit_files" type="file" accept=".fit" multiple>
                <button class="btn-soft" onclick="parseFits()">Parse FIT</button>
              </div>
              <div class="log" id="fit_parse_out" style="min-height:140px;overflow:auto;"></div>
              <div id="fit_chart_wrap" class="fit-chart hidden">
                <div class="fit-chart-head">
                  <span>Workout Graph</span>
                  <select id="fit_chart_select" class="fit-select hidden"></select>
                </div>
                <canvas id="fit_chart" height="220"></canvas>
                <div class="footnote" id="fit_chart_note"></div>
              </div>
            </div>
          </div>

          <div class="field">
            Plan JSON Preview
            <div class="log" id="plan_json" style="min-height:140px;overflow:auto;"></div>
          </div>

          <div class="field">
            Activity Log
            <div class="log" id="log"></div>
          </div>
        </section>

        <section class="connections">
          <div class="card">
            <div class="section-title">
              <h2>Local Keychain</h2>
            </div>
            <p class="footnote">Stored locally on this Mac (localhost only). Optional, but convenient for repeated sessions.</p>
            <div class="actions" style="margin-top:12px;">
              <button class="btn-ghost" onclick="saveSecrets()">Save secrets (Keychain)</button>
            </div>
          </div>

          <div class="card" id="garmin-connect">
            <div class="section-title">
              <h2>Garmin connection</h2>
              <span class="pill" id="gc_connection_badge">Checking…</span>
            </div>
            <div class="garmin-connection-detail" id="gc_connection_detail" role="status" aria-live="polite">
              <span class="garmin-state-icon" id="gc_connection_icon" aria-hidden="true">…</span>
              <div class="garmin-state-copy">
                <strong id="gc_connection_title">Checking Garmin…</strong>
                <span id="gc_connection_note">Confirming that Garmin accepts the saved connection on this Mac.</span>
                <span class="garmin-state-meta" id="gc_connection_meta"></span>
              </div>
            </div>
            <div id="gc_connect_form" style="display:none;margin-top:12px;">
              <p class="footnote">One-time setup: open this page at <b>http://localhost:8000</b> on the Mac, then sign in once. Only Garmin session tokens are saved; the password is discarded.</p>
              <div class="grid-2" style="margin-top:12px;">
                <label class="field">
                  Garmin username
                  <input id="gc_username" type="text" placeholder="you@example.com" autocomplete="username">
                </label>
                <label class="field">
                  Garmin password
                  <input id="gc_password" type="password" placeholder="••••••••" autocomplete="current-password">
                </label>
              </div>
              <div class="actions" style="margin-top:12px;">
                <button class="btn-primary" onclick="connectGarmin()">Connect Garmin once</button>
              </div>
            </div>
            <div class="actions" id="gc_connected_actions" style="display:none;margin-top:12px;">
              <button class="btn-primary" id="gc_check_connection_btn" onclick="loadGarminStatus()">Check connection</button>
              <button class="btn-ghost" onclick="disconnectGarmin()">Disconnect Garmin</button>
            </div>
            <div id="gc_mfa_box" style="display:none;margin-top:14px;">
              <label class="field">
                Garmin verification code
                <input id="gc_mfa_code" type="text" inputmode="numeric" autocomplete="one-time-code" placeholder="Enter the code Garmin sent">
              </label>
              <div class="actions">
                <button class="btn-primary" onclick="finishGarminMfa()">Verify and save connection</button>
              </div>
            </div>
            <div class="footnote" id="gc_log" role="status" aria-live="polite" style="margin-top:10px;"></div>

            <div style="height:1px;background:var(--stroke);margin:22px 0;"></div>
            <div class="section-title">
              <h2>Choose workouts to upload</h2>
              <span class="pill">Manual selection</span>
            </div>
            <p class="footnote">Nothing uploads automatically. Check the exact workouts you want, then press the upload button. This list shows FIT files in the Mac's <b>fit_out_gui</b> folder, even when you open this page from your phone.</p>
            <label class="field" style="margin-top:12px;">
              Find a workout
              <input id="gc_local_fit_search" type="search" placeholder="Search by workout name or date">
            </label>
            <div id="gc_local_fit_list" class="log" style="margin-top:10px;max-height:310px;overflow:auto;">Loading workouts from this Mac…</div>
            <label class="field" style="margin-top:12px;">
              Schedule date for checked or chosen workouts (optional)
              <input id="gc_schedule_date" type="date">
            </label>
            <div class="actions" style="margin-top:12px;">
              <button class="btn-primary" id="gc_local_upload_btn" onclick="uploadSelectedLocalFits()">Upload checked workouts</button>
              <button class="btn-ghost" onclick="loadLocalFitLibrary()">Refresh list</button>
              <span class="garmin-selection-count" id="gc_selection_count">0 selected</span>
            </div>
            <div class="garmin-upload-status" id="gc_upload_status" role="status" aria-live="polite">
              <span class="garmin-state-icon" id="gc_upload_icon" aria-hidden="true">↑</span>
              <div class="garmin-state-copy">
                <strong id="gc_upload_title">No upload attempted yet</strong>
                <span id="gc_upload_detail">After an upload, Garmin’s confirmation and each workout ID will appear here.</span>
                <span class="garmin-state-meta" id="gc_upload_meta"></span>
                <div class="garmin-upload-results" id="gc_upload_results"></div>
              </div>
            </div>

            <div style="height:1px;background:var(--stroke);margin:20px 0 16px;"></div>
            <p class="footnote"><b>Or choose files from this device</b> if the workout is not in the Mac list.</p>
            <label class="field" style="margin-top:12px;">
              Workout FIT files on this device
              <input id="gc_fit_files" type="file" accept=".fit,application/octet-stream" multiple>
            </label>
            <div class="actions" style="margin-top:12px;">
              <button class="btn-soft" id="gc_upload_btn" onclick="uploadFitsToGarmin()">Upload files from this device</button>
              <button class="btn-soft" id="gc_prompt_upload_btn" onclick="sendGarmin()">Upload current prompt workout</button>
            </div>
          </div>
        </section>
      </main>
    </div>
    <script src="/static/app.js?v=__APP_JS_VERSION__"></script>
  </body>
  </html>
"""


PLAN_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Plan Builder → Calendar</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    @import url("https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=Space+Grotesk:wght@400;500;600&display=swap");

    :root {
      --bg: #f5f1eb;
      --bg-2: #ece6de;
      --ink: #1c1c1c;
      --muted: #5a5957;
      --card: rgba(255,255,255,0.8);
      --stroke: rgba(31, 28, 24, 0.12);
      --accent: #284b63;
      --accent-2: #e9c46a;
      --accent-3: #2a9d8f;
      --shadow: 0 24px 60px rgba(20, 20, 18, 0.15);
      --radius: 18px;
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Space Grotesk", "Trebuchet MS", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(1000px 520px at 5% 10%, rgba(42,157,143,0.18), transparent 60%),
        radial-gradient(900px 420px at 90% 20%, rgba(233,196,106,0.3), transparent 60%),
        linear-gradient(180deg, var(--bg), var(--bg-2));
      min-height: 100vh;
    }

    .page {
      max-width: 1200px;
      margin: 0 auto;
      padding: 40px 24px 64px;
      display: flex;
      flex-direction: column;
      gap: 28px;
      animation: fade-in 0.6s ease-out;
    }

    header.hero {
      display: flex;
      flex-direction: column;
      gap: 14px;
      padding: 18px 22px;
      border-radius: 24px;
      background: linear-gradient(140deg, rgba(255,255,255,0.9), rgba(255,255,255,0.62));
      border: 1px solid var(--stroke);
      box-shadow: var(--shadow);
      position: relative;
      overflow: hidden;
    }

    .hero::after {
      content: "";
      position: absolute;
      right: -120px;
      top: -140px;
      width: 320px;
      height: 320px;
      border-radius: 50%;
      background: radial-gradient(circle at 30% 30%, rgba(42,157,143,0.25), transparent 65%);
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      font-weight: 600;
      letter-spacing: 0.02em;
      text-transform: uppercase;
      color: var(--accent);
      font-size: 12px;
    }

    .brand .badge {
      padding: 4px 10px;
      border-radius: 999px;
      background: rgba(42,157,143,0.12);
      border: 1px solid rgba(42,157,143,0.2);
    }

    h1 {
      font-family: "Fraunces", "Times New Roman", serif;
      font-weight: 700;
      font-size: clamp(24px, 2.6vw, 38px);
      margin: 0;
    }

    .hero p {
      margin: 0;
      color: var(--muted);
      max-width: 720px;
      line-height: 1.6;
    }

    .hero-links a {
      text-decoration: none;
      color: var(--accent);
      font-size: 13px;
      font-weight: 600;
      padding: 6px 12px;
      border-radius: 999px;
      background: rgba(42,157,143,0.12);
      display: inline-flex;
      gap: 6px;
      align-items: center;
    }

    .layout {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 20px;
    }

    .card {
      background: var(--card);
      border: 1px solid var(--stroke);
      border-radius: var(--radius);
      padding: 22px;
      box-shadow: 0 16px 30px rgba(20, 18, 16, 0.08);
      backdrop-filter: blur(12px);
      animation: rise 0.6s ease-out;
    }

    .form-card { grid-column: span 7; }
    .summary-card { grid-column: span 5; display: flex; flex-direction: column; gap: 16px; }

    .section-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 16px;
    }

    .section-title h2 {
      font-size: 18px;
      margin: 0;
    }

    .field {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-bottom: 14px;
      font-size: 13px;
      color: var(--muted);
    }

    textarea,
    input[type=text],
    input[type=password],
    input[type=date],
    input[type=file],
    select {
      width: 100%;
      padding: 12px 14px;
      border-radius: 12px;
      border: 1px solid rgba(34, 30, 24, 0.18);
      background: rgba(255,255,255,0.9);
      font-family: inherit;
      font-size: 14px;
      color: var(--ink);
    }

    .dropzone {
      border: 2px dashed rgba(38,70,83,0.25);
      border-radius: 16px;
      padding: 14px;
      background: rgba(255,255,255,0.7);
      cursor: pointer;
    }

    .dropzone.dragover {
      border-color: var(--accent);
      background: rgba(42,157,143,0.08);
    }

    .file-input { display: none; }

    .file-row {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }

    .grid-2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
    .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }

    .inline {
      display: flex;
      align-items: center;
      gap: 10px;
      color: var(--muted);
      font-size: 13px;
      flex-wrap: wrap;
    }

    button {
      border: none;
      border-radius: 999px;
      padding: 10px 18px;
      font-weight: 600;
      font-family: inherit;
      cursor: pointer;
      transition: transform 0.2s ease, box-shadow 0.2s ease, background 0.2s ease;
    }

    .btn-primary {
      background: var(--accent);
      color: white;
      box-shadow: 0 12px 30px rgba(40,75,99,0.25);
    }

    .btn-primary:hover { transform: translateY(-1px); box-shadow: 0 16px 32px rgba(40,75,99,0.3); }
    .btn-ghost { background: rgba(40,75,99,0.12); color: var(--accent); }
    .btn-ghost:hover { background: rgba(40,75,99,0.2); }

    .pill {
      display: inline-flex;
      padding: 6px 12px;
      border-radius: 999px;
      background: rgba(42,157,143,0.12);
      color: var(--accent-3);
      font-size: 12px;
    }

    .log {
      white-space: pre-wrap;
      font-family: "Space Grotesk", "Trebuchet MS", sans-serif;
      background: rgba(255,255,255,0.8);
      padding: 12px;
      border-radius: 12px;
      min-height: 120px;
      border: 1px solid rgba(34, 30, 24, 0.1);
      color: #1f1f1f;
    }

    .footnote { font-size: 12px; color: var(--muted); }

    @keyframes fade-in {
      from { opacity: 0; transform: translateY(8px); }
      to { opacity: 1; transform: translateY(0); }
    }

    @keyframes rise {
      from { opacity: 0; transform: translateY(12px); }
      to { opacity: 1; transform: translateY(0); }
    }

    @media (max-width: 980px) {
      .layout { grid-template-columns: repeat(1, 1fr); }
      .form-card, .summary-card { grid-column: span 1; }
    }
  </style>
  </head>
  <body>
    <div class="page">
      <header class="hero">
        <div class="brand">
          <span class="badge">Plan Builder</span>
          Training Plan → Calendar
        </div>
        <h1>Scale big race plans into a calendar and FIT workflow.</h1>
        <p>Upload your plan JSON, set race targets, and generate an ICS + HTML preview that matches your training volume.</p>
        <div class="hero-links">
          <a href="/">← Back to Prompt → FIT</a>
          <a href="/fit-editor">Open FIT Editor →</a>
        </div>
      </header>

      <main class="layout">
        <section class="card form-card">
          <div class="section-title">
            <h2>Plan Inputs</h2>
            <span class="pill">ICS + HTML output</span>
          </div>

          <label class="field">
            Preset plan (optional)
            <select id="plan_preset">
              <option value="">Select a preset plan...</option>
            </select>
            <div class="footnote" id="plan_preset_desc">Choose a preset to auto-load its JSON.</div>
          </label>

          <div class="field">
            Plan JSON file
            <div id="plan_dropzone" class="dropzone" style="margin-top:6px;">
              <input id="plan_file" class="file-input" type="file" accept=".json">
              <div class="file-row">
                <button class="btn-soft" type="button" onclick="triggerPlanFile()">Choose JSON</button>
                <span class="footnote" id="plan_file_label">Drag & drop a JSON plan here</span>
              </div>
            </div>
          </div>

          <div class="grid-3">
            <label class="field">
              Race date
              <input id="plan_race_date" type="date">
            </label>
            <label class="field">
              Race distance
              <select id="plan_race_distance">
                <option value="5k">5k</option>
                <option value="10k">10k</option>
                <option value="half marathon" selected>half marathon</option>
                <option value="marathon">marathon</option>
              </select>
            </label>
            <label class="field">
              Peak mileage
              <input id="plan_peak_mileage" type="text" placeholder="e.g., 70">
            </label>
          </div>

          <div class="grid-3">
            <label class="field">
              Race pace (min/mi)
              <input id="plan_race_pace" type="text" placeholder="6:30 or 6.50">
            </label>
            <label class="field">
              Easy pace (min/mi)
              <input id="plan_easy_pace" type="text" placeholder="optional">
            </label>
            <label class="field">
              Output base name
              <input id="plan_base_name" type="text" value="training_plan">
            </label>
          </div>

          <div class="inline" style="margin-top:6px;">
            <label class="inline"><input id="plan_include_wu" type="checkbox"> Include implicit WU/CD (~1mi)</label>
            <label class="inline"><input id="plan_scale_wu" type="checkbox"> Scale explicit WU/CD segments</label>
            <label class="inline"><input id="plan_collapse_doubles" type="checkbox"> Collapse doubles</label>
            <label class="inline"><input id="plan_consolidate_workouts" type="checkbox"> Consolidate workout doubles</label>
          </div>
          <div class="grid-2" style="margin-top:12px;">
            <label class="field">
              FIT file scope
              <select id="plan_fit_scope">
                <option value="workouts" selected>Workout days only (recommended)</option>
                <option value="all_runs">All running days</option>
                <option value="none">Do not generate FIT files</option>
              </select>
              <div class="footnote">Rest days never create FIT files.</div>
            </label>
            <label class="field">
              Download contents
              <select id="plan_package_mode">
                <option value="full" selected>Calendar + preview + workout FITs</option>
                <option value="fits">Workout FITs only</option>
                <option value="calendar">Calendar + preview only</option>
              </select>
            </label>
          </div>
          <div class="grid-2" style="margin-top:12px;">
            <label class="field">
              WU+CD total distance (mi)
              <input id="plan_wu_cd_distance" type="text" placeholder="e.g., 1.0">
            </label>
            <label class="field">
              WU+CD total time (min)
              <input id="plan_wu_cd_duration" type="text" placeholder="e.g., 12">
            </label>
          </div>

          <div class="grid-3" style="margin-top:14px;">
            <label class="field">
              Rest days / week
              <input id="plan_rest_days" type="text" value="0">
            </label>
            <label class="field">
              Workout factor mode
              <select id="plan_wf_mode">
                <option value="same">same as base</option>
                <option value="normalize">normalize to peak mileage</option>
                <option value="custom">custom multiplier</option>
              </select>
              <div class="footnote" id="plan_wf_ratio_note">Peak ratio: —</div>
            </label>
            <label class="field">
              Custom multiplier (of original plan)
              <input id="plan_wf_value" type="text" placeholder="e.g., 0.9 or 1.1">
            </label>
          </div>

          <div class="inline">
            <label class="inline"><input id="plan_redistribute" type="checkbox" checked> Redistribute removed mileage</label>
            <label class="inline"><input id="plan_normalize" type="checkbox" checked> Normalize weekly miles</label>
            <label class="inline"><input id="plan_norm_reduce" type="checkbox"> Also reduce for fast athletes</label>
          </div>

          <div class="card" style="margin-top:16px;padding:16px;background:rgba(227,238,233,.45);">
            <label class="inline"><input id="plan_schedule_garmin" type="checkbox"> Upload generated workout FITs to Garmin</label>
            <div class="grid-2" id="plan_garmin_options" style="margin-top:12px;">
              <label class="field">
                Rolling upload window
                <span class="inline"><input id="plan_garmin_weeks" type="number" min="1" max="52" value="4" style="max-width:90px;"> weeks starting today</span>
              </label>
              <label class="inline"><input id="plan_garmin_replace" type="checkbox" checked> Replace earlier PromptFit uploads in this window</label>
            </div>
            <div class="footnote">Replace removes only workouts this app has tracked, then schedules the refreshed versions. Unrelated Garmin workouts are left alone.</div>
            <div class="log" id="plan_garmin_status" style="display:none;margin-top:10px;min-height:0;"></div>
          </div>

          <div class="actions" style="margin-top:18px;">
            <button class="btn-primary" onclick="generatePlan()">Generate Calendar</button>
            <button class="btn-ghost" onclick="resetPlanForm()">Reset</button>
          </div>
        </section>

        <section class="card summary-card">
          <div class="section-title">
            <h2>Output</h2>
            <span class="pill">Plan status</span>
          </div>
          <div class="log" id="plan_status">Upload a JSON plan to begin.</div>
          <div class="inline" id="plan_preview_link" style="display:none;">
            <a class="pill" href="#" target="_blank" rel="noopener">Open HTML preview</a>
          </div>
          <div class="footnote">Your selected download contents are bundled into one ZIP. The live HTML preview remains available for day and week Garmin actions.</div>
        </section>

        <section class="card builder-card" style="grid-column: span 12;">
          <div class="section-title">
            <h2>LLM Plan → JSON Builder</h2>
            <span class="pill">Plain text to JSON</span>
          </div>
          <label class="field">
            Paste full plan text
            <textarea id="plan_text" placeholder="Week 1: Monday 45 min easy..."></textarea>
          </label>
          <div class="grid-3">
            <label class="field">
              Provider
              <select id="plan_provider">
                <option value="auto">auto</option>
                <option value="openai">openai</option>
                <option value="openrouter">openrouter</option>
              </select>
            </label>
            <label class="field">
              OpenAI API key
              <input id="openai_api_key" type="password" placeholder="sk-..." autocomplete="off">
            </label>
            <label class="field">
              OpenRouter API key
              <input id="openrouter_api_key" type="password" placeholder="or-..." autocomplete="off">
            </label>
          </div>
          <label class="field">
            OpenAI model (optional)
            <input id="openai_model" type="text" placeholder="e.g., gpt-4o-mini">
          </label>
          <label class="field">
            OpenRouter model (optional)
            <input id="openrouter_model" type="text" placeholder="e.g., openrouter/auto or anthropic/claude-3.5-sonnet">
          </label>
          <div class="actions" style="margin-top:12px;">
            <button class="btn-primary" onclick="buildPlanJson()">Build JSON</button>
            <button class="btn-ghost" onclick="downloadPlanJson()">Download JSON</button>
          </div>
          <div class="field" style="margin-top:16px;">
            JSON output
            <div class="log" id="plan_json_out" style="min-height:160px;overflow:auto;"></div>
          </div>
        </section>
      </main>
    </div>
    <script src="/static/app.js?v=__APP_JS_VERSION__"></script>
  </body>
  </html>
"""


FIT_EDITOR_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>FIT Leg Editor</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    @import url("https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=Manrope:wght@400;500;600;700&display=swap");

    :root {
      --bg: #f2f4ef;
      --bg-2: #e7ece2;
      --ink: #172018;
      --muted: #4a5b4b;
      --card: rgba(255,255,255,0.84);
      --stroke: rgba(23, 32, 24, 0.12);
      --accent: #1f6f4a;
      --accent-2: #d97745;
      --accent-3: #2d4a7e;
      --shadow: 0 20px 50px rgba(20, 24, 18, 0.14);
      --radius: 18px;
      --line: #d97745;
      --fill: rgba(217,119,69,0.2);
      --grid: rgba(45,74,126,0.18);
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Manrope", "Trebuchet MS", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(900px 440px at 10% 12%, rgba(31,111,74,0.18), transparent 60%),
        radial-gradient(860px 420px at 90% 16%, rgba(217,119,69,0.2), transparent 60%),
        linear-gradient(180deg, var(--bg), var(--bg-2));
      min-height: 100vh;
    }

    .page {
      max-width: 1280px;
      margin: 0 auto;
      padding: 34px 22px 60px;
      display: flex;
      flex-direction: column;
      gap: 22px;
      animation: fade-in 0.6s ease-out;
    }

    .hero {
      padding: 20px 22px;
      border-radius: 22px;
      background: linear-gradient(140deg, rgba(255,255,255,0.92), rgba(255,255,255,0.62));
      border: 1px solid var(--stroke);
      box-shadow: var(--shadow);
      display: flex;
      flex-direction: column;
      gap: 12px;
      position: relative;
      overflow: hidden;
    }
    .hero::after {
      content: "";
      position: absolute;
      width: 320px;
      height: 320px;
      border-radius: 50%;
      right: -130px;
      top: -150px;
      background: radial-gradient(circle at 35% 35%, rgba(45,74,126,0.28), transparent 66%);
    }
    .brand {
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--accent-3);
      font-weight: 700;
    }
    h1 {
      font-family: "DM Serif Display", "Times New Roman", serif;
      margin: 0;
      font-size: clamp(28px, 3vw, 40px);
      line-height: 1.1;
    }
    .hero p {
      margin: 0;
      max-width: 760px;
      color: var(--muted);
    }
    .hero-links { display: flex; gap: 10px; flex-wrap: wrap; }
    .hero-links a {
      text-decoration: none;
      color: var(--accent-3);
      font-size: 13px;
      font-weight: 700;
      padding: 7px 13px;
      border-radius: 999px;
      background: rgba(45,74,126,0.12);
    }

    .layout {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 16px;
    }
    .workbench {
      grid-column: span 12;
      display: grid;
      grid-template-columns: minmax(300px, 1fr) minmax(360px, 1.15fr) minmax(440px, 1.35fr);
      gap: 16px;
      align-items: start;
      overflow-x: auto;
      padding-bottom: 6px;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--stroke);
      border-radius: var(--radius);
      padding: 18px;
      box-shadow: 0 14px 28px rgba(22, 24, 20, 0.08);
      backdrop-filter: blur(10px);
      animation: rise 0.55s ease-out;
    }
    .controls { display: flex; flex-direction: column; gap: 12px; }
    .editor { display: flex; flex-direction: column; gap: 10px; }
    .preview { display: flex; flex-direction: column; gap: 10px; }
    .inspector { grid-column: span 12; }

    .title {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 8px;
    }
    .title h2 { margin: 0; font-size: 18px; }
    .pill {
      border-radius: 999px;
      padding: 5px 10px;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      background: rgba(31,111,74,0.12);
      color: var(--accent);
    }

    label { font-size: 12px; color: var(--muted); display: flex; flex-direction: column; gap: 6px; }
    input[type=text], input[type=number], select {
      width: 100%;
      border: 1px solid rgba(24, 28, 22, 0.2);
      border-radius: 12px;
      padding: 10px 12px;
      font-family: inherit;
      background: rgba(255,255,255,0.92);
      color: var(--ink);
    }

    .row { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
    .row-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
    .inline { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); }

    .drop {
      border: 2px dashed rgba(45,74,126,0.26);
      border-radius: 14px;
      padding: 12px;
      background: rgba(255,255,255,0.7);
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .drop.dragover {
      border-color: var(--accent);
      background: rgba(31,111,74,0.08);
    }

    button {
      border: none;
      border-radius: 999px;
      padding: 9px 15px;
      font-family: inherit;
      font-weight: 700;
      cursor: pointer;
      transition: transform 0.18s ease, box-shadow 0.18s ease, background 0.18s ease;
    }
    .btn-primary { background: var(--accent); color: #fff; box-shadow: 0 10px 24px rgba(31,111,74,0.28); }
    .btn-primary:hover { transform: translateY(-1px); }
    .btn-ghost { background: rgba(45,74,126,0.12); color: var(--accent-3); }
    .btn-soft { background: rgba(217,119,69,0.18); color: #8a3e1f; }
    .actions { display: flex; gap: 8px; flex-wrap: wrap; }

    .status {
      white-space: pre-wrap;
      min-height: 86px;
      border-radius: 12px;
      border: 1px solid rgba(22,28,22,0.1);
      background: rgba(255,255,255,0.75);
      padding: 10px;
      font-size: 13px;
      color: #1f281f;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
    }
    .metric {
      border-radius: 12px;
      border: 1px solid rgba(22,28,22,0.1);
      background: rgba(255,255,255,0.76);
      padding: 9px;
      text-align: center;
    }
    .metric b { display: block; font-size: 16px; color: var(--accent-3); }
    .metric span { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }

    #fe_legs {
      display: flex;
      flex-direction: column;
      gap: 10px;
      max-height: 66vh;
      overflow: auto;
      padding-right: 3px;
    }
    .leg {
      border-radius: 14px;
      border: 1px solid rgba(22,28,22,0.12);
      background: rgba(255,255,255,0.78);
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 9px;
    }
    .leg-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
    .leg-head-left { display: flex; align-items: center; gap: 8px; }
    .leg-badge {
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      border-radius: 999px;
      padding: 4px 9px;
      background: rgba(31,111,74,0.12);
      color: var(--accent);
    }
    .leg-tools { display: flex; gap: 6px; flex-wrap: wrap; }
    .leg-tools button { padding: 6px 10px; font-size: 12px; }
    .drag-handle {
      width: 22px;
      height: 22px;
      border-radius: 8px;
      border: 1px solid rgba(22,28,22,0.16);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: rgba(255,255,255,0.9);
      color: var(--muted);
      font-size: 13px;
      user-select: none;
      cursor: grab;
    }
    .drag-handle:active { cursor: grabbing; }
    .leg.dragging {
      opacity: 0.56;
      transform: scale(0.995);
    }
    .leg.selected {
      border-color: rgba(45,74,126,0.55);
      box-shadow: 0 0 0 2px rgba(45,74,126,0.18);
      background: rgba(235,242,255,0.72);
    }
    .leg-drop-slot {
      height: 10px;
      margin: 2px 10px;
      border-radius: 999px;
      border: 1px dashed transparent;
      transition: all 0.12s ease;
    }
    .leg-drop-slot.active {
      height: 16px;
      border-color: rgba(31,111,74,0.55);
      background: rgba(31,111,74,0.16);
      margin: 4px 8px;
    }
    .repeat-bundle {
      border: 1px dashed rgba(45,74,126,0.28);
      border-radius: 12px;
      padding: 9px;
      background: rgba(45,74,126,0.05);
    }
    .repeat-bundle-head {
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--accent-3);
      margin-bottom: 6px;
    }
    .repeat-chip-row {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .repeat-chip {
      border: 1px solid rgba(22,28,22,0.14);
      border-radius: 10px;
      padding: 6px 8px;
      background: rgba(255,255,255,0.86);
      font-size: 11px;
      line-height: 1.35;
      min-width: 140px;
    }
    .repeat-chip strong {
      display: block;
      font-size: 10px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 2px;
    }
    .repeat-chip-empty {
      color: var(--muted);
      font-style: italic;
    }
    .repeat-row {
      border: 1px solid rgba(22,28,22,0.14);
      border-radius: 10px;
      padding: 8px;
      background: rgba(255,255,255,0.9);
    }
    .repeat-row.dragging {
      opacity: 0.55;
      transform: scale(0.995);
    }
    .repeat-row.selected {
      border-color: rgba(45,74,126,0.55);
      box-shadow: inset 0 0 0 2px rgba(45,74,126,0.16);
      background: rgba(235,242,255,0.72);
    }
    .repeat-row-head {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 6px;
    }
    .repeat-row-handle {
      width: 22px;
      height: 22px;
      border-radius: 8px;
      border: 1px solid rgba(22,28,22,0.16);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: rgba(255,255,255,0.94);
      color: var(--muted);
      font-size: 13px;
      user-select: none;
      cursor: grab;
      flex: 0 0 auto;
    }
    .repeat-row-handle:active { cursor: grabbing; }
    .repeat-row-title {
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted);
    }
    .repeat-sub-drop-slot {
      height: 10px;
      margin: 2px 4px;
      border-radius: 999px;
      border: 1px dashed transparent;
      transition: all 0.12s ease;
    }
    .repeat-sub-drop-slot.active {
      height: 14px;
      border-color: rgba(31,111,74,0.65);
      background: rgba(31,111,74,0.16);
      margin: 3px 2px;
    }
    .repeat-drop-target {
      margin-top: 8px;
      border-radius: 10px;
      border: 1px dashed rgba(31,111,74,0.45);
      padding: 8px;
      font-size: 11px;
      color: var(--muted);
      background: rgba(31,111,74,0.05);
    }
    .repeat-drop-target.active {
      border-color: rgba(31,111,74,0.7);
      background: rgba(31,111,74,0.16);
      color: var(--ink);
      font-weight: 600;
    }

    .chart-wrap {
      position: relative;
      border-radius: 14px;
      border: 1px solid rgba(22,28,22,0.13);
      background: rgba(255,255,255,0.8);
      padding: 10px;
    }
    #fe_chart {
      width: 100%;
      height: 420px;
      border: 1px solid rgba(22,28,22,0.12);
      border-radius: 10px;
      background: linear-gradient(180deg, rgba(255,255,255,0.92), rgba(249,247,241,0.95));
      cursor: pointer;
    }
    .chart-tip {
      position: absolute;
      z-index: 20;
      min-width: 190px;
      max-width: 240px;
      border: 1px solid rgba(22,28,22,0.16);
      background: rgba(255,255,255,0.97);
      color: var(--ink);
      border-radius: 10px;
      padding: 8px 10px;
      box-shadow: 0 12px 28px rgba(22,28,22,0.2);
      font-size: 12px;
      line-height: 1.4;
      pointer-events: none;
      transform: translate(-50%, calc(-100% - 10px));
      white-space: normal;
    }
    .chart-tip.hidden { display: none; }
    .chart-tip.below { transform: translate(-50%, 12px); }
    .chart-tip strong {
      display: block;
      margin-bottom: 4px;
      font-size: 12px;
      color: var(--accent-3);
    }
    #fe_chart_note { font-size: 12px; color: var(--muted); margin-top: 8px; }
    #fe_json {
      min-height: 180px;
      white-space: pre-wrap;
      font-size: 12px;
      border: 1px solid rgba(22,28,22,0.12);
      border-radius: 12px;
      padding: 12px;
      background: rgba(255,255,255,0.82);
      overflow: auto;
      max-height: 280px;
    }

    @keyframes fade-in {
      from { opacity: 0; transform: translateY(8px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @keyframes rise {
      from { opacity: 0; transform: translateY(10px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @media (max-width: 1024px) {
      .workbench {
        grid-template-columns: 300px 360px 460px;
      }
      #fe_legs { max-height: none; }
      #fe_chart { height: 300px; }
    }
  </style>
</head>
<body>
  <div class="page">
    <header class="hero">
      <div class="brand">FIT Leg Editor</div>
      <h1>Edit one leg or build the full workout from scratch.</h1>
      <p>Import a FIT file, adjust any interval/rest leg, preview the workout shape, and export a deterministic FIT file that preserves your exact leg sequence.</p>
      <div class="hero-links">
        <a href="/">Prompt → FIT</a>
        <a href="/plan">Plan Builder</a>
      </div>
    </header>

    <main class="layout">
      <div class="workbench">
      <section class="card controls">
        <div class="title">
          <h2>Inputs</h2>
          <span class="pill">Import or start blank</span>
        </div>
        <div id="fe_drop" class="drop">
          <input id="fit_editor_file" type="file" accept=".fit">
          <div class="actions">
            <button class="btn-soft" type="button" id="fe_parse_btn">Load FIT</button>
            <button class="btn-ghost" type="button" id="fe_blank_btn">New Blank</button>
          </div>
        </div>
        <label>
          Workout name
          <input id="fe_name" type="text" value="Workout">
        </label>
        <label class="inline">
          <input id="fe_deterministic" type="checkbox" checked>
          Deterministic export metadata
        </label>
        <div class="actions">
          <button class="btn-ghost" type="button" id="fe_add_step_btn">+ Add Step</button>
          <button class="btn-ghost" type="button" id="fe_add_repeat_btn">+ Add Repeat</button>
          <button class="btn-soft" type="button" id="fe_template_btn">Load Interval Template</button>
          <button class="btn-primary" type="button" id="fe_export_btn">Export FIT</button>
        </div>
        <div class="title" style="margin-top:6px;margin-bottom:0;">
          <h2 style="font-size:14px;">Quick Templates</h2>
          <span class="pill">One click</span>
        </div>
        <div class="actions">
          <button class="btn-ghost" type="button" id="fe_tpl_wu_cd_btn">Warm Up / Cool Down</button>
          <button class="btn-ghost" type="button" id="fe_tpl_work_btn">Work rep</button>
          <button class="btn-ghost" type="button" id="fe_tpl_recovery_btn">Recovery Jog</button>
          <button class="btn-ghost" type="button" id="fe_tpl_rest_btn">Rest 2 min</button>
        </div>
        <div class="metrics">
          <div class="metric"><b id="fe_metric_legs">0</b><span>legs</span></div>
          <div class="metric"><b id="fe_metric_time">0:00</b><span>duration</span></div>
          <div class="metric"><b id="fe_metric_reps">0</b><span>repeats</span></div>
        </div>
        <div id="fe_status" class="status">Load a FIT file or start with a blank workout.</div>
      </section>

      <section class="card editor">
        <div class="title">
          <h2>Leg Builder</h2>
          <span class="pill">Deterministic sequence</span>
        </div>
        <div id="fe_legs"></div>
      </section>

      <section class="card preview">
        <div class="title">
          <h2>Preview</h2>
          <span class="pill">Live</span>
        </div>
        <div class="chart-wrap">
          <canvas id="fe_chart" height="420"></canvas>
          <div id="fe_chart_tip" class="chart-tip hidden"></div>
          <div id="fe_chart_note">No workout loaded.</div>
        </div>
      </section>
      </div>

      <section class="card inspector">
        <div class="title">
          <h2>Editable JSON</h2>
          <span class="pill">Round-trip view</span>
        </div>
        <div id="fe_json"></div>
      </section>
    </main>
  </div>
  <script src="/static/fit_editor.js"></script>
</body>
</html>
"""


def _version_app_html(html_doc: str) -> HTMLResponse:
    """Give changed frontend code a new URL so phones cannot reuse stale JS."""
    static_dir = Path(__file__).resolve().parent / "static"
    asset_paths = [
        static_dir / "app.js",
        static_dir / "fit_editor.js",
        static_dir / "studio.js",
        static_dir / "studio.css",
    ]
    try:
        version = str(max(path.stat().st_mtime_ns for path in asset_paths))
    except OSError:
        version = str(int(time.time()))
    content = (
        html_doc
        .replace("__APP_JS_VERSION__", version)
        .replace("__ASSET_VERSION__", version)
    )
    return HTMLResponse(content, headers={"Cache-Control": "no-cache"})


@app.get("/", response_class=HTMLResponse)
def index():
    return _version_app_html(STUDIO_HTML)


@app.get("/plan", response_class=HTMLResponse)
def plan_builder():
    return _version_app_html(STUDIO_HTML)


@app.get("/fit-editor", response_class=HTMLResponse)
def fit_editor():
    return _version_app_html(STUDIO_HTML)


@app.get("/favicon.ico")
def favicon():
    return FileResponse(
        _PROJECT_ROOT / "PromptFitIOS" / "PromptFitIOS" / "Assets.xcassets"
        / "AppIcon.appiconset" / "PromptFitIcon-v2.png",
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/app-icon.png")
def app_icon():
    return FileResponse(
        _PROJECT_ROOT / "PromptFitIOS" / "PromptFitIOS" / "Assets.xcassets"
        / "AppIcon.appiconset" / "PromptFitIcon-v2.png",
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _export_fit_bytes(name: str, workout: Dict[str, Any], *, targets_enabled: bool,
                      target_mode: str, target_margin: int,
                      pace_min_per_mile: Optional[float],
                      race_distance: Optional[str] = None,
                      pace_profile: Optional[Dict[str, Any]] = None,
                      include_implicit_wu_cd: bool = False,
                      wu_cd_distance_miles: Optional[float] = None,
                      wu_cd_duration_min: Optional[float] = None) -> bytes:
    import importlib
    import hm_plan_to_garmin as hm
    import hm_plan_calendar as gen
    import final_spec_compliant_fix as spec

    importlib.reload(hm)
    importlib.reload(spec)

    # Configure targets and race pace
    hm.TARGETS_ENABLED = bool(targets_enabled)
    hm.TARGET_MODE = target_mode if target_mode in ("pace", "speed") else "pace"
    hm.TARGET_MARGIN_SEC = int(target_margin)
    hm.INCLUDE_IMPLICIT_WU_CD = bool(include_implicit_wu_cd)
    gen.implicit_wu_cd_distance_miles = wu_cd_distance_miles
    gen.implicit_wu_cd_duration_min = wu_cd_duration_min
    _apply_export_pace_context(
        pace_min_per_mile=pace_min_per_mile,
        race_distance=race_distance,
        pace_profile=pace_profile,
        hm_module=hm,
        gen_module=gen,
    )

    steps = hm.workout_to_garmin_steps(workout)
    if not steps:
        raise ValueError("No steps generated from workout")

    # Write to temp file then return bytes
    with tempfile.TemporaryDirectory() as td:
        outp = os.path.join(td, "w.fit")
        spec.export_spec_compliant_fit_workout(name, steps, outp, estimated_miles=0.0)
        with open(outp, 'rb') as f:
            return f.read()


def _safe_name(text: str, max_len: int = 80) -> str:
    import re
    s = (text or "").strip()
    s = re.sub(r"\s*\([^)]*\)", "", s)
    s = s.replace("~", "").replace(",", "")
    s = s.replace("/", "-").replace(" ", "_")
    s = re.sub(r"[^A-Za-z0-9._-]", "", s)
    s = re.sub(r"[_-]{2,}", lambda m: m.group(0)[0], s)
    s = s.strip("-_")
    return s[:max_len] or "Workout"


def _time_token(total_minutes: Optional[float]) -> str:
    try:
        m = int(round(float(total_minutes or 0)))
    except Exception:
        return ""
    if m <= 0:
        return ""
    h = m // 60
    mm = m % 60
    return f"{h}h{mm:02d}m" if h > 0 else f"{m}m"


def _build_prompt_style_fit_filename(name: str, miles: Optional[float], minutes: Optional[float], prefix: str = "") -> str:
    nm = (name or "Workout").strip() or "Workout"
    head = " ".join(p for p in [(prefix or "").strip(), nm] if p).strip()
    head_safe = _safe_name(head)
    miles_token = f"{float(miles):.1f} mi" if (miles is not None and float(miles) > 0) else ""
    time_token = _time_token(minutes)
    suffix_parts = [p for p in [miles_token, time_token] if p]
    if suffix_parts:
        return f"{head_safe}_{'_'.join(suffix_parts)}.fit"
    return f"{head_safe}.fit"


def _parse_pace_input(val: Optional[str]) -> Optional[float]:
    s = (val or "").strip()
    if not s:
        return None
    m = re.match(r"^(\d{1,2})\s*:\s*([0-5]?\d)$", s)
    if m:
        try:
            mins = int(m.group(1))
            secs = int(m.group(2))
            return mins + secs / 60.0
        except Exception:
            pass
    try:
        import hm_plan_calendar as gen
        p = gen.parse_pace_str(s)
        if p is not None:
            return float(p)
    except Exception:
        pass
    try:
        return float(s)
    except Exception:
        return None


def _apply_export_pace_context(*, pace_min_per_mile: Optional[float], race_distance: Optional[str],
                               pace_profile: Optional[Dict[str, Any]] = None,
                               hm_module: Any = None, gen_module: Any = None) -> None:
    """Keep race pace/race distance globals aligned across hm/gen modules."""
    if gen_module is None:
        import hm_plan_calendar as gen_module  # type: ignore

    norm_race = _normalize_race_distance(race_distance) or "half marathon"
    gen_module.race_distance = norm_race
    anchors = normalize_pace_profile(
        pace_profile,
        reference_pace=pace_min_per_mile,
        race_distance=norm_race,
        easy_pace=getattr(gen_module, "easy_pace_min_per_mile", None),
    )
    gen_module.pace_anchors_min_per_mile = dict(anchors)

    goal_key = normalize_pace_race_distance(norm_race) or "half_marathon"
    pace_val = estimate_anchor_pace(goal_key, anchors) if anchors else pace_min_per_mile
    if pace_val:
        pace_val = float(pace_val)
        gen_module.race_pace_min_per_mile = pace_val
        if hm_module is not None:
            hm_module.RACE_PACE_MIN_PER_MILE = pace_val
    elif hm_module is not None:
        try:
            gen_module.race_pace_min_per_mile = float(hm_module.RACE_PACE_MIN_PER_MILE)
        except Exception:
            pass


def _pace_profile_from_payload(payload: Dict[str, Any], *, reference_pace: Any = None,
                               race_distance: Any = None, easy_pace: Any = None) -> Dict[str, float]:
    raw = payload.get("paces")
    if raw is None:
        raw = payload.get("pace_profile")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    return normalize_pace_profile(
        raw,
        reference_pace=reference_pace,
        race_distance=race_distance,
        easy_pace=easy_pace,
    )


def _parse_bool(val: Optional[str]) -> bool:
    if val is None:
        return False
    if isinstance(val, bool):
        return bool(val)
    s = str(val).strip().lower()
    return s in ("1", "true", "yes", "on")


def _parse_date_input(s: str) -> date:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return date(dt.year, dt.month, dt.day)
        except ValueError:
            pass
    raise ValueError("Enter date as YYYY-MM-DD or MM/DD/YYYY")


SERVICE_NAME = "prompt-fit"


def _garmin_tokenstore_path() -> Path:
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "PromptFit" / "garmin"
    return Path.home() / ".promptfit" / "garmin"


def _clear_legacy_garmin_keychain() -> None:
    if not keyring:
        return
    for name in ("gc_username", "gc_password"):
        try:
            keyring.delete_password(SERVICE_NAME, name)
        except Exception:
            pass

def _is_local(request: Request) -> bool:
    host = request.client.host if request and request.client else ""
    return host in ("127.0.0.1", "::1", "localhost")


def _fit_summary_bytes(data: bytes) -> str:
    try:
        from fitparse import FitFile
    except Exception as e:
        return f"ERROR: fitparse not installed: {e}"
    try:
        ff = FitFile(io.BytesIO(data))
        def fields_dict(msg):
            return {f.name: f.value for f in msg.fields if getattr(f, 'value', None) is not None}
        lines = []
        for m in ff.get_messages('file_id'):
            d = fields_dict(m)
            lines.append(f"  file_id: type={d.get('type')}, manuf={d.get('manufacturer')}, garmin_product={d.get('garmin_product')}")
            break
        w_hdr = None
        for m in ff.get_messages('workout'):
            d = fields_dict(m)
            w_hdr = d
            break
        if w_hdr:
            name = w_hdr.get('wkt_name') or w_hdr.get('workout_name')
            lines.append(
                f"  workout: name={name}, sport={w_hdr.get('sport')}, sub_sport={w_hdr.get('sub_sport')}, steps={w_hdr.get('num_valid_steps')}"
            )
        steps = list(ff.get_messages('workout_step'))
        show_n = min(20, len(steps))
        for i, m in enumerate(steps[:show_n]):
            d = fields_dict(m)
            ordered = {}
            for k in (
                'message_index','wkt_step_name','intensity','duration_type','duration_time','duration_distance',
                'target_type','target_value','custom_target_value_low','custom_target_value_high',
                'custom_target_speed_low','custom_target_speed_high'):
                if k in d:
                    ordered[k] = d.pop(k)
            ordered.update(d)
            lines.append(f"    step[{i}]: {ordered}")
        if len(steps) > show_n:
            lines.append(f"    ... ({len(steps)-show_n} more steps)")
        return "\n".join(lines) if lines else "(no workout messages)"
    except Exception as e:
        return f"ERROR: {e}"


_PACE_RE = re.compile(r"(\d{1,2})\s*:\s*([0-5]\d)")
_DUR_HM_RE = re.compile(r"(\d+)\s*h(?:\s*(\d{1,2})\s*m)?", re.I)
_DUR_M_RE = re.compile(r"\b(\d{1,3})\s*m\b", re.I)
_DUR_COLON_RE = re.compile(r"\b(\d+):([0-5]\d)(?::([0-5]\d))?\b")


def _parse_pace_text(text: Optional[str]) -> Optional[float]:
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


def _parse_duration_text(text: Optional[str]) -> Optional[float]:
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


def _extract_workout_total_seconds(ff) -> Optional[float]:
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


def _enum_key(val: Any) -> str:
    if val is None:
        return ""
    try:
        if hasattr(val, "name"):
            val = val.name
    except Exception:
        pass
    return str(val).strip().lower()


def _duration_type_key(val: Any) -> str:
    if isinstance(val, (int, float)):
        try:
            if int(val) == 6:
                return "repeat_until_steps_cmplt"
        except Exception:
            pass
    return _enum_key(val)


def _intensity_key(val: Any) -> str:
    key = _enum_key(val)
    if "warmup" in key:
        return "warmup"
    if "cooldown" in key:
        return "cooldown"
    if "rest" in key or "recovery" in key or "walk" in key:
        return "rest"
    return key or "active"


def _pace_from_speed(mps: Optional[float]) -> Optional[float]:
    if not mps:
        return None
    try:
        mps_val = float(mps)
    except Exception:
        return None
    if mps_val <= 0:
        return None
    return (1609.34 / mps_val) / 60.0


def _speed_from_pace(pace_min_per_mi: Optional[float]) -> Optional[float]:
    if not pace_min_per_mi:
        return None
    try:
        pace_val = float(pace_min_per_mi)
    except Exception:
        return None
    if pace_val <= 0:
        return None
    return 1609.34 / (pace_val * 60.0)


def _pace_from_step(step: Dict[str, Any]) -> Optional[float]:
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


def _fit_graph_bytes(data: bytes) -> Dict[str, Any]:
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

        def default_pace(intensity: str) -> float:
            if intensity in ("rest", "recovery", "walk"):
                return base + 4.0
            if intensity in ("warmup", "cooldown"):
                return base + 2.0
            return base + 1.0

        expanded: List[Dict[str, Any]] = []
        for idx, st in enumerate(steps):
            dtype = _duration_type_key(st.get("duration_type"))
            is_repeat = dtype.startswith("repeat")
            if is_repeat:
                start_idx = int(st.get("duration_step") or 0)
                if start_idx >= idx and idx > 0:
                    start_idx = max(0, idx - 1)
                repeat_count = int(
                    st.get("repeat_steps")
                    or st.get("target_repeat_steps")
                    or st.get("target_value")
                    or 1
                )
                repeat_count = max(1, repeat_count)
                if repeat_count <= 1:
                    continue
                block: List[Dict[str, Any]] = []
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

        segments: List[Dict[str, Any]] = []
        total = 0.0
        unknown_idxs: List[int] = []
        rest_durations: List[float] = []
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


def _fit_editor_parse_bytes(data: bytes) -> Dict[str, Any]:
    try:
        from fitparse import FitFile
    except Exception as e:
        raise RuntimeError(f"fitparse not installed: {e}")

    def fields_dict(msg):
        return {f.name: f.value for f in msg.fields if getattr(f, "value", None) is not None}

    ff = FitFile(io.BytesIO(data))
    workout_name = "Workout"
    for m in ff.get_messages("workout"):
        d = fields_dict(m)
        nm = d.get("wkt_name") or d.get("workout_name")
        if nm:
            workout_name = str(nm).strip() or "Workout"
        break

    legs: List[Dict[str, Any]] = []
    for idx, m in enumerate(ff.get_messages("workout_step")):
        d = fields_dict(m)
        dtype = _duration_type_key(d.get("duration_type"))
        if dtype.startswith("repeat"):
            start_index = int(d.get("duration_step") or max(0, idx - 1))
            repeat_count = int(
                d.get("repeat_steps")
                or d.get("target_repeat_steps")
                or d.get("target_value")
                or 1
            )
            repeat_count = max(1, repeat_count)
            block_len = max(1, idx - start_index)
            legs.append({
                "kind": "repeat",
                "label": "Repeat block",
                "repeat_start_index": max(0, start_index),
                "repeat_count": repeat_count,
                "block_len": block_len,
                "skip_last_leg_on_final_repeat": False,
            })
            continue

        intensity = _intensity_key(d.get("intensity"))
        if intensity not in ("active", "rest", "warmup", "cooldown"):
            intensity = "active"

        duration_value: Optional[float] = None
        duration_unit = "open"
        if dtype == "time":
            duration_value = float(d.get("duration_time") or 0.0)
            duration_unit = "seconds"
        elif dtype == "distance":
            duration_value = float(d.get("duration_distance") or 0.0)
            duration_unit = "meters"

        speed_low = d.get("custom_target_speed_low")
        speed_high = d.get("custom_target_speed_high")
        try:
            speed_low_val = float(speed_low) if speed_low is not None else None
        except Exception:
            speed_low_val = None
        try:
            speed_high_val = float(speed_high) if speed_high is not None else None
        except Exception:
            speed_high_val = None

        target_key = _enum_key(d.get("target_type"))
        target_type = "speed" if (target_key == "speed" and speed_low_val and speed_high_val) else "open"
        pace_slow = _pace_from_speed(speed_low_val) if speed_low_val else None
        pace_fast = _pace_from_speed(speed_high_val) if speed_high_val else None

        legs.append({
            "kind": "step",
            "label": str(d.get("wkt_step_name") or d.get("notes") or intensity.title()),
            "intensity": intensity,
            "duration_type": dtype if dtype in ("time", "distance", "open") else "open",
            "duration_value": round(float(duration_value), 3) if duration_value is not None else None,
            "duration_unit": duration_unit,
            "target_type": target_type,
            "speed_low_mps": round(speed_low_val, 4) if speed_low_val else None,
            "speed_high_mps": round(speed_high_val, 4) if speed_high_val else None,
            "pace_slow_min_per_mi": round(pace_slow, 3) if pace_slow else None,
            "pace_fast_min_per_mi": round(pace_fast, 3) if pace_fast else None,
        })

    return {
        "workout_name": workout_name,
        "legs": legs,
        "graph": _fit_graph_bytes(data),
    }


def _fit_editor_steps_from_legs(legs: Any) -> List[Dict[str, Any]]:
    if not isinstance(legs, list):
        raise ValueError("legs must be a list")

    steps: List[Dict[str, Any]] = []
    for idx, leg in enumerate(legs):
        if not isinstance(leg, dict):
            continue
        kind = (leg.get("kind") or "step").strip().lower()
        if kind == "repeat":
            try:
                start_index = int(leg.get("repeat_start_index"))
            except Exception:
                start_index = max(0, len(steps) - 1)
            if start_index < 0:
                start_index = 0
            if start_index >= len(steps):
                start_index = max(0, len(steps) - 1)
            block_len = max(1, len(steps) - start_index)
            try:
                repeat_count = int(leg.get("repeat_count") or 1)
            except Exception:
                repeat_count = 1
            repeat_count = max(1, repeat_count)
            skip_last = bool(leg.get("skip_last_leg_on_final_repeat"))

            if skip_last and block_len >= 1:
                block = steps[start_index:start_index + block_len]
                if not block:
                    continue
                truncated_tail = [dict(x) for x in block[:-1]]
                if repeat_count <= 1:
                    # Keep only the first (block_len-1) steps from the initial block.
                    del steps[start_index + block_len - 1]
                    continue

                # Build (repeat_count-1) full loops + one final truncated loop.
                full_loop_total = max(1, repeat_count - 1)
                if full_loop_total > 1:
                    steps.append({
                        "type": "repeat",
                        "repeat": {
                            "start_index": start_index,
                            "block_len": block_len,
                            "count": full_loop_total,
                        },
                    })
                steps.extend(truncated_tail)
                continue

            steps.append({
                "type": "repeat",
                "repeat": {
                    "start_index": start_index,
                    "block_len": block_len,
                    "count": repeat_count,
                },
            })
            continue

        intensity = (leg.get("intensity") or "active").strip().lower()
        if intensity not in ("active", "rest", "warmup", "cooldown"):
            intensity = "active"

        step_type = "run"
        if intensity == "warmup":
            step_type = "warmup"
        elif intensity == "cooldown":
            step_type = "cooldown"
        elif intensity == "rest":
            step_type = "recovery"

        label = (leg.get("label") or intensity.title() or "Step").strip()
        duration_type = (leg.get("duration_type") or "open").strip().lower()
        if duration_type not in ("time", "distance", "open"):
            duration_type = "open"

        st: Dict[str, Any] = {
            "type": step_type,
            "description": label,
            "endCondition": "OPEN",
        }
        if duration_type == "time":
            try:
                sec = float(leg.get("duration_value") or 0.0)
            except Exception:
                sec = 0.0
            if sec <= 0:
                raise ValueError(f"Leg {idx + 1}: time duration must be > 0")
            st["endCondition"] = "TIME"
            st["endConditionValue"] = sec
        elif duration_type == "distance":
            try:
                meters = float(leg.get("duration_value") or 0.0)
            except Exception:
                meters = 0.0
            if meters <= 0:
                raise ValueError(f"Leg {idx + 1}: distance must be > 0")
            st["endCondition"] = "DISTANCE"
            st["endConditionValue"] = meters

        target_type = (leg.get("target_type") or "open").strip().lower()
        speed_low = leg.get("speed_low_mps")
        speed_high = leg.get("speed_high_mps")
        if target_type == "speed":
            low_mps: Optional[float] = None
            high_mps: Optional[float] = None
            try:
                if speed_low not in (None, ""):
                    low_mps = float(speed_low)
            except Exception:
                low_mps = None
            try:
                if speed_high not in (None, ""):
                    high_mps = float(speed_high)
            except Exception:
                high_mps = None
            if (not low_mps or not high_mps) and (leg.get("pace_slow_min_per_mi") or leg.get("pace_fast_min_per_mi")):
                low_mps = _speed_from_pace(leg.get("pace_slow_min_per_mi"))
                high_mps = _speed_from_pace(leg.get("pace_fast_min_per_mi"))
            if low_mps and high_mps and low_mps > 0 and high_mps > 0:
                lo = min(low_mps, high_mps)
                hi = max(low_mps, high_mps)
                st["targetType"] = "SPEED"
                st["targetValueLow"] = lo
                st["targetValueHigh"] = hi
            else:
                st["targetType"] = "NO_TARGET"
        else:
            st["targetType"] = "NO_TARGET"

        steps.append(st)

    if not steps:
        raise ValueError("No legs to export")
    return steps


def _fit_editor_estimate_miles_minutes(legs: Any) -> tuple[float, float]:
    if not isinstance(legs, list):
        return (0.0, 0.0)

    def _leg_pace_min_per_mi(leg: Dict[str, Any]) -> Optional[float]:
        slow = leg.get("pace_slow_min_per_mi")
        fast = leg.get("pace_fast_min_per_mi")
        try:
            slow_v = float(slow) if slow is not None else None
        except Exception:
            slow_v = None
        try:
            fast_v = float(fast) if fast is not None else None
        except Exception:
            fast_v = None
        if slow_v and fast_v and slow_v > 0 and fast_v > 0:
            return (slow_v + fast_v) / 2.0
        if slow_v and slow_v > 0:
            return slow_v
        if fast_v and fast_v > 0:
            return fast_v

        sl = leg.get("speed_low_mps")
        sh = leg.get("speed_high_mps")
        try:
            sl_v = float(sl) if sl is not None else None
        except Exception:
            sl_v = None
        try:
            sh_v = float(sh) if sh is not None else None
        except Exception:
            sh_v = None
        if sl_v and sh_v and sl_v > 0 and sh_v > 0:
            mps = (sl_v + sh_v) / 2.0
            return (1609.34 / mps) / 60.0
        if sl_v and sl_v > 0:
            return (1609.34 / sl_v) / 60.0
        if sh_v and sh_v > 0:
            return (1609.34 / sh_v) / 60.0

        intensity = str(leg.get("intensity") or "active").lower()
        if intensity == "rest":
            return 13.0
        if intensity in ("warmup", "cooldown"):
            return 10.0
        return 8.0

    row_segments: List[List[Dict[str, float]]] = []
    expanded: List[Dict[str, float]] = []
    for idx, leg in enumerate(legs):
        if not isinstance(leg, dict):
            row_segments.append([])
            continue
        kind = str(leg.get("kind") or "step").lower()
        if kind == "repeat":
            if idx == 0:
                row_segments.append([])
                continue
            try:
                start = int(leg.get("repeat_start_index") or 0)
            except Exception:
                start = 0
            start = max(0, min(start, idx - 1))
            try:
                repeats = int(leg.get("repeat_count") or 1)
            except Exception:
                repeats = 1
            repeats = max(1, repeats)
            skip_last = bool(leg.get("skip_last_leg_on_final_repeat"))
            block: List[Dict[str, float]] = []
            for j in range(start, idx):
                for s in (row_segments[j] if j < len(row_segments) else []):
                    block.append({"duration_s": float(s.get("duration_s") or 0.0), "distance_m": float(s.get("distance_m") or 0.0)})
            produced: List[Dict[str, float]] = []
            full_loop_adds = max(0, repeats - 1)
            if skip_last:
                full_loop_adds = max(0, repeats - 2)
            for _ in range(full_loop_adds):
                for s in block:
                    produced.append({"duration_s": s["duration_s"], "distance_m": s["distance_m"]})
            if skip_last and repeats >= 2 and block:
                for s in block[:-1]:
                    produced.append({"duration_s": s["duration_s"], "distance_m": s["distance_m"]})
            row_segments.append(produced)
            expanded.extend(produced)
            continue

        pace = _leg_pace_min_per_mi(leg)
        mps = 1609.34 / (pace * 60.0) if (pace and pace > 0) else 0.0
        duration_type = str(leg.get("duration_type") or "time").lower()
        intensity = str(leg.get("intensity") or "active").lower()
        duration_s = 0.0
        distance_m = 0.0
        try:
            dv = float(leg.get("duration_value") or 0.0)
        except Exception:
            dv = 0.0
        if duration_type == "time":
            duration_s = max(0.0, dv)
            distance_m = duration_s * mps if mps > 0 else 0.0
        elif duration_type == "distance":
            distance_m = max(0.0, dv)
            duration_s = (distance_m / mps) if mps > 0 else 0.0
        else:
            duration_s = 90.0 if intensity == "rest" else 60.0
            distance_m = duration_s * mps if mps > 0 else 0.0
        if duration_s <= 0:
            row_segments.append([])
            continue
        seg = {"duration_s": duration_s, "distance_m": distance_m}
        row_segments.append([seg])
        expanded.append(seg)

    total_seconds = sum(float(s.get("duration_s") or 0.0) for s in expanded)
    total_meters = sum(float(s.get("distance_m") or 0.0) for s in expanded)
    return (total_meters / 1609.34, total_seconds / 60.0)


def _find_garmin_dest_dir() -> Optional[str]:
    """Return a GARMIN workouts/newfiles directory if a watch is mounted on macOS."""
    if platform.system() != "Darwin":
        return None
    home = os.path.expanduser("~")
    candidates = [
        "/Volumes/*/GARMIN/Workouts",
        "/Volumes/*/GARMIN/NEWFILES",
        "/Volumes/*/Garmin/Workouts",
        "/Volumes/*/Garmin/NewFiles",
        # Also support simple-mtpfs-style mounts under the user home
        f"{home}/mnt/*/GARMIN/Workouts",
        f"{home}/mnt/*/GARMIN/NEWFILES",
        f"{home}/mnt/*/Garmin/Workouts",
        f"{home}/mnt/*/Garmin/NewFiles",
    ]
    for pat in candidates:
        for d in glob.glob(pat):
            if os.path.isdir(d):
                return d
    return None


def _unique_path(dest_dir: str, filename: str) -> str:
    base = os.path.splitext(filename)[0]
    ext = os.path.splitext(filename)[1]
    cand = os.path.join(dest_dir, filename)
    i = 1
    while os.path.exists(cand):
        cand = os.path.join(dest_dir, f"{base}_{i}{ext}")
        i += 1
    return cand


@app.get("/api/secrets", response_class=JSONResponse)
def get_secrets(request: Request):
    if not _is_local(request):
        raise HTTPException(403, "Forbidden")
    if not keyring:
        return JSONResponse({})
    def g(name: str) -> str:
        try:
            v = keyring.get_password(SERVICE_NAME, name)
            return v or ""
        except Exception:
            return ""
    return JSONResponse({
        "openai_api_key": g("openai_api_key"),
        "openai_model": g("openai_model"),
        "openrouter_api_key": g("openrouter_api_key"),
        "openrouter_model": g("openrouter_model"),
    })


@app.post("/api/secrets", response_class=JSONResponse)
def save_secrets(payload: Dict[str, Any], request: Request):
    if not _is_local(request):
        raise HTTPException(403, "Forbidden")
    if not keyring:
        raise HTTPException(400, "keyring not installed")
    def s(name: str, val: Optional[str]):
        if val is None:
            return
        try:
            keyring.set_password(SERVICE_NAME, name, val)
        except Exception as e:
            raise HTTPException(400, f"failed saving {name}: {e}")
    s("openai_api_key", (payload.get("openai_api_key") or "").strip())
    s("openai_model", (payload.get("openai_model") or "").strip())
    s("openrouter_api_key", (payload.get("openrouter_api_key") or "").strip())
    s("openrouter_model", (payload.get("openrouter_model") or "").strip())
    return JSONResponse({"ok": True})


@app.post("/api/generate-plan")
async def generate_plan(
    request: Request,
    plan_file: UploadFile = File(None),
    plan_preset: str = Form(""),
    race_date: str = Form(...),
    race_distance: str = Form("half marathon"),
    race_pace: str = Form(""),
    easy_pace: str = Form(""),
    pace_profile: str = Form(""),
    peak_mileage: str = Form(""),
    base_name: str = Form("training_plan"),
    include_wu_cd: str = Form(""),
    scale_wu_cd: str = Form(""),
    collapse_doubles: str = Form(""),
    consolidate_workouts: str = Form(""),
    rest_days: str = Form("0"),
    redistribute: str = Form(""),
    normalize: str = Form(""),
    norm_reduce: str = Form(""),
    wf_mode: str = Form("same"),
    wf_value: str = Form(""),
    generate_fits: str = Form("true"),
    include_easy_fits: str = Form(""),
    package_mode: str = Form("full"),
    wu_cd_distance: str = Form(""),
    wu_cd_duration: str = Form(""),
):
    plan_bytes = None
    plan_preset = (plan_preset or "").strip()
    if plan_file is not None and getattr(plan_file, "filename", ""):
        plan_bytes = await plan_file.read()
    elif plan_preset:
        preset_path = _resolve_plan_preset_path(plan_preset)
        if not preset_path:
            raise HTTPException(400, "Invalid plan preset")
        plan_bytes = preset_path.read_bytes()
    if not plan_bytes:
        raise HTTPException(400, "Provide plan_file or select a preset plan")
    plan_meta_norm = None
    try:
        plan_data = json.loads(plan_bytes)
        if isinstance(plan_data, dict):
            plan_meta_norm = plan_data.get("plan_meta", {}).get("normalize_weekly_to_reference")
    except Exception:
        plan_meta_norm = None

    try:
        dt = _parse_date_input(race_date)
    except Exception as e:
        raise HTTPException(400, f"Invalid race_date: {e}")

    try:
        peak_val = float(peak_mileage) if str(peak_mileage).strip() else 0.0
        if peak_val <= 0:
            raise ValueError("Peak mileage must be > 0")
    except Exception as e:
        raise HTTPException(400, f"Invalid peak_mileage: {e}")

    pace_val = _parse_pace_input(race_pace)
    if pace_val is None:
        raise HTTPException(400, "Provide race_pace (e.g., 6:30 or 6.50)")
    easy_val = _parse_pace_input(easy_pace)
    try:
        raw_plan_paces = json.loads(pace_profile) if str(pace_profile).strip() else {}
    except Exception:
        raw_plan_paces = {}
    plan_paces = normalize_pace_profile(raw_plan_paces, race_distance=race_distance)
    plan_goal_key = normalize_pace_race_distance(race_distance) or "half_marathon"
    plan_paces[plan_goal_key] = float(pace_val)
    if easy_val:
        plan_paces["easy"] = float(easy_val)

    try:
        rest_days_val = int(rest_days or 0)
        if rest_days_val < 0:
            raise ValueError("rest_days must be >= 0")
    except Exception as e:
        raise HTTPException(400, f"Invalid rest_days: {e}")

    safe_base = _safe_name(base_name or "training_plan")

    import importlib
    import hm_plan_calendar as gen
    gen = importlib.reload(gen)

    generate_fits_val = _parse_bool(generate_fits)
    include_easy_fits_val = _parse_bool(include_easy_fits)
    package_mode_val = str(package_mode or "full").strip().lower()
    if package_mode_val not in {"full", "fits", "calendar"}:
        raise HTTPException(400, "Invalid download contents")
    if package_mode_val == "fits" and not generate_fits_val:
        raise HTTPException(400, "Choose a FIT scope before requesting a FIT-only download")

    # Save plan JSON to a temp file
    with tempfile.TemporaryDirectory() as td:
        plan_path = os.path.join(td, "plan.json")
        with open(plan_path, "wb") as f:
            f.write(plan_bytes)

        # Apply settings
        gen.input_json_file = plan_path
        gen.output_ics_file = safe_base + ".ics"
        gen.race_date = dt
        gen.race_distance = gen.normalize_race_distance(race_distance) or race_distance
        gen.race_pace_min_per_mile = float(pace_val)
        gen.easy_pace_min_per_mile = float(easy_val) if easy_val else None
        gen.pace_anchors_min_per_mile = dict(plan_paces)
        gen.peak_mileage = peak_val
        gen.include_implicit_wu_cd = _parse_bool(include_wu_cd)
        gen.scale_wu_cd_segments = _parse_bool(scale_wu_cd)
        try:
            gen.implicit_wu_cd_distance_miles = float(wu_cd_distance) if str(wu_cd_distance).strip() else None
        except Exception:
            gen.implicit_wu_cd_distance_miles = None
        try:
            gen.implicit_wu_cd_duration_min = float(wu_cd_duration) if str(wu_cd_duration).strip() else None
        except Exception:
            gen.implicit_wu_cd_duration_min = None
        gen.collapse_doubles = _parse_bool(collapse_doubles)
        gen.consolidate_two_workout_doubles = _parse_bool(consolidate_workouts)
        gen.rest_days_per_week_target = rest_days_val
        gen.redistribute_removed_load = _parse_bool(redistribute) if redistribute != "" else True
        if normalize == "" and plan_meta_norm is not None:
            gen.normalize_weekly_to_reference = bool(plan_meta_norm)
        else:
            gen.normalize_weekly_to_reference = _parse_bool(normalize) if normalize != "" else True
        gen.normalize_reduce_for_fast = _parse_bool(norm_reduce)

        mode = (wf_mode or "same").strip().lower()
        if mode == "custom":
            try:
                gen.workout_factor_override = float(wf_value)
            except Exception:
                raise HTTPException(400, "Invalid workout factor multiplier")
            gen.workout_factor_multiplier = None
            gen.workout_factor_mode = "custom"
        elif mode == "normalize":
            gen.workout_factor_override = None
            gen.workout_factor_multiplier = None
            gen.workout_factor_mode = "normalize"
        elif mode == "same":
            gen.workout_factor_override = None
            gen.workout_factor_multiplier = None
            gen.workout_factor_mode = "same"
        elif mode == "override":
            # legacy: explicit override
            try:
                gen.workout_factor_override = float(wf_value)
            except Exception:
                raise HTTPException(400, "Invalid workout factor override")
            gen.workout_factor_multiplier = None
            gen.workout_factor_mode = "custom"
        elif mode == "mult":
            # legacy: multiplier on base
            try:
                gen.workout_factor_multiplier = float(wf_value)
            except Exception:
                raise HTTPException(400, "Invalid workout factor multiplier")
            gen.workout_factor_override = None
            gen.workout_factor_mode = "mult"
        elif mode == "original":
            gen.workout_factor_override = None
            gen.workout_factor_multiplier = None
            gen.workout_factor_mode = "original"
        else:
            # plan default (legacy)
            gen.workout_factor_override = None
            gen.workout_factor_multiplier = None
            gen.workout_factor_mode = "plan"

        preview_token = uuid.uuid4().hex
        try:
            home_url = str(request.base_url).rstrip("/")
            ics_path, html_path, fit_files, fit_schedule = gen.main(
                return_paths=True,
                open_browser=False,
                home_url=home_url,
                preview_token=preview_token,
                generate_fits=generate_fits_val,
                include_easy_fits=include_easy_fits_val,
                fit_targets_enabled=True,
                fit_target_mode="pace",
                fit_target_margin=30,
                return_fit_files=True,
                return_fit_schedule=True,
            )
        except Exception as e:
            raise HTTPException(400, f"Plan generation failed: {e}")

        try:
            with open(html_path, "r", encoding="utf-8") as f:
                _PLAN_PREVIEW_STORE.clear()
                _PLAN_PREVIEW_STORE[preview_token] = {
                    "html": f.read(),
                    "fits": {name: data for (name, data) in (fit_files or [])},
                    "fit_schedule": {
                        name: scheduled_date.isoformat()
                        for name, scheduled_date in (fit_schedule or {}).items()
                    },
                }
        except Exception:
            preview_token = None

        # Bundle into a zip for download
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            try:
                if package_mode_val in {"full", "calendar"}:
                    zf.write(ics_path, arcname=os.path.basename(ics_path))
                    zf.write(html_path, arcname=os.path.basename(html_path))
                if package_mode_val in {"full", "fits"} and fit_files:
                    for (fname, fbytes) in fit_files:
                        if not fname:
                            continue
                        arc = os.path.join("fits", fname)
                        zf.writestr(arc, fbytes)
            except Exception as e:
                raise HTTPException(400, f"Failed packaging outputs: {e}")
        buf.seek(0)
        resp = StreamingResponse(buf, media_type="application/zip")
        resp.headers["Content-Disposition"] = f"attachment; filename={safe_base}.zip"
        if preview_token:
            resp.headers["X-Plan-Preview"] = f"/api/plan-preview/{preview_token}"
            resp.headers["X-Plan-Token"] = preview_token
        return resp


@app.get("/api/plan-preview/{token}", response_class=HTMLResponse)
def plan_preview(token: str, request: Request):
    entry = _PLAN_PREVIEW_STORE.get(token)
    if not entry:
        raise HTTPException(404, "Preview not available")
    html_doc = entry.get("html") if isinstance(entry, dict) else entry
    if not html_doc:
        raise HTTPException(404, "Preview not available")
    if request.query_params.get("embed") == "1":
        html_doc = html_doc.replace("<body>", "<body class='embedded'>", 1)
    return HTMLResponse(html_doc)


@app.get("/api/plan-preview/{token}/download")
def download_plan_preview(token: str):
    entry = _PLAN_PREVIEW_STORE.get(token)
    if not entry:
        raise HTTPException(404, "Preview not available")
    html_doc = entry.get("html") if isinstance(entry, dict) else entry
    if not html_doc:
        raise HTTPException(404, "Preview not available")
    return HTMLResponse(
        html_doc,
        headers={
            "Content-Disposition": "attachment; filename=promptfit-training-plan.html",
            "Cache-Control": "no-cache",
        },
    )

@app.get("/api/plan-presets", response_class=JSONResponse)
def plan_presets():
    return JSONResponse(_list_plan_presets())


@app.get("/api/plan-fit/{token}/{fit_name}")
def plan_fit(token: str, fit_name: str):
    if "/" in fit_name or "\\" in fit_name:
        raise HTTPException(400, "Invalid filename")
    entry = _PLAN_PREVIEW_STORE.get(token)
    if not entry or not isinstance(entry, dict):
        raise HTTPException(404, "FIT not available")
    fits = entry.get("fits") or {}
    data = fits.get(fit_name)
    if not data:
        raise HTTPException(404, "FIT not available")
    resp = StreamingResponse(io.BytesIO(data), media_type="application/octet-stream")
    resp.headers["Content-Disposition"] = f"attachment; filename={fit_name}"
    return resp


def _garmin_checked_at() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _garmin_account_name(client: Any) -> str:
    """Return a short, user-recognizable label from the profile loaded at login."""
    for attr in ("full_name", "display_name"):
        value = getattr(client, attr, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                value = None
        label = str(value or "").strip()
        if label:
            return label[:120]
    return ""


def _garmin_connection_status(
    request: Optional[Request] = None,
    *,
    verify: bool = False,
    client: Any = None,
) -> Dict[str, Any]:
    tokenstore = str(_garmin_tokenstore_path())
    saved_session = gc_opt.has_saved_login(tokenstore)
    can_manage = bool(request and _is_local(request))

    if not saved_session:
        return {
            "status": "not_connected",
            "connected": False,
            "verified": False,
            "saved_session": False,
            "can_manage": can_manage,
            "checked_at": _garmin_checked_at(),
            "message": (
                "Garmin is not connected yet. Complete the one-time setup on this Mac."
                if can_manage
                else "Garmin is not connected yet. Connect once from localhost on the Mac."
            ),
        }

    verified_client = client
    if verify and verified_client is None:
        try:
            verified_client = gc_opt.login_saved(tokenstore)
        except Exception as exc:
            return {
                "status": "verification_failed",
                "connected": False,
                "verified": False,
                "saved_session": True,
                "can_manage": can_manage,
                "checked_at": _garmin_checked_at(),
                "message": (
                    "A saved Garmin session was found, but the live Garmin check failed. "
                    + (
                        "Try checking again; reconnect below if it keeps failing."
                        if can_manage
                        else "Try again; reconnect from localhost on the Mac if it keeps failing."
                    )
                ),
                "verification_error": str(exc),
            }

    verified = verified_client is not None
    account_name = _garmin_account_name(verified_client) if verified else ""
    return {
        "status": "verified" if verified else "saved",
        "connected": True,
        "verified": verified,
        "saved_session": True,
        "can_manage": can_manage,
        "checked_at": _garmin_checked_at(),
        "account_name": account_name,
        "message": (
            "Garmin accepted the saved connection. Workouts are ready to upload."
            if verified
            else "A saved Garmin session is available on this Mac."
        ),
    }


@app.get("/api/garmin/status", response_class=JSONResponse)
def garmin_status(request: Request):
    # Loading the saved client also fetches the Garmin profile/settings, so this
    # is a live check rather than a filesystem-only token check.
    return JSONResponse(_garmin_connection_status(request, verify=True))


@app.post("/api/garmin/connect", response_class=JSONResponse)
def garmin_connect(payload: Dict[str, Any], request: Request):
    if not _is_local(request):
        raise HTTPException(403, "Connect Garmin from http://localhost:8000 on this Mac")
    username = str(payload.get("gc_username") or "").strip()
    password = str(payload.get("gc_password") or "")
    if not username or not password:
        raise HTTPException(400, "Enter your Garmin username and password")

    tokenstore = str(_garmin_tokenstore_path())
    gc_opt.disconnect_saved(tokenstore)
    _cleanup_garmin_pending()
    try:
        client = gc_opt.login(username, password, tokenstore=tokenstore)
    except gc_opt.GarminMFARequired as mfa:
        token = uuid.uuid4().hex
        _GARMIN_PENDING_UPLOADS[token] = {
            "operation": "connect",
            "created_at": time.monotonic(),
            "client": mfa.client,
            "client_state": mfa.client_state,
            "tokenstore": mfa.tokenstore or tokenstore,
            "attempts": 0,
        }
        return JSONResponse({
            "status": "mfa_required",
            "mfa_token": token,
            "message": "Enter the verification code Garmin sent you.",
        }, status_code=202)
    except Exception as exc:
        raise HTTPException(400, f"Garmin connection failed: {exc}")

    _clear_legacy_garmin_keychain()
    return JSONResponse({
        **_garmin_connection_status(request, client=client),
        "message": "Garmin connected. Your password was discarded; only session tokens were saved.",
    })


@app.delete("/api/garmin/connection", response_class=JSONResponse)
def garmin_disconnect(request: Request):
    if not _is_local(request):
        raise HTTPException(403, "Disconnect Garmin from http://localhost:8000 on this Mac")
    gc_opt.disconnect_saved(str(_garmin_tokenstore_path()))
    _GARMIN_PENDING_UPLOADS.clear()
    return JSONResponse({
        "status": "not_connected",
        "connected": False,
        "verified": False,
        "saved_session": False,
        "can_manage": True,
        "checked_at": _garmin_checked_at(),
        "message": "The saved Garmin connection was removed from this Mac.",
    })


def _cleanup_garmin_pending() -> None:
    cutoff = time.monotonic() - _GARMIN_PENDING_TTL_SECONDS
    expired = [
        token for token, entry in _GARMIN_PENDING_UPLOADS.items()
        if float(entry.get("created_at") or 0) < cutoff
    ]
    for token in expired:
        _GARMIN_PENDING_UPLOADS.pop(token, None)


def _upload_garmin_items(
    client: Any,
    items: List[Dict[str, Any]],
    initial_results: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = list(initial_results or [])
    for item in items:
        source_name = item.get("source_name") or item.get("workout_name") or "Workout"
        result_source = {"source": source_name}
        if item.get("source_id"):
            result_source["sourceId"] = item["source_id"]
        if item.get("schedule_date"):
            result_source["requestedScheduleDate"] = item["schedule_date"].isoformat()
        try:
            uploaded = gc_opt.upload_and_optionally_schedule(
                client,
                item["workout_json"],
                schedule_date=item.get("schedule_date"),
            )
            results.append({**result_source, "ok": True, **uploaded})
        except Exception as exc:
            results.append({**result_source, "ok": False, "error": str(exc)})

    successful = sum(1 for result in results if result.get("ok"))
    failed = len(results) - successful
    attempted = len(results)
    workout_label = "workout" if attempted == 1 else "workouts"
    if failed == 0:
        status = "success"
        message = f"Confirmed: {successful} of {attempted} {workout_label} uploaded to Garmin Connect."
    elif successful:
        status = "partial"
        message = f"{successful} of {attempted} {workout_label} uploaded; {failed} failed."
    else:
        status = "failed"
        message = f"No workouts uploaded; {failed} failed."
    return {
        "status": status,
        "attempted": attempted,
        "successful": successful,
        "failed": failed,
        "completed_at": _garmin_checked_at(),
        "results": results,
        "message": message,
    }


def _begin_garmin_upload(
    items: List[Dict[str, Any]],
    initial_results: Optional[List[Dict[str, Any]]] = None,
):
    try:
        client = gc_opt.login_saved(str(_garmin_tokenstore_path()))
    except Exception as exc:
        raise HTTPException(400, str(exc))
    return JSONResponse(_upload_garmin_items(client, items, initial_results))


def _garmin_item_from_fit_bytes(
    data: bytes,
    filename: str,
    scheduled_for: Optional[date],
) -> Dict[str, Any]:
    parsed = _fit_editor_parse_bytes(data)
    legs = parsed.get("legs") or []
    steps = _fit_editor_steps_from_legs(legs)
    workout_name = parsed.get("workout_name") or Path(filename).stem or "Workout"
    return {
        "source_name": filename,
        "workout_name": workout_name,
        "workout_json": gc_opt.create_workout_json(workout_name, steps),
        "schedule_date": scheduled_for,
    }


def _local_fit_root() -> Path:
    """The intentionally narrow folder exposed to phones on the local network."""
    return (_PROJECT_ROOT / "fit_out_gui").resolve()


def _local_fit_catalog() -> List[Dict[str, Any]]:
    root = _local_fit_root()
    if not root.is_dir():
        return []

    files: List[Dict[str, Any]] = []
    for candidate in root.rglob("*"):
        try:
            resolved = candidate.resolve()
            if not resolved.is_file() or resolved.suffix.lower() != ".fit":
                continue
            if root not in resolved.parents:
                continue
            stat = resolved.stat()
            relative = resolved.relative_to(_PROJECT_ROOT.resolve()).as_posix()
        except (OSError, ValueError):
            continue
        files.append({
            "id": relative,
            "name": resolved.name,
            "folder": resolved.parent.relative_to(_PROJECT_ROOT.resolve()).as_posix(),
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "modified_epoch": stat.st_mtime,
        })

    files.sort(key=lambda item: (-float(item["modified_epoch"]), str(item["name"]).lower()))
    for item in files:
        item.pop("modified_epoch", None)
    return files[:500]


def _resolve_local_fit(file_id: str) -> Path:
    root = _local_fit_root()
    if not root.is_dir():
        raise ValueError("The fit_out_gui folder does not exist yet")
    if not file_id or "\x00" in file_id:
        raise ValueError("Invalid workout selection")
    try:
        candidate = (_PROJECT_ROOT / file_id).resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError("Invalid workout selection") from exc
    if root not in candidate.parents or candidate.suffix.lower() != ".fit":
        raise ValueError("That workout is outside the selectable FIT folder")
    if not candidate.is_file():
        raise ValueError("That workout file is no longer available")
    if candidate.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("FIT file is larger than 5 MB")
    return candidate


@app.get("/api/garmin/local-fits", response_class=JSONResponse)
def garmin_local_fits():
    """List FIT workouts available for explicit selection from a phone or Mac."""
    files = _local_fit_catalog()
    return JSONResponse({
        "files": files,
        "count": len(files),
        "folder": "fit_out_gui",
    })


@app.post("/api/fit-review/approve", response_class=JSONResponse)
async def approve_fit_review(file: UploadFile = File(...)):
    """Validate and add one explicitly approved FIT to the local delivery queue."""
    filename = Path(file.filename or "workout.fit").name
    if Path(filename).suffix.lower() != ".fit":
        raise HTTPException(400, "Approve a .fit workout file")
    data = await file.read()
    if not data:
        raise HTTPException(400, "The FIT file is empty")
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, "FIT file is larger than 5 MB")
    try:
        parsed = _fit_editor_parse_bytes(data)
        if not (parsed.get("legs") or []):
            raise ValueError("No workout steps were found")
    except Exception as exc:
        raise HTTPException(400, f"FIT validation failed: {exc}") from exc

    root = _local_fit_root()
    root.mkdir(parents=True, exist_ok=True)
    safe_filename = f"{_safe_name(Path(filename).stem)}.fit"
    destination = Path(_unique_path(str(root), safe_filename))
    try:
        destination.write_bytes(data)
    except OSError as exc:
        raise HTTPException(500, f"Could not add the FIT to the delivery queue: {exc}") from exc

    relative = destination.resolve().relative_to(_PROJECT_ROOT.resolve()).as_posix()
    approved = next((item for item in _local_fit_catalog() if item.get("id") == relative), None)
    if not approved:
        raise HTTPException(500, "The approved FIT was saved but could not be added to the queue")
    return JSONResponse({"ok": True, "file": approved})


@app.get("/api/garmin/local-fit-download")
def garmin_local_fit_download(file: str):
    """Download one explicitly selected FIT from the narrow local queue."""
    try:
        fit_path = _resolve_local_fit(file)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    resp = StreamingResponse(io.BytesIO(fit_path.read_bytes()), media_type="application/octet-stream")
    resp.headers["Content-Disposition"] = f"attachment; filename={_safe_name(fit_path.stem)}.fit"
    return resp


@app.post("/api/garmin/local-fit-upload", response_class=JSONResponse)
def garmin_local_fit_upload(payload: Dict[str, Any]):
    """Upload only the project FIT files explicitly selected in the request."""
    selected = payload.get("files")
    if not isinstance(selected, list) or not selected:
        raise HTTPException(400, "Check at least one workout first")
    if len(selected) > 50:
        raise HTTPException(400, "Upload at most 50 FIT files at a time")

    scheduled_for: Optional[date] = None
    schedule_date = str(payload.get("schedule_date") or "").strip()
    if schedule_date:
        try:
            scheduled_for = _parse_date_input(schedule_date)
        except Exception as exc:
            raise HTTPException(400, f"Invalid schedule date: {exc}")

    items: List[Dict[str, Any]] = []
    parse_results: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw_file_id in selected:
        file_id = str(raw_file_id or "").strip()
        if file_id in seen:
            continue
        seen.add(file_id)
        source_name = Path(file_id).name or "Workout"
        try:
            fit_path = _resolve_local_fit(file_id)
            data = fit_path.read_bytes()
            item = _garmin_item_from_fit_bytes(data, fit_path.name, scheduled_for)
            item["source_id"] = file_id
            items.append(item)
        except Exception as exc:
            parse_results.append({
                "source": source_name,
                "sourceId": file_id,
                "ok": False,
                "error": str(exc),
            })

    if not items:
        detail = "; ".join(
            f"{result.get('source')}: {result.get('error')}" for result in parse_results
        ) or "No valid workout FIT files found"
        raise HTTPException(400, detail)
    return _begin_garmin_upload(items, parse_results)


def _plan_garmin_items(
    entry: Dict[str, Any],
    *,
    fit_names: Optional[List[str]] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build Garmin upload items with the date calculated for each plan FIT."""
    fits = entry.get("fits") or {}
    fit_schedule = entry.get("fit_schedule") or {}
    if not isinstance(fits, dict) or not fits:
        raise ValueError("This plan does not contain generated FIT workouts")
    if not isinstance(fit_schedule, dict):
        fit_schedule = {}

    selected_names = {Path(str(name)).name for name in (fit_names or []) if str(name).strip()}
    items: List[Dict[str, Any]] = []
    parse_results: List[Dict[str, Any]] = []
    for filename, data in fits.items():
        source_name = Path(str(filename or "Workout.fit")).name
        if selected_names and source_name not in selected_names:
            continue
        schedule_value = str(fit_schedule.get(filename) or "").strip()
        try:
            if not schedule_value:
                raise ValueError("No calculated plan date was recorded for this workout")
            scheduled_for = _parse_date_input(schedule_value)
            if start_date and scheduled_for < start_date:
                continue
            if end_date and scheduled_for > end_date:
                continue
            item = _garmin_item_from_fit_bytes(data, source_name, scheduled_for)
            item["source_id"] = source_name
            items.append(item)
        except Exception as exc:
            parse_results.append({
                "source": source_name,
                "sourceId": source_name,
                "requestedScheduleDate": schedule_value or None,
                "ok": False,
                "error": str(exc),
            })
    return items, parse_results


def _add_plan_schedule_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    results = report.get("results") or []
    scheduled = sum(1 for result in results if result.get("ok") and result.get("scheduled"))
    schedule_failed = sum(
        1 for result in results
        if result.get("ok") and not result.get("scheduled")
    )
    attempted = int(report.get("attempted") or len(results))
    uploaded = int(report.get("successful") or 0)
    failed = int(report.get("failed") or 0)
    report["scheduled"] = scheduled
    report["scheduleFailed"] = schedule_failed
    if attempted and scheduled == attempted:
        label = "workout" if scheduled == 1 else "workouts"
        report["status"] = "success"
        report["message"] = (
            f"Confirmed: {scheduled} {label} uploaded and added to the Garmin calendar "
            "on the calculated plan dates."
        )
    elif scheduled:
        report["status"] = "partial"
        report["message"] = (
            f"{scheduled} of {attempted} workouts were added to the Garmin calendar; "
            f"{schedule_failed + failed} need attention."
        )
    elif uploaded:
        report["status"] = "partial"
        report["message"] = (
            f"{uploaded} workouts reached the Garmin library, but none could be added "
            "to the calendar."
        )
    else:
        report["status"] = "failed"
        report["message"] = f"No plan workouts were added to Garmin; {failed} failed."
    return report


@app.post("/api/garmin/plan-upload/{token}", response_class=JSONResponse)
def garmin_plan_upload(token: str, payload: Optional[Dict[str, Any]] = None):
    """Upload a selected plan window and schedule each FIT on its calculated date."""
    entry = _PLAN_PREVIEW_STORE.get(token)
    if not entry or not isinstance(entry, dict):
        raise HTTPException(404, "Plan workouts are no longer available; generate the plan again")

    payload = payload or {}
    raw_names = payload.get("fit_names") or []
    if raw_names and not isinstance(raw_names, list):
        raise HTTPException(400, "fit_names must be a list")
    fit_names = [Path(str(name)).name for name in raw_names if str(name).strip()]
    if len(fit_names) > 50:
        raise HTTPException(400, "Upload at most 50 workouts at a time")

    start_date: Optional[date] = None
    end_date: Optional[date] = None
    if str(payload.get("start_date") or "").strip():
        try:
            start_date = _parse_date_input(str(payload["start_date"]))
        except Exception as exc:
            raise HTTPException(400, f"Invalid start date: {exc}") from exc
    if payload.get("weeks") not in (None, ""):
        try:
            weeks = int(payload.get("weeks"))
            if weeks < 1 or weeks > 52:
                raise ValueError("weeks must be between 1 and 52")
        except Exception as exc:
            raise HTTPException(400, f"Invalid upload window: {exc}") from exc
        start_date = start_date or date.today()
        end_date = start_date + timedelta(days=(weeks * 7) - 1)
    elif str(payload.get("end_date") or "").strip():
        try:
            end_date = _parse_date_input(str(payload["end_date"]))
        except Exception as exc:
            raise HTTPException(400, f"Invalid end date: {exc}") from exc
    if start_date and end_date and end_date < start_date:
        raise HTTPException(400, "The upload window ends before it starts")

    try:
        items, parse_results = _plan_garmin_items(
            entry,
            fit_names=fit_names,
            start_date=start_date,
            end_date=end_date,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    selected_dates = [item["schedule_date"] for item in items if item.get("schedule_date")]
    replace = bool(payload.get("replace"))
    replace_start = start_date or (min(selected_dates) if selected_dates else None)
    replace_end = end_date or (max(selected_dates) if selected_dates else None)
    if not items and not (replace and replace_start and replace_end):
        detail = "; ".join(
            f"{result.get('source')}: {result.get('error')}" for result in parse_results
        ) or "No generated workout FITs fall inside the selected upload window"
        raise HTTPException(400, detail)
    request_key = json.dumps({
        "fits": sorted(item.get("source_id") or "" for item in items),
        "start": replace_start.isoformat() if replace_start else None,
        "end": replace_end.isoformat() if replace_end else None,
        "replace": replace,
    }, sort_keys=True)

    with _PLAN_GARMIN_UPLOAD_LOCK:
        reports = entry.setdefault("garmin_upload_reports", {})
        completed = reports.get(request_key) if isinstance(reports, dict) else None
        if isinstance(completed, dict):
            return JSONResponse({**completed, "replayed": True})
        if entry.get("garmin_upload_in_progress"):
            raise HTTPException(409, "This plan is already being added to Garmin")
        entry["garmin_upload_in_progress"] = True
    try:
        client = gc_opt.login_saved(str(_garmin_tokenstore_path()))
        replacement = {"removed": [], "errors": []}
        if replace and replace_start and replace_end:
            replacement = _remove_managed_garmin_range(client, replace_start, replace_end)
            if replacement["errors"]:
                detail = "; ".join(
                    f"{row.get('workout_name') or 'Workout'}: {row.get('error')}"
                    for row in replacement["errors"][:5]
                )
                raise HTTPException(
                    409,
                    "Could not safely clear every previously managed workout; no new workouts were uploaded. " + detail,
                )
        if not items:
            replaced_count = len(replacement["removed"])
            report = {
                "status": "success",
                "message": (
                    f"Removed {replaced_count} earlier PromptFit workout"
                    f"{'s' if replaced_count != 1 else ''}; no new workout FITs fall in this window."
                ),
                "attempted": 0,
                "successful": 0,
                "failed": 0,
                "scheduled": 0,
                "scheduleFailed": 0,
                "results": [],
                "replaced": replaced_count,
                "windowStart": replace_start.isoformat(),
                "windowEnd": replace_end.isoformat(),
            }
            entry.setdefault("garmin_upload_reports", {})[request_key] = report
            return JSONResponse(report)
        report = _upload_garmin_items(client, items, parse_results)
        report = _add_plan_schedule_summary(report)
        report["replaced"] = len(replacement["removed"])
        report["windowStart"] = replace_start.isoformat() if replace_start else None
        report["windowEnd"] = replace_end.isoformat() if replace_end else None
        _remember_plan_uploads(token, report)
        # Once Garmin has accepted any workout, replay the report instead of
        # risking duplicates if the browser repeats the request.
        if int(report.get("successful") or 0) > 0:
            entry.setdefault("garmin_upload_reports", {})[request_key] = report
        return JSONResponse(report)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        with _PLAN_GARMIN_UPLOAD_LOCK:
            entry["garmin_upload_in_progress"] = False


@app.post("/api/garmin/fit-upload", response_class=JSONResponse)
async def garmin_fit_upload(
    files: List[UploadFile] = File(...),
    schedule_date: str = Form(""),
):
    """Convert workout FIT files to Garmin workouts and add them to the account."""
    if not files:
        raise HTTPException(400, "Choose at least one FIT file")
    if len(files) > 50:
        raise HTTPException(400, "Upload at most 50 FIT files at a time")

    scheduled_for: Optional[date] = None
    if (schedule_date or "").strip():
        try:
            scheduled_for = _parse_date_input(schedule_date)
        except Exception as exc:
            raise HTTPException(400, f"Invalid schedule date: {exc}")

    items: List[Dict[str, Any]] = []
    parse_results: List[Dict[str, Any]] = []
    max_fit_bytes = 5 * 1024 * 1024
    for upload in files:
        filename = Path(upload.filename or "workout.fit").name
        if not filename.lower().endswith(".fit"):
            parse_results.append({
                "source": filename,
                "ok": False,
                "error": "Only .fit workout files are supported",
            })
            continue
        try:
            data = await upload.read(max_fit_bytes + 1)
            if len(data) > max_fit_bytes:
                raise ValueError("FIT file is larger than 5 MB")
            items.append(_garmin_item_from_fit_bytes(data, filename, scheduled_for))
        except Exception as exc:
            parse_results.append({"source": filename, "ok": False, "error": str(exc)})

    if not items:
        detail = "; ".join(
            f"{result.get('source')}: {result.get('error')}" for result in parse_results
        ) or "No valid workout FIT files found"
        raise HTTPException(400, detail)
    return _begin_garmin_upload(items, parse_results)


@app.post("/api/garmin/mfa", response_class=JSONResponse)
def garmin_mfa(payload: Dict[str, Any], request: Request):
    _cleanup_garmin_pending()
    token = str(payload.get("mfa_token") or "").strip()
    code = str(payload.get("mfa_code") or "").strip()
    pending = _GARMIN_PENDING_UPLOADS.get(token)
    if not pending:
        raise HTTPException(400, "That verification request expired. Start the upload again.")
    try:
        client = gc_opt.resume_login(
            pending["client"],
            pending["client_state"],
            code,
            tokenstore=pending.get("tokenstore"),
        )
    except Exception as exc:
        pending["attempts"] = int(pending.get("attempts") or 0) + 1
        if pending["attempts"] >= 3:
            _GARMIN_PENDING_UPLOADS.pop(token, None)
            raise HTTPException(400, f"Garmin verification failed: {exc}. Start the upload again.")
        raise HTTPException(400, f"Garmin verification failed: {exc}")

    _GARMIN_PENDING_UPLOADS.pop(token, None)
    if pending.get("operation") == "connect":
        _clear_legacy_garmin_keychain()
        return JSONResponse({
            **_garmin_connection_status(request, client=client),
            "message": "Garmin connected. Your password was discarded; only session tokens were saved.",
        })
    return JSONResponse(_upload_garmin_items(
        client,
        pending.get("items") or [],
        pending.get("initial_results") or [],
    ))

@app.post("/api/prompt-to-fit")
def prompt_to_fit(payload: Dict[str, Any], request: Request):
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "Provide 'prompt'")
    provider = (payload.get("provider") or "auto").strip()
    openai_model = (payload.get("openai_model") or "").strip() or None
    openrouter_model = (payload.get("openrouter_model") or "").strip() or None
    model = openai_model if provider == "openai" else (openrouter_model if provider == "openrouter" else None)
    # Choose API key based on provider; allow either field.
    api_key = (
        (payload.get("openai_api_key") or "").strip() if provider in ("openai","auto") else ""
    ) or (
        (payload.get("openrouter_api_key") or "").strip() if provider in ("openrouter","auto") else ""
    ) or (payload.get("api_key") or "").strip()
    hmp = payload.get("hmp")
    pace_min_per_mile = _parse_pace_input(hmp)
    race_distance = _normalize_race_distance(payload.get("race_distance")) or ((payload.get("race_distance") or "").strip().lower() or None)
    pace_profile = _pace_profile_from_payload(
        payload,
        reference_pace=pace_min_per_mile,
        race_distance=race_distance,
    )
    goal_key = normalize_pace_race_distance(race_distance) or "half_marathon"
    pace_min_per_mile = estimate_anchor_pace(goal_key, pace_profile) or pace_min_per_mile
    targets_enabled = bool(payload.get("targets"))
    target_mode = (payload.get("target_mode") or "pace").strip()
    try:
        target_margin = int(payload.get("target_margin") or 30)
    except Exception:
        target_margin = 30
    sideload = bool(payload.get("sideload"))

    plan = parse_prompt_to_plan(
        prompt, pace_min_per_mile, race_distance, provider, model, api_key, pace_profile
    )
    workouts = plan.get("workouts") or []
    if not workouts:
        raise HTTPException(400, "Model did not return workouts. Try a clearer prompt.")

    # Build filenames and display names
    # If dates provided, compute week/day prefix relative to earliest Monday
    def _parse_date(s: Optional[str]) -> Optional[date]:
        if not s:
            return None
        try:
            dt = datetime.strptime(s, "%Y-%m-%d")
            return date(dt.year, dt.month, dt.day)
        except Exception:
            return None

    dates: List[Optional[date]] = [ _parse_date(w.get("date")) for w in workouts ]
    valid_dates = [d for d in dates if d]
    start_monday: Optional[date] = None
    if valid_dates:
        first = min(valid_dates)
        start_monday = first - timedelta(days=first.weekday())

    def _prefix_for(d: Optional[date]) -> str:
        if not (start_monday and d):
            return ""
        delta = (d - start_monday).days
        week = 1 + (delta // 7)
        day = 1 + d.weekday()
        return f"{week:02d}w{day:02d}d"

    hm_calc = None
    try:
        import importlib
        import hm_plan_to_garmin as hm_calc_mod
        import hm_plan_calendar as gen_calc
        hm_calc = importlib.reload(hm_calc_mod)
        _apply_export_pace_context(
            pace_min_per_mile=pace_min_per_mile,
            race_distance=race_distance,
            pace_profile=pace_profile,
            hm_module=hm_calc,
            gen_module=gen_calc,
        )
    except Exception:
        hm_calc = None

    # Export
    outputs: List[tuple[str, bytes]] = []
    for idx, w in enumerate(workouts, start=1):
        nm = (w.get("name") or (w.get("type") or "Workout")).strip().capitalize()
        d = dates[idx-1]
        pref = _prefix_for(d)
        # Estimate miles/time for descriptive names and file names
        try:
            if hm_calc is not None:
                mi, minutes = hm_calc.compute_obj_miles_minutes(w)
            else:
                mi, minutes = (0.0, 0.0)
        except Exception:
            mi, minutes = (0.0, 0.0)
        miles_token = f"{mi:.1f} mi" if (mi and mi > 0) else ""
        time_token = _time_token(minutes)
        # Build Garmin display title: prefix + type + miles + time (ASCII-safe and concise)
        title_parts = [p for p in [pref, nm, miles_token, time_token] if p]
        disp = " - ".join(title_parts).strip()
        try:
            fit_bytes = _export_fit_bytes(disp, w, targets_enabled=targets_enabled,
                                          target_mode=target_mode, target_margin=target_margin,
                                          pace_min_per_mile=pace_min_per_mile,
                                          race_distance=race_distance,
                                          pace_profile=pace_profile)
        except Exception as ex:
            raise HTTPException(400, f"Failed exporting workout #{idx}: {ex}")
        # Descriptive filename: [prefix_]Type_[miles]_[time].fit
        head = " ".join(p for p in [(pref or "").strip(), nm] if p).strip()
        head_safe = _safe_name(head)
        suffix_parts = [p for p in [miles_token, time_token] if p]
        if suffix_parts:
            fname = f"{head_safe}_{'_'.join(suffix_parts)}.fit"
        else:
            fname_core = _safe_name(d.isoformat() if d else f"w{idx:02d}")
            fname = f"{fname_core}_{_safe_name(nm)}.fit"
        outputs.append((fname, fit_bytes))

    # Optional sideload to watch (macOS only)
    sideload_msg = ""
    if sideload:
        dest = _find_garmin_dest_dir()
        if dest:
            copied = 0
            for fname, data in outputs:
                try:
                    p = _unique_path(dest, fname)
                    with open(p, 'wb') as f:
                        f.write(data)
                    copied += 1
                except Exception:
                    pass
            sideload_msg = f"Sideloaded {copied} file(s) to: {dest}"
        else:
            sideload_msg = "No GARMIN device mounted under /Volumes."

    # Single vs zip (return download and annotate with sideload header)
    if len(outputs) == 1:
        name, data = outputs[0]
        resp = StreamingResponse(io.BytesIO(data), media_type="application/octet-stream")
        resp.headers["Content-Disposition"] = f"attachment; filename={name}"
        if sideload_msg:
            resp.headers["X-Sideload"] = sideload_msg
        return resp

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in outputs:
            zf.writestr(name, data)
    buf.seek(0)
    resp = StreamingResponse(buf, media_type="application/zip")
    resp.headers["Content-Disposition"] = "attachment; filename=workouts.zip"
    if sideload_msg:
        resp.headers["X-Sideload"] = sideload_msg
    return resp


@app.post("/api/prompt-to-garmin")
def prompt_to_garmin(payload: Dict[str, Any], request: Request):
    """Create workouts in Garmin Connect and optionally schedule them.
    WARNING: Uses unofficial APIs via `garminconnect` and a saved local session.
    """
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "Provide 'prompt'")
    provider = (payload.get("provider") or "auto").strip()
    openai_model = (payload.get("openai_model") or "").strip() or None
    openrouter_model = (payload.get("openrouter_model") or "").strip() or None
    model = openai_model if provider == "openai" else (openrouter_model if provider == "openrouter" else None)
    api_key = (
        (payload.get("openai_api_key") or "").strip() if provider in ("openai","auto") else ""
    ) or (
        (payload.get("openrouter_api_key") or "").strip() if provider in ("openrouter","auto") else ""
    ) or (payload.get("api_key") or "").strip() or None

    hmp = payload.get("hmp")
    pace_min_per_mile = _parse_pace_input(hmp)
    race_distance = _normalize_race_distance(payload.get("race_distance")) or ((payload.get("race_distance") or "").strip().lower() or None)
    pace_profile = _pace_profile_from_payload(
        payload,
        reference_pace=pace_min_per_mile,
        race_distance=race_distance,
    )
    goal_key = normalize_pace_race_distance(race_distance) or "half_marathon"
    pace_min_per_mile = estimate_anchor_pace(goal_key, pace_profile) or pace_min_per_mile

    targets_enabled = bool(payload.get("targets"))
    target_mode = (payload.get("target_mode") or "pace").strip()
    try:
        target_margin = int(payload.get("target_margin") or 30)
    except Exception:
        target_margin = 30

    # Parse plan
    plan = parse_prompt_to_plan(
        prompt, pace_min_per_mile, race_distance, provider, model, api_key, pace_profile
    )
    workouts = plan.get("workouts") or []
    if not workouts:
        raise HTTPException(400, "Model did not return workouts. Try a clearer prompt.")

    # Optional: schedule date if provided on each workout
    from datetime import datetime as _dt
    def _parse_date(s: Optional[str]):
        if not s:
            return None
        try:
            x = _dt.strptime(s, "%Y-%m-%d").date()
            return x
        except Exception:
            return None

    garmin_items: List[Dict[str, Any]] = []
    # Convert to steps via hm module to ensure parity with FIT
    import importlib, hm_plan_to_garmin as hm
    import hm_plan_calendar as gen
    hm = importlib.reload(hm)
    hm.TARGETS_ENABLED = bool(targets_enabled)
    hm.TARGET_MODE = target_mode if target_mode in ("pace","speed") else "pace"
    hm.TARGET_MARGIN_SEC = int(target_margin)
    _apply_export_pace_context(
        pace_min_per_mile=pace_min_per_mile,
        race_distance=race_distance,
        pace_profile=pace_profile,
        hm_module=hm,
        gen_module=gen,
    )

    for w in workouts:
        nm = (w.get("name") or (w.get("type") or "Workout")).strip().capitalize()
        dt = _parse_date(w.get("date"))
        steps = hm.workout_to_garmin_steps(w)
        if not steps:
            continue
        wjson = gc_opt.create_workout_json(nm, steps)
        garmin_items.append({
            "source_name": nm,
            "workout_name": nm,
            "workout_json": wjson,
            "schedule_date": dt,
        })

    if not garmin_items:
        raise HTTPException(400, "No Garmin-compatible workout steps were generated")
    return _begin_garmin_upload(garmin_items)


@app.get("/api/prompt-to-garmin", response_class=JSONResponse)
def prompt_to_garmin_get():
    return JSONResponse({
        "error": "Method Not Allowed",
        "hint": "POST JSON to this endpoint",
        "example_curl": "Connect Garmin once in the web app, then POST the prompt payload to this endpoint."
    })


@app.post("/api/preview-plan", response_class=JSONResponse)
def preview_plan(payload: Dict[str, Any], request: Request):
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "Provide 'prompt'")
    provider = (payload.get("provider") or "auto").strip()
    openai_model = (payload.get("openai_model") or "").strip() or None
    openrouter_model = (payload.get("openrouter_model") or "").strip() or None
    model = openai_model if provider == "openai" else (openrouter_model if provider == "openrouter" else None)
    api_key = (
        (payload.get("openai_api_key") or "").strip() if provider in ("openai","auto") else ""
    ) or (
        (payload.get("openrouter_api_key") or "").strip() if provider in ("openrouter","auto") else ""
    ) or (payload.get("api_key") or "").strip() or None
    hmp = payload.get("hmp")
    hmp_val = _parse_pace_input(hmp)
    race_distance = (payload.get("race_distance") or "").strip().lower() or None
    pace_profile = _pace_profile_from_payload(
        payload,
        reference_pace=hmp_val,
        race_distance=race_distance,
    )
    goal_key = normalize_pace_race_distance(race_distance) or "half_marathon"
    hmp_val = estimate_anchor_pace(goal_key, pace_profile) or hmp_val
    plan = parse_prompt_to_plan(
        prompt, hmp_val, race_distance, provider, model, api_key, pace_profile
    )
    return JSONResponse(plan)


@app.post("/api/plan-text-to-json", response_class=JSONResponse)
def plan_text_to_json(payload: Dict[str, Any], request: Request):
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "Provide 'prompt'")
    provider = (payload.get("provider") or "auto").strip()
    openai_model = (payload.get("openai_model") or "").strip() or None
    openrouter_model = (payload.get("openrouter_model") or "").strip() or None
    model = openai_model if provider == "openai" else (openrouter_model if provider == "openrouter" else None)
    api_key = (
        (payload.get("openai_api_key") or "").strip() if provider in ("openai","auto") else ""
    ) or (
        (payload.get("openrouter_api_key") or "").strip() if provider in ("openrouter","auto") else ""
    ) or (payload.get("api_key") or "").strip() or None
    race_distance = (payload.get("race_distance") or "").strip().lower() or None
    pace_profile = _pace_profile_from_payload(payload, race_distance=race_distance)
    plan = parse_plan_text_to_json(
        prompt, provider, model, api_key, race_distance, pace_profile
    )
    return JSONResponse(plan)


@app.post("/api/fit-editor/parse", response_class=JSONResponse)
async def fit_editor_parse(files: List[UploadFile] = File(...)):
    results = []
    for f in files:
        try:
            data = await f.read()
            parsed = _fit_editor_parse_bytes(data)
            results.append({
                "name": f.filename,
                "workout_name": parsed.get("workout_name") or f.filename or "Workout",
                "legs": parsed.get("legs") or [],
                "graph": parsed.get("graph") or {"segments": [], "total_seconds": 0},
            })
        except Exception as e:
            results.append({
                "name": f.filename,
                "error": str(e),
                "workout_name": f.filename or "Workout",
                "legs": [],
                "graph": {"error": str(e)},
            })
    return JSONResponse({"results": results})


@app.post("/api/fit-editor/export")
def fit_editor_export(payload: Dict[str, Any]):
    name = (payload.get("name") or "Workout").strip()
    deterministic = bool(payload.get("deterministic", True))
    legs = payload.get("legs")
    try:
        steps = _fit_editor_steps_from_legs(legs)
    except Exception as e:
        raise HTTPException(400, f"Invalid legs: {e}")

    try:
        import final_spec_compliant_fix as spec
        with tempfile.TemporaryDirectory() as td:
            outp = os.path.join(td, "w.fit")
            spec.export_spec_compliant_fit_workout(
                name,
                steps,
                outp,
                estimated_miles=0.0,
                deterministic=deterministic,
            )
            with open(outp, "rb") as f:
                fit_bytes = f.read()
    except Exception as e:
        raise HTTPException(400, f"FIT export failed: {e}")

    est_miles, est_minutes = _fit_editor_estimate_miles_minutes(legs)
    out_name = _build_prompt_style_fit_filename(name, est_miles, est_minutes)
    resp = StreamingResponse(io.BytesIO(fit_bytes), media_type="application/octet-stream")
    resp.headers["Content-Disposition"] = f"attachment; filename={out_name}"
    return resp


@app.post("/api/parse-fit", response_class=JSONResponse)
async def parse_fit(files: List[UploadFile] = File(...)):
    results = []
    for f in files:
        try:
            data = await f.read()
            summary = _fit_summary_bytes(data)
            graph = _fit_graph_bytes(data)
        except Exception as e:
            summary = f"ERROR: {e}"
            graph = {"error": str(e)}
        results.append({"name": f.filename, "summary": summary, "graph": graph})
    return JSONResponse({"results": results})


@app.post("/api/plan-workout-fit")
def plan_workout_fit(payload: Dict[str, Any], request: Request):
    workout = payload.get("workout")
    if not isinstance(workout, dict):
        raise HTTPException(400, "Provide workout object")
    name = (payload.get("name") or "Workout").strip()
    pace_val = _parse_pace_input(payload.get("race_pace"))
    race_distance = _normalize_race_distance(payload.get("race_distance")) or ((payload.get("race_distance") or "").strip().lower() or None)
    pace_profile = _pace_profile_from_payload(
        payload,
        reference_pace=pace_val,
        race_distance=race_distance,
    )
    goal_key = normalize_pace_race_distance(race_distance) or "half_marathon"
    pace_val = estimate_anchor_pace(goal_key, pace_profile) or pace_val
    targets_enabled = bool(payload.get("targets", True))
    target_mode = (payload.get("target_mode") or "pace").strip()
    try:
        target_margin = int(payload.get("target_margin") or 30)
    except Exception:
        target_margin = 30
    include_wu_cd = bool(payload.get("include_wu_cd", False))
    try:
        wu_cd_distance = float(payload.get("wu_cd_distance")) if payload.get("wu_cd_distance") not in (None, "") else None
    except Exception:
        wu_cd_distance = None
    try:
        wu_cd_duration = float(payload.get("wu_cd_duration")) if payload.get("wu_cd_duration") not in (None, "") else None
    except Exception:
        wu_cd_duration = None
    try:
        fit_bytes = _export_fit_bytes(_safe_name(name), workout, targets_enabled=targets_enabled,
                                     target_mode=target_mode, target_margin=target_margin,
                                     pace_min_per_mile=pace_val,
                                     race_distance=race_distance,
                                     pace_profile=pace_profile,
                                     include_implicit_wu_cd=include_wu_cd,
                                     wu_cd_distance_miles=wu_cd_distance,
                                     wu_cd_duration_min=wu_cd_duration)
    except Exception as e:
        raise HTTPException(400, f"FIT generation failed: {e}")
    resp = StreamingResponse(io.BytesIO(fit_bytes), media_type="application/octet-stream")
    resp.headers["Content-Disposition"] = f"attachment; filename={_safe_name(name)}.fit"
    return resp
