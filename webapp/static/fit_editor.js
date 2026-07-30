const FE_STATE = {
  name: 'Workout',
  legs: [],
};
const FE_CHART_STATE = {
  hits: [],
  selectedLegIdx: null,
  pendingScrollToSelected: false,
};
const FE_PACE_RANGE_HALF_MIN = 0.5; // +/- 30 sec per mile
const FE_DND_STATE = {
  dragIndex: null,
  subDrag: null,
  autoScrollRaf: null,
  autoScrollLastTs: 0,
  autoScrollX: 0,
  autoScrollY: 0,
};
const FE_OPEN_FIT_KEY_PREFIX = 'promptfit_open_fit_payload_';

function feSetStatus(msg){
  const el = document.getElementById('fe_status');
  if (el) el.textContent = msg || '';
}

function feClamp(n, lo, hi){
  return Math.max(lo, Math.min(hi, n));
}

function feDefaultStep(){
  return {
    kind: 'step',
    label: 'Run',
    intensity: 'active',
    duration_type: 'time',
    duration_value: 300,
    target_type: 'open',
    speed_low_mps: null,
    speed_high_mps: null,
    pace_slow_min_per_mi: null,
    pace_fast_min_per_mi: null,
  };
}

function feDefaultRepeat(startIndex){
  return {
    kind: 'repeat',
    label: 'Repeat block',
    repeat_start_index: Math.max(0, Number.isFinite(startIndex) ? startIndex : 0),
    repeat_count: 2,
    block_len: 1,
    skip_last_leg_on_final_repeat: false,
  };
}

function feCopy(obj){
  return JSON.parse(JSON.stringify(obj));
}

function feEscHtml(text){
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function feParsePace(val){
  if (val === null || val === undefined) return null;
  const s = String(val).trim();
  if (!s) return null;
  const m = s.match(/^(\d{1,2})\s*:\s*([0-5]?\d)$/);
  if (m){
    const mins = parseInt(m[1], 10);
    const secs = parseInt(m[2], 10);
    return mins + (secs / 60);
  }
  const f = parseFloat(s);
  return Number.isFinite(f) && f > 0 ? f : null;
}

function feFormatPace(minPerMile){
  if (!Number.isFinite(minPerMile) || minPerMile <= 0) return '';
  const total = Math.round(minPerMile * 60);
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  return `${mins}:${String(secs).padStart(2, '0')}`;
}

function feFormatTime(totalSeconds){
  const sec = Math.max(0, Math.round(Number(totalSeconds) || 0));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function feFormatDistance(distanceMeters){
  const meters = Number(distanceMeters);
  if (!Number.isFinite(meters) || meters <= 0) return '0 m';
  const miles = meters / 1609.34;
  if (miles >= 0.1) return `${miles.toFixed(2)} mi`;
  return `${Math.round(meters)} m`;
}

function feLegDurationLabel(leg){
  if (!leg || leg.kind !== 'step') return '';
  if (leg.duration_type === 'time') return feFormatTime(Number(leg.duration_value) || 0);
  if (leg.duration_type === 'distance') return feFormatDistance(Number(leg.duration_value) || 0);
  if (leg.duration_type === 'open') return leg.intensity === 'rest' ? 'open rest' : 'open';
  return '';
}

function feLegPaceLabel(leg){
  if (!leg || leg.kind !== 'step') return '';
  let slow = Number.isFinite(Number(leg.pace_slow_min_per_mi)) ? Number(leg.pace_slow_min_per_mi) : null;
  let fast = Number.isFinite(Number(leg.pace_fast_min_per_mi)) ? Number(leg.pace_fast_min_per_mi) : null;
  if ((!slow || !fast) && Number.isFinite(Number(leg.speed_low_mps)) && Number.isFinite(Number(leg.speed_high_mps))){
    slow = fePaceFromSpeed(Number(leg.speed_low_mps));
    fast = fePaceFromSpeed(Number(leg.speed_high_mps));
  }
  if (slow && fast) return `${feFormatPace(slow)}-${feFormatPace(fast)}/mi`;
  if (slow) return `${feFormatPace(slow)}/mi`;
  if (fast) return `${feFormatPace(fast)}/mi`;
  return '';
}

function feLegCenterPace(leg){
  if (!leg || leg.kind !== 'step') return null;
  let slow = Number.isFinite(Number(leg.pace_slow_min_per_mi)) ? Number(leg.pace_slow_min_per_mi) : null;
  let fast = Number.isFinite(Number(leg.pace_fast_min_per_mi)) ? Number(leg.pace_fast_min_per_mi) : null;
  if ((!slow || !fast) && Number.isFinite(Number(leg.speed_low_mps)) && Number.isFinite(Number(leg.speed_high_mps))){
    slow = fePaceFromSpeed(Number(leg.speed_low_mps));
    fast = fePaceFromSpeed(Number(leg.speed_high_mps));
  }
  if (slow && fast) return (slow + fast) / 2.0;
  if (slow) return slow;
  if (fast) return fast;
  return null;
}

function feSpeedFromPace(paceMinPerMile){
  if (!Number.isFinite(paceMinPerMile) || paceMinPerMile <= 0) return null;
  return 1609.34 / (paceMinPerMile * 60.0);
}

function fePaceFromSpeed(mps){
  if (!Number.isFinite(mps) || mps <= 0) return null;
  return (1609.34 / mps) / 60.0;
}

function feDefaultPaceByIntensity(intensity){
  if (intensity === 'rest') return 13.0;
  if (intensity === 'warmup' || intensity === 'cooldown') return 10.0;
  return 8.0;
}

function feAddTemplateLeg(templateKey){
  const key = String(templateKey || '').toLowerCase();
  let leg = null;
  let statusLabel = null;

  if (key === 'wu_cd'){
    const hasWarmup = FE_STATE.legs.some((l) => l && l.kind === 'step' && l.intensity === 'warmup');
    const intensity = hasWarmup ? 'cooldown' : 'warmup';
    const label = hasWarmup ? 'Cool Down' : 'Warm Up';
    const pace = 9.0;
    const mps = feSpeedFromPace(pace);
    leg = {
      kind: 'step',
      label,
      intensity,
      duration_type: 'distance',
      duration_value: 1609.34, // 1 mile
      target_type: 'speed',
      pace_slow_min_per_mi: pace,
      pace_fast_min_per_mi: pace,
      speed_low_mps: mps,
      speed_high_mps: mps,
    };
    statusLabel = `${label} (1 mi @ 9:00/mi)`;
  } else if (key === 'work_rep'){
    const paceSlow = 7.0;
    const paceFast = 6.5;
    leg = {
      kind: 'step',
      label: 'Work Rep',
      intensity: 'active',
      duration_type: 'time',
      duration_value: 240,
      target_type: 'speed',
      pace_slow_min_per_mi: paceSlow,
      pace_fast_min_per_mi: paceFast,
      speed_low_mps: feSpeedFromPace(paceSlow),
      speed_high_mps: feSpeedFromPace(paceFast),
    };
    statusLabel = 'Work rep (4:00 @ 6:30-7:00/mi)';
  } else if (key === 'recovery_jog'){
    const pace = 10.0;
    const mps = feSpeedFromPace(pace);
    leg = {
      kind: 'step',
      label: 'Recovery Jog',
      intensity: 'rest',
      duration_type: 'time',
      duration_value: 120,
      target_type: 'speed',
      pace_slow_min_per_mi: pace,
      pace_fast_min_per_mi: pace,
      speed_low_mps: mps,
      speed_high_mps: mps,
    };
    statusLabel = 'Recovery Jog (2:00 @ 10:00/mi)';
  } else if (key === 'rest_2min'){
    leg = {
      kind: 'step',
      label: 'Rest',
      intensity: 'rest',
      duration_type: 'time',
      duration_value: 120,
      target_type: 'open',
      pace_slow_min_per_mi: null,
      pace_fast_min_per_mi: null,
      speed_low_mps: null,
      speed_high_mps: null,
    };
    statusLabel = 'Rest 2 min (open/no walk-run target)';
  }

  if (!leg) return;
  FE_STATE.legs.push(feNormalizeLeg(leg));
  feRenderLegs();
  feSetStatus(`Added template: ${statusLabel}.`);
}

function feNormalizeLeg(leg){
  const base = feCopy(leg || {});
  const kind = (base.kind || 'step').toLowerCase();
  if (kind === 'repeat'){
    const start = parseInt(base.repeat_start_index, 10);
    const count = parseInt(base.repeat_count, 10);
    return {
      kind: 'repeat',
      label: String(base.label || 'Repeat block'),
      repeat_start_index: Number.isFinite(start) ? Math.max(0, start) : 0,
      repeat_count: Number.isFinite(count) ? Math.max(1, count) : 1,
      block_len: Number.isFinite(parseInt(base.block_len, 10)) ? Math.max(1, parseInt(base.block_len, 10)) : 1,
      skip_last_leg_on_final_repeat: !!base.skip_last_leg_on_final_repeat,
    };
  }
  const durationType = ['time', 'distance', 'open'].includes(base.duration_type) ? base.duration_type : 'time';
  let durationValue = null;
  if (durationType !== 'open'){
    const dv = Number(base.duration_value);
    durationValue = Number.isFinite(dv) ? dv : 0;
  }
  const targetType = base.target_type === 'speed' ? 'speed' : 'open';
  return {
    kind: 'step',
    label: String(base.label || 'Run'),
    intensity: ['active', 'rest', 'warmup', 'cooldown'].includes(base.intensity) ? base.intensity : 'active',
    duration_type: durationType,
    duration_value: durationValue,
    duration_unit: base.duration_unit || (durationType === 'distance' ? 'meters' : (durationType === 'time' ? 'seconds' : 'open')),
    target_type: targetType,
    speed_low_mps: Number.isFinite(Number(base.speed_low_mps)) ? Number(base.speed_low_mps) : null,
    speed_high_mps: Number.isFinite(Number(base.speed_high_mps)) ? Number(base.speed_high_mps) : null,
    pace_slow_min_per_mi: Number.isFinite(Number(base.pace_slow_min_per_mi)) ? Number(base.pace_slow_min_per_mi) : null,
    pace_fast_min_per_mi: Number.isFinite(Number(base.pace_fast_min_per_mi)) ? Number(base.pace_fast_min_per_mi) : null,
  };
}

function feNormalizeState(){
  FE_STATE.name = String(FE_STATE.name || 'Workout');
  FE_STATE.legs = Array.isArray(FE_STATE.legs) ? FE_STATE.legs.map(feNormalizeLeg) : [];
}

function feHasActiveDrag(){
  return Number.isFinite(FE_DND_STATE.dragIndex) || (FE_DND_STATE.subDrag && Number.isFinite(FE_DND_STATE.subDrag.stepIdx));
}

function feIsInteractiveTarget(el){
  if (!el || !el.closest) return false;
  return !!el.closest('input,select,textarea,button,a');
}

function feAutoScrollOnDrag(event){
  if (!event) return;
  FE_DND_STATE.autoScrollX = Number(event.clientX) || 0;
  FE_DND_STATE.autoScrollY = Number(event.clientY) || 0;
}

function feStepAutoScroll(ts){
  if (!feHasActiveDrag()){
    FE_DND_STATE.autoScrollRaf = null;
    FE_DND_STATE.autoScrollLastTs = 0;
    return;
  }
  const last = FE_DND_STATE.autoScrollLastTs || ts;
  const dtMs = Math.max(8, Math.min(40, ts - last));
  FE_DND_STATE.autoScrollLastTs = ts;
  const dt = Math.min(1.25, dtMs / 16.6667); // ~frames at 60hz, capped to reduce jumps

  const x = FE_DND_STATE.autoScrollX;
  const y = FE_DND_STATE.autoScrollY;

  const list = document.getElementById('fe_legs');
  if (list){
    const rect = list.getBoundingClientRect();
    if (x >= rect.left && x <= rect.right){
      const edge = 88;
      const maxPxPerFrame = 4.5;
      const topDist = y - rect.top;
      const botDist = rect.bottom - y;
      let vy = 0;
      if (topDist < edge){
        const ratio = Math.max(0, Math.min(1, (edge - topDist) / edge));
        vy = -maxPxPerFrame * ratio * ratio;
      } else if (botDist < edge){
        const ratio = Math.max(0, Math.min(1, (edge - botDist) / edge));
        vy = maxPxPerFrame * ratio * ratio;
      }
      if (vy !== 0) list.scrollTop += vy * dt;
    }
  }

  const winEdge = 120;
  const winMaxPxPerFrame = 2.8;
  let winVy = 0;
  if (y < winEdge){
    const ratio = Math.max(0, Math.min(1, (winEdge - y) / winEdge));
    winVy = -winMaxPxPerFrame * ratio * ratio;
  } else if (y > (window.innerHeight - winEdge)){
    const ratio = Math.max(0, Math.min(1, (y - (window.innerHeight - winEdge)) / winEdge));
    winVy = winMaxPxPerFrame * ratio * ratio;
  }
  if (winVy !== 0) window.scrollBy(0, winVy * dt);

  FE_DND_STATE.autoScrollRaf = window.requestAnimationFrame(feStepAutoScroll);
}

function feStartAutoScrollLoop(){
  if (FE_DND_STATE.autoScrollRaf) return;
  FE_DND_STATE.autoScrollLastTs = 0;
  FE_DND_STATE.autoScrollRaf = window.requestAnimationFrame(feStepAutoScroll);
}

function feStopAutoScrollLoop(){
  if (FE_DND_STATE.autoScrollRaf){
    try { window.cancelAnimationFrame(FE_DND_STATE.autoScrollRaf); } catch (e) {}
  }
  FE_DND_STATE.autoScrollRaf = null;
  FE_DND_STATE.autoScrollLastTs = 0;
}

function feComputeRepeatBundleInfo(){
  const bundledSet = new Set();
  const repeatBundles = {};
  FE_STATE.legs.forEach((leg, idx) => {
    if (!leg || leg.kind !== 'repeat') return;
    const start = feClamp(parseInt(leg.repeat_start_index, 10) || 0, 0, Math.max(0, idx - 1));
    const rowIndices = [];
    for (let i = start; i < idx; i++){
      const li = FE_STATE.legs[i];
      if (li && li.kind === 'step'){
        rowIndices.push(i);
        bundledSet.add(i);
      }
    }
    repeatBundles[idx] = rowIndices;
  });
  return { bundledSet, repeatBundles };
}

function feGetVisibleComponents(){
  const info = feComputeRepeatBundleInfo();
  const components = [];
  FE_STATE.legs.forEach((leg, idx) => {
    if (!leg) return;
    if (info.bundledSet.has(idx)) return;
    if (leg.kind === 'repeat'){
      const bundle = info.repeatBundles[idx] || [];
      const fallbackStart = feClamp(parseInt(leg.repeat_start_index, 10) || Math.max(0, idx - 1), 0, Math.max(0, idx - 1));
      const startIdx = bundle.length ? Math.min(...bundle) : fallbackStart;
      components.push({
        kind: 'repeat',
        controlIdx: idx,
        startIdx: startIdx,
        bundleIndices: bundle,
      });
    } else {
      components.push({
        kind: 'step',
        controlIdx: idx,
        startIdx: idx,
        bundleIndices: [],
      });
    }
  });
  return { ...info, components };
}

function feReorderLegs(fromIdx, toIdx){
  const n = FE_STATE.legs.length;
  if (!Number.isFinite(fromIdx) || !Number.isFinite(toIdx)) return null;
  if (fromIdx < 0 || fromIdx >= n) return null;
  if (toIdx < 0) toIdx = 0;
  if (toIdx > n) toIdx = n;
  const srcLeg = FE_STATE.legs[fromIdx];
  let segStart = fromIdx;
  let segEnd = fromIdx;
  if (srcLeg && srcLeg.kind === 'repeat'){
    segStart = feClamp(parseInt(srcLeg.repeat_start_index, 10) || 0, 0, Math.max(0, fromIdx - 1));
    segEnd = fromIdx;
  }
  const segLen = (segEnd - segStart + 1);
  if (segLen <= 0) return null;
  if (toIdx >= segStart && toIdx <= segEnd + 1) return null;

  const orig = FE_STATE.legs.map((x) => feCopy(x));
  const order = Array.from({ length: n }, (_, i) => i);
  const movedSegment = order.splice(segStart, segLen);
  let insertAt = toIdx;
  if (insertAt > segEnd) insertAt -= segLen;
  if (insertAt < 0) insertAt = 0;
  if (insertAt > order.length) insertAt = order.length;
  order.splice(insertAt, 0, ...movedSegment);
  const mapOldToNew = Array(n).fill(-1);
  order.forEach((oldIdx, newIdx) => { mapOldToNew[oldIdx] = newIdx; });

  const newLegs = order.map((oldIdx) => feNormalizeLeg(orig[oldIdx]));
  newLegs.forEach((leg, idx) => {
    if (!leg || leg.kind !== 'repeat') return;
    const oldStart = parseInt(leg.repeat_start_index, 10);
    let mapped = Number.isFinite(oldStart) ? mapOldToNew[feClamp(oldStart, 0, n - 1)] : 0;
    if (!Number.isFinite(mapped) || mapped < 0) mapped = 0;
    if (mapped >= idx) mapped = Math.max(0, idx - 1);
    leg.repeat_start_index = mapped;
  });
  FE_STATE.legs = newLegs;
  return {
    mapOldToNew,
    movedOld: segStart,
    movedNew: mapOldToNew[segStart],
  };
}

function feIncludeStepInRepeat(stepIdx, repeatIdx){
  if (!Number.isFinite(stepIdx) || !Number.isFinite(repeatIdx)) return;
  if (stepIdx < 0 || stepIdx >= FE_STATE.legs.length) return;
  if (repeatIdx < 0 || repeatIdx >= FE_STATE.legs.length) return;
  const src = FE_STATE.legs[stepIdx];
  const rep = FE_STATE.legs[repeatIdx];
  if (!src || src.kind !== 'step' || !rep || rep.kind !== 'repeat') return;

  const oldStart = feClamp(parseInt(rep.repeat_start_index, 10) || 0, 0, Math.max(0, repeatIdx - 1));
  if (stepIdx >= oldStart && stepIdx < repeatIdx){
    feSetStatus(`Leg ${stepIdx + 1} is already bundled in repeat ${repeatIdx + 1}.`);
    return;
  }

  const res = feReorderLegs(stepIdx, repeatIdx);
  if (!res) return;
  const newRepeatIdx = Number.isFinite(res.mapOldToNew[repeatIdx]) ? res.mapOldToNew[repeatIdx] : repeatIdx;
  const repeatLeg = FE_STATE.legs[newRepeatIdx];
  if (!repeatLeg || repeatLeg.kind !== 'repeat') return;
  const mappedStart = Number.isFinite(res.mapOldToNew[oldStart]) ? res.mapOldToNew[oldStart] : 0;
  const movedNew = Number.isFinite(res.movedNew) ? res.movedNew : Math.max(0, newRepeatIdx - 1);
  repeatLeg.repeat_start_index = feClamp(Math.min(mappedStart, movedNew), 0, Math.max(0, newRepeatIdx - 1));
  FE_STATE.legs[newRepeatIdx] = feNormalizeLeg(repeatLeg);
  feSetStatus(`Added leg ${movedNew + 1} to repeat leg ${newRepeatIdx + 1}.`);
  feRenderLegs();
}

function feSetNameInput(){
  const nameInput = document.getElementById('fe_name');
  if (nameInput) nameInput.value = FE_STATE.name || 'Workout';
}

function feLegSegment(leg, sourceIdx){
  if (!leg || leg.kind !== 'step') return null;
  const intensity = leg.intensity || 'active';

  let paceSlow = Number.isFinite(leg.pace_slow_min_per_mi) ? Number(leg.pace_slow_min_per_mi) : null;
  let paceFast = Number.isFinite(leg.pace_fast_min_per_mi) ? Number(leg.pace_fast_min_per_mi) : null;

  if ((!paceSlow || !paceFast) && Number.isFinite(leg.speed_low_mps) && Number.isFinite(leg.speed_high_mps)){
    paceSlow = fePaceFromSpeed(Number(leg.speed_low_mps));
    paceFast = fePaceFromSpeed(Number(leg.speed_high_mps));
  }

  let pace = null;
  if (Number.isFinite(paceSlow) && Number.isFinite(paceFast)) pace = (paceSlow + paceFast) / 2.0;
  else if (Number.isFinite(paceSlow)) pace = paceSlow;
  else if (Number.isFinite(paceFast)) pace = paceFast;
  else pace = feDefaultPaceByIntensity(intensity);

  let durationS = 0;
  let distanceM = 0;
  let durationInferred = false;
  if (leg.duration_type === 'time'){
    durationS = Math.max(0, Number(leg.duration_value) || 0);
    const mps = feSpeedFromPace(pace);
    distanceM = (mps && mps > 0) ? durationS * mps : 0;
  } else if (leg.duration_type === 'distance'){
    const meters = Math.max(0, Number(leg.duration_value) || 0);
    const mps = feSpeedFromPace(pace);
    durationS = (mps && mps > 0) ? (meters / mps) : 0;
    distanceM = meters;
  } else if (leg.duration_type === 'open'){
    durationS = intensity === 'rest' ? 90 : 60;
    durationInferred = true;
    const mps = feSpeedFromPace(pace);
    distanceM = (mps && mps > 0) ? durationS * mps : 0;
  }

  if (!(durationS > 0)) return null;
  return {
    duration_s: durationS,
    pace_min_per_mi: pace,
    intensity: intensity,
    label: leg.label || intensity,
    duration_inferred: durationInferred,
    distance_m: distanceM,
    source_leg_idx: Number.isFinite(sourceIdx) ? sourceIdx : null,
  };
}

function feBuildExpandedSegments(){
  const rowSegments = [];
  const expanded = [];

  FE_STATE.legs.forEach((leg, idx) => {
    if (!leg) return;
    if (leg.kind === 'repeat'){
      if (idx === 0){
        rowSegments[idx] = [];
        return;
      }
      const start = feClamp(parseInt(leg.repeat_start_index, 10) || 0, 0, idx - 1);
      const totalRepeats = Math.max(1, parseInt(leg.repeat_count, 10) || 1);
      const skipLast = !!leg.skip_last_leg_on_final_repeat;
      const block = [];
      for (let i = start; i < idx; i++){
        const segs = Array.isArray(rowSegments[i]) ? rowSegments[i] : [];
        segs.forEach((s) => block.push(feCopy(s)));
      }
      const produced = [];
      let fullLoopAdds = Math.max(0, totalRepeats - 1);
      if (skipLast) fullLoopAdds = Math.max(0, totalRepeats - 2);
      for (let r = 0; r < fullLoopAdds; r++){
        block.forEach((seg) => {
          produced.push(feCopy(seg));
        });
      }
      if (skipLast && totalRepeats >= 2 && block.length > 0){
        const truncated = block.slice(0, Math.max(0, block.length - 1));
        truncated.forEach((seg) => produced.push(feCopy(seg)));
      }
      rowSegments[idx] = produced;
      expanded.push(...produced);
      return;
    }

    const seg = feLegSegment(leg, idx);
    rowSegments[idx] = seg ? [seg] : [];
    if (seg) expanded.push(seg);
  });

  return expanded;
}

function feUpdateMetrics(segments){
  const legsEl = document.getElementById('fe_metric_legs');
  const timeEl = document.getElementById('fe_metric_time');
  const repsEl = document.getElementById('fe_metric_reps');
  if (legsEl) legsEl.textContent = String(FE_STATE.legs.length);
  if (repsEl) repsEl.textContent = String(FE_STATE.legs.filter((l) => l.kind === 'repeat').reduce((a, l) => a + (parseInt(l.repeat_count, 10) || 0), 0));
  const total = segments.reduce((acc, s) => acc + (Number(s.duration_s) || 0), 0);
  if (timeEl) timeEl.textContent = feFormatTime(total);
}

function feRenderJson(){
  const el = document.getElementById('fe_json');
  if (!el) return;
  const payload = {
    name: FE_STATE.name,
    legs: FE_STATE.legs,
  };
  el.textContent = JSON.stringify(payload, null, 2);
}

function feHideChartTip(){
  const tip = document.getElementById('fe_chart_tip');
  if (!tip) return;
  tip.classList.add('hidden');
  tip.classList.remove('below');
}

function feChartHitAtEvent(evt){
  const canvas = document.getElementById('fe_chart');
  const hits = FE_CHART_STATE.hits || [];
  if (!canvas || !hits.length || !evt) return null;
  const rect = canvas.getBoundingClientRect();
  const x = evt.clientX - rect.left;
  return hits.find((h) => x >= h.x0 && x <= h.x1) || null;
}

function feScrollToSelectedLeg(){
  const selectedIdx = FE_CHART_STATE.selectedLegIdx;
  if (!Number.isFinite(selectedIdx)) return;
  const list = document.getElementById('fe_legs');
  if (!list) return;
  let target = list.querySelector(`.repeat-row[data-sub-step-idx="${selectedIdx}"]`);
  if (!target) target = list.querySelector(`.leg[data-idx="${selectedIdx}"]`);
  if (!target || !target.scrollIntoView) return;
  target.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
}

function feSelectLegFromPreview(stepIdx){
  if (!Number.isFinite(stepIdx) || stepIdx < 0 || stepIdx >= FE_STATE.legs.length) return;
  const leg = FE_STATE.legs[stepIdx];
  if (!leg || leg.kind !== 'step') return;
  FE_CHART_STATE.selectedLegIdx = stepIdx;
  FE_CHART_STATE.pendingScrollToSelected = true;
  feRenderLegs();
}

function feHandleChartHover(evt){
  const canvas = document.getElementById('fe_chart');
  const wrap = canvas ? canvas.closest('.chart-wrap') : null;
  const tip = document.getElementById('fe_chart_tip');
  if (!canvas || !wrap || !tip){
    feHideChartTip();
    return;
  }

  const rect = canvas.getBoundingClientRect();
  const hit = feChartHitAtEvent(evt);
  const x = evt.clientX - rect.left;
  const y = evt.clientY - rect.top;
  if (!hit){
    feHideChartTip();
    return;
  }

  const seg = hit.seg || {};
  const paceText = seg.pace_min_per_mi ? `${feFormatPace(seg.pace_min_per_mi)}/mi` : 'n/a';
  const durationText = feFormatTime(seg.duration_s || 0);
  const distanceText = feFormatDistance(seg.distance_m || 0);
  const inferredText = seg.duration_inferred ? ' (inferred open duration)' : '';
  const label = feEscHtml(seg.label || `Segment ${hit.index + 1}`);
  const typeText = feEscHtml(seg.intensity || 'active');
  tip.innerHTML = `<strong>${label}</strong>Type: ${typeText}<br>Duration: ${durationText}${inferredText}<br>Distance: ${distanceText}<br>Pace: ${paceText}`;
  tip.classList.remove('hidden');
  tip.classList.toggle('below', y < 95);

  const wrapRect = wrap.getBoundingClientRect();
  const tipW = tip.offsetWidth || 220;
  const minCenter = 14 + (tipW / 2);
  const maxCenter = wrapRect.width - 14 - (tipW / 2);
  const clampedX = feClamp(x, minCenter, Math.max(minCenter, maxCenter));
  const clampedY = feClamp(y, 20, Math.max(20, wrapRect.height - 20));
  tip.style.left = `${clampedX}px`;
  tip.style.top = `${clampedY}px`;
}

function feHandleChartClick(evt){
  const hit = feChartHitAtEvent(evt);
  if (!hit || !hit.seg) return;
  const sourceIdx = Number(hit.seg.source_leg_idx);
  if (!Number.isFinite(sourceIdx)) return;
  feSelectLegFromPreview(sourceIdx);
}

function feRenderChart(segments){
  const canvas = document.getElementById('fe_chart');
  const note = document.getElementById('fe_chart_note');
  FE_CHART_STATE.hits = [];
  feHideChartTip();
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  ctx.clearRect(0, 0, rect.width, rect.height);

  if (!segments || !segments.length){
    if (note) note.textContent = 'No timed/distance legs to preview.';
    return;
  }

  const total = segments.reduce((a, s) => a + (Number(s.duration_s) || 0), 0);
  const paces = segments.map((s) => Number(s.pace_min_per_mi)).filter((p) => Number.isFinite(p) && p > 0);
  if (!(total > 0) || !paces.length){
    if (note) note.textContent = 'Preview needs valid durations and paces.';
    return;
  }

  let paceMin = Math.min(...paces);
  let paceMax = Math.max(...paces);
  if (paceMax - paceMin < 0.5){
    paceMax += 0.25;
    paceMin = Math.max(0.1, paceMin - 0.25);
  }

  const styles = getComputedStyle(document.documentElement);
  const lineColor = (styles.getPropertyValue('--line') || '#d97745').trim();
  const fillColor = (styles.getPropertyValue('--fill') || 'rgba(217,119,69,0.2)').trim();
  const gridColor = (styles.getPropertyValue('--grid') || 'rgba(45,74,126,0.18)').trim();
  const textColor = (styles.getPropertyValue('--muted') || '#4a5b4b').trim();

  const pad = { left: 48, right: 12, top: 14, bottom: 28 };
  const w = rect.width - pad.left - pad.right;
  const h = rect.height - pad.top - pad.bottom;

  const xAt = (t) => pad.left + (t / total) * w;
  const yAt = (p) => pad.top + ((p - paceMin) / (paceMax - paceMin)) * h;

  const fmtPace = (p) => feFormatPace(p);
  const fmtTime = (s) => feFormatTime(s);

  const segGeom = [];
  let t = 0;
  segments.forEach((seg, idx) => {
    const dur = Number(seg.duration_s) || 0;
    const pace = Number(seg.pace_min_per_mi) || 0;
    if (!(dur > 0) || !(pace > 0)) return;
    const x0 = xAt(t);
    const x1 = xAt(t + dur);
    const y = yAt(pace);
    segGeom.push({
      index: idx,
      seg: seg,
      start_s: t,
      end_s: t + dur,
      x0: x0,
      x1: x1,
      y: y,
    });
    t += dur;
  });
  if (!segGeom.length){
    if (note) note.textContent = 'Preview needs valid durations and paces.';
    return;
  }

  ctx.font = '12px "Manrope", "Trebuchet MS", sans-serif';
  ctx.fillStyle = textColor;
  ctx.strokeStyle = gridColor;
  ctx.lineWidth = 1;

  for (let i = 0; i <= 4; i++){
    const y = pad.top + (i / 4) * h;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(pad.left + w, y);
    ctx.stroke();
    ctx.fillText(fmtPace(paceMin + ((paceMax - paceMin) * i / 4)), 4, y + 4);
  }

  for (let i = 0; i <= 4; i++){
    const t = total * (i / 4);
    const x = xAt(t);
    ctx.beginPath();
    ctx.moveTo(x, pad.top + h);
    ctx.lineTo(x, pad.top + h + 5);
    ctx.stroke();
    ctx.fillText(fmtTime(t), x - 12, pad.top + h + 18);
  }

  ctx.beginPath();
  segGeom.forEach((g, i) => {
    if (i === 0) ctx.moveTo(g.x0, g.y);
    else ctx.lineTo(g.x0, g.y);
    ctx.lineTo(g.x1, g.y);
  });
  ctx.lineTo(xAt(total), pad.top + h);
  ctx.lineTo(pad.left, pad.top + h);
  ctx.closePath();
  ctx.fillStyle = fillColor;
  ctx.fill();

  ctx.beginPath();
  segGeom.forEach((g, i) => {
    if (i === 0) ctx.moveTo(g.x0, g.y);
    else ctx.lineTo(g.x0, g.y);
    ctx.lineTo(g.x1, g.y);
  });
  ctx.strokeStyle = lineColor;
  ctx.lineWidth = 2;
  ctx.stroke();

  FE_CHART_STATE.hits = segGeom.map((g) => ({
    index: g.index,
    seg: g.seg,
    x0: Math.min(g.x0, g.x1),
    x1: Math.max(g.x0, g.x1),
    start_s: g.start_s,
    end_s: g.end_s,
  }));

  if (note){
    const inferredCount = segments.filter((s) => s && s.duration_inferred).length;
    const inferredText = inferredCount ? ` • ${inferredCount} inferred open-leg duration${inferredCount === 1 ? '' : 's'}` : '';
    note.textContent = `${segments.length} preview segments • ${feFormatTime(total)} total${inferredText} • hover for details • click to jump to leg`;
  }
}

function feRenderLegs(){
  feNormalizeState();
  if (Number.isFinite(FE_CHART_STATE.selectedLegIdx)){
    const selIdx = FE_CHART_STATE.selectedLegIdx;
    const selLeg = FE_STATE.legs[selIdx];
    if (!selLeg || selLeg.kind !== 'step') FE_CHART_STATE.selectedLegIdx = null;
  }
  const list = document.getElementById('fe_legs');
  if (!list) return;

  if (!FE_STATE.legs.length){
    list.innerHTML = '<div class="status">No legs yet. Add a step or load a FIT file.</div>';
    feRenderJson();
    feRenderChart([]);
    feUpdateMetrics([]);
    return;
  }

  const view = feGetVisibleComponents();
  const html = [];
  view.components.forEach((comp) => {
    html.push(`<div class="leg-drop-slot" data-drop-idx="${comp.startIdx}"></div>`);

    if (comp.kind === 'repeat'){
      const repeatIdx = comp.controlIdx;
      const repeatLeg = FE_STATE.legs[repeatIdx];
      const rowIndices = comp.bundleIndices || [];
      const rowsHtml = rowIndices.length
        ? (() => {
            const bits = [];
            rowIndices.forEach((stepIdx, rowNum) => {
              const item = FE_STATE.legs[stepIdx];
              if (!item || item.kind !== 'step') return;
              const paceCenter = feLegCenterPace(item);
              const paceCenterText = Number.isFinite(paceCenter) ? feFormatPace(Number(paceCenter)) : '';
              const selectedClass = FE_CHART_STATE.selectedLegIdx === stepIdx ? ' selected' : '';
              bits.push(`<div class="repeat-sub-drop-slot" data-repeat-idx="${repeatIdx}" data-sub-drop-idx="${stepIdx}"></div>`);
              bits.push(`
                <div class="repeat-row${selectedClass}" draggable="true" data-sub-step-idx="${stepIdx}" data-repeat-idx="${repeatIdx}">
                  <div class="repeat-row-head">
                    <span class="repeat-row-handle" draggable="true" data-sub-step-idx="${stepIdx}" data-repeat-idx="${repeatIdx}" title="Drag row to reorder">≡</span>
                    <span class="repeat-row-title">Block Row ${rowNum + 1}</span>
                  </div>
                  <div class="row-3">
                    <label>Label
                      <input type="text" data-sub-idx="${stepIdx}" data-field="label" value="${feEscHtml(item.label || '')}">
                    </label>
                    <label>Intensity
                      <select data-sub-idx="${stepIdx}" data-field="intensity">
                        <option value="active" ${item.intensity === 'active' ? 'selected' : ''}>active</option>
                        <option value="rest" ${item.intensity === 'rest' ? 'selected' : ''}>rest</option>
                        <option value="warmup" ${item.intensity === 'warmup' ? 'selected' : ''}>warmup</option>
                        <option value="cooldown" ${item.intensity === 'cooldown' ? 'selected' : ''}>cooldown</option>
                      </select>
                    </label>
                    <label>Duration
                      <input type="number" min="0" step="0.1" data-sub-idx="${stepIdx}" data-field="duration_value" value="${item.duration_value === null || item.duration_value === undefined ? '' : item.duration_value}">
                    </label>
                  </div>
                  <div class="row-3">
                    <label>Duration type
                      <select data-sub-idx="${stepIdx}" data-field="duration_type">
                        <option value="time" ${item.duration_type === 'time' ? 'selected' : ''}>time</option>
                        <option value="distance" ${item.duration_type === 'distance' ? 'selected' : ''}>distance</option>
                        <option value="open" ${item.duration_type === 'open' ? 'selected' : ''}>open</option>
                      </select>
                    </label>
                    <label>Pace center (min/mi)
                      <input type="text" data-sub-idx="${stepIdx}" data-field="pace_center_text" placeholder="9:00" value="${paceCenterText}">
                    </label>
                    <label>Auto pace range
                      <input type="text" value="±0:30 /mi" disabled>
                    </label>
                  </div>
                  <div class="inline">
                    <label class="inline"><input type="checkbox" data-sub-idx="${stepIdx}" data-field="target_type_toggle" ${item.target_type === 'speed' ? 'checked' : ''}> pace target</label>
                    <button class="btn-soft" type="button" data-act="repeat_remove_sub" data-sub-idx="${stepIdx}">Remove Row</button>
                  </div>
                </div>
              `);
            });
            bits.push(`<div class="repeat-sub-drop-slot" data-repeat-idx="${repeatIdx}" data-sub-drop-idx="${repeatIdx}"></div>`);
            return bits.join('');
          })()
        : '<div class="repeat-chip repeat-chip-empty">No bundled rows.</div>';

      html.push(`
        <article class="leg" draggable="true" data-idx="${repeatIdx}" data-start-idx="${comp.startIdx}">
          <div class="leg-head">
            <div class="leg-head-left"><span class="drag-handle" draggable="true" data-drag-idx="${repeatIdx}" title="Drag to reorder">≡</span><span class="leg-badge">Repeat Block</span></div>
            <div class="leg-tools">
              <button class="btn-soft" data-act="repeat_add_active">+ Active Row</button>
              <button class="btn-soft" data-act="repeat_add_rest">+ Rest Row</button>
              <button class="btn-soft" data-act="dup">Dup</button>
              <button class="btn-soft" data-act="del">Delete</button>
            </div>
          </div>
          <div class="repeat-bundle">
            <div class="repeat-bundle-head">Rows included in this repeat block</div>
            <div class="repeat-chip-row">${rowsHtml}</div>
            <div class="repeat-drop-target" data-repeat-idx="${repeatIdx}">Drop a step leg here to include it in this repeat block</div>
          </div>
          <div class="row">
            <label>Repeat count (total)
              <input type="number" min="1" step="1" data-field="repeat_count" value="${Math.max(1, parseInt(repeatLeg.repeat_count, 10) || 1)}">
            </label>
          </div>
          <label class="inline">
            <input type="checkbox" data-field="skip_last_leg_on_final_repeat" ${repeatLeg.skip_last_leg_on_final_repeat ? 'checked' : ''}>
            Skip last bundled leg on final repeat
          </label>
        </article>
      `);
      return;
    }

    const idx = comp.controlIdx;
    const leg = FE_STATE.legs[idx];
    const durationPlaceholder = leg.duration_type === 'distance' ? 'meters' : (leg.duration_type === 'time' ? 'seconds' : 'n/a');
    const paceCenter = feLegCenterPace(leg);
    const paceCenterText = Number.isFinite(paceCenter) ? feFormatPace(Number(paceCenter)) : '';
    const selectedClass = FE_CHART_STATE.selectedLegIdx === idx ? ' selected' : '';

    html.push(`
      <article class="leg${selectedClass}" draggable="true" data-idx="${idx}" data-start-idx="${comp.startIdx}">
        <div class="leg-head">
          <div class="leg-head-left"><span class="drag-handle" draggable="true" data-drag-idx="${idx}" title="Drag to reorder">≡</span><span class="leg-badge">Leg ${idx + 1} • step</span></div>
          <div class="leg-tools">
            <button class="btn-soft" data-act="dup">Dup</button>
            <button class="btn-soft" data-act="del">Delete</button>
          </div>
        </div>

        <label>Label
          <input type="text" data-field="label" value="${feEscHtml(leg.label || '')}">
        </label>

        <div class="row-3">
          <label>Intensity
            <select data-field="intensity">
              <option value="active" ${leg.intensity === 'active' ? 'selected' : ''}>active</option>
              <option value="rest" ${leg.intensity === 'rest' ? 'selected' : ''}>rest</option>
              <option value="warmup" ${leg.intensity === 'warmup' ? 'selected' : ''}>warmup</option>
              <option value="cooldown" ${leg.intensity === 'cooldown' ? 'selected' : ''}>cooldown</option>
            </select>
          </label>
          <label>Duration type
            <select data-field="duration_type">
              <option value="time" ${leg.duration_type === 'time' ? 'selected' : ''}>time</option>
              <option value="distance" ${leg.duration_type === 'distance' ? 'selected' : ''}>distance</option>
              <option value="open" ${leg.duration_type === 'open' ? 'selected' : ''}>open</option>
            </select>
          </label>
          <label>Duration value (${durationPlaceholder})
            <input type="number" min="0" step="0.1" data-field="duration_value" value="${leg.duration_value === null || leg.duration_value === undefined ? '' : leg.duration_value}">
          </label>
        </div>

        <div class="row-3">
          <label>Target type
            <select data-field="target_type">
              <option value="open" ${leg.target_type === 'open' ? 'selected' : ''}>open</option>
              <option value="speed" ${leg.target_type === 'speed' ? 'selected' : ''}>pace range</option>
            </select>
          </label>
          <label>Pace center (min/mi)
            <input type="text" data-field="pace_center_text" placeholder="9:00" value="${paceCenterText}">
          </label>
          <label>Auto pace range
            <input type="text" value="±0:30 /mi" disabled>
          </label>
        </div>
      </article>
    `);
  });
  html.push(`<div class="leg-drop-slot" data-drop-idx="${FE_STATE.legs.length}"></div>`);
  list.innerHTML = html.join('');

  const segments = feBuildExpandedSegments();
  if (FE_CHART_STATE.pendingScrollToSelected){
    FE_CHART_STATE.pendingScrollToSelected = false;
    window.requestAnimationFrame(feScrollToSelectedLeg);
  }
  feRenderJson();
  feRenderChart(segments);
  feUpdateMetrics(segments);
}

function feUpdateNameFromInput(){
  const nameInput = document.getElementById('fe_name');
  if (!nameInput) return;
  FE_STATE.name = String(nameInput.value || 'Workout');
  feRenderJson();
}

function feHandleLegInput(event){
  const input = event.target;
  const subRaw = input && input.getAttribute ? input.getAttribute('data-sub-idx') : null;
  let targetIdx = null;
  if (subRaw !== null && subRaw !== ''){
    const subIdx = parseInt(subRaw, 10);
    if (Number.isFinite(subIdx) && subIdx >= 0 && subIdx < FE_STATE.legs.length) targetIdx = subIdx;
  }
  const card = input && input.closest ? input.closest('.leg') : null;
  if (targetIdx === null){
    if (!card) return;
    const idx = parseInt(card.getAttribute('data-idx') || '-1', 10);
    if (!Number.isFinite(idx) || idx < 0 || idx >= FE_STATE.legs.length) return;
    targetIdx = idx;
  }

  const leg = FE_STATE.legs[targetIdx];
  const field = input.getAttribute('data-field') || '';
  if (!field) return;

  if (field === 'repeat_count'){
    const raw = parseInt(input.value || '1', 10);
    leg.repeat_count = Number.isFinite(raw) ? Math.max(1, raw) : 1;
  } else if (field === 'skip_last_leg_on_final_repeat'){
    leg.skip_last_leg_on_final_repeat = !!input.checked;
  } else if (field === 'target_type_toggle'){
    leg.target_type = input.checked ? 'speed' : 'open';
  } else if (field === 'duration_value'){
    if (leg.duration_type === 'open'){
      leg.duration_value = null;
    } else {
      const raw = parseFloat(input.value || '0');
      leg.duration_value = Number.isFinite(raw) ? Math.max(0, raw) : 0;
    }
  } else if (field === 'pace_center_text'){
    const center = feParsePace(input.value);
    if (center){
      const slow = Math.max(0.1, center + FE_PACE_RANGE_HALF_MIN);
      const fast = Math.max(0.1, center - FE_PACE_RANGE_HALF_MIN);
      leg.pace_slow_min_per_mi = slow;
      leg.pace_fast_min_per_mi = fast;
      leg.speed_low_mps = feSpeedFromPace(slow);
      leg.speed_high_mps = feSpeedFromPace(fast);
    } else {
      leg.pace_slow_min_per_mi = null;
      leg.pace_fast_min_per_mi = null;
      leg.speed_low_mps = null;
      leg.speed_high_mps = null;
    }
  } else if (field === 'pace_slow_text'){
    const pace = feParsePace(input.value);
    leg.pace_slow_min_per_mi = pace;
    leg.speed_low_mps = pace ? feSpeedFromPace(pace) : null;
  } else if (field === 'pace_fast_text'){
    const pace = feParsePace(input.value);
    leg.pace_fast_min_per_mi = pace;
    leg.speed_high_mps = pace ? feSpeedFromPace(pace) : null;
  } else {
    const value = input.value;
    leg[field] = value;
    if (field === 'duration_type' && value === 'open') leg.duration_value = null;
  }

  FE_STATE.legs[targetIdx] = feNormalizeLeg(leg);
  feRenderLegs();
}

function feHandleLegActions(event){
  const btn = event.target;
  if (!btn || btn.tagName !== 'BUTTON') return;
  const act = btn.getAttribute('data-act');
  if (!act) return;

  const card = btn.closest('.leg');
  if (!card) return;
  const idx = parseInt(card.getAttribute('data-idx') || '-1', 10);
  if (!Number.isFinite(idx) || idx < 0 || idx >= FE_STATE.legs.length) return;

  const leg = FE_STATE.legs[idx];
  const bundleInfo = feComputeRepeatBundleInfo();
  const bundleRows = (bundleInfo.repeatBundles[idx] || []).slice();
  const blockStart = bundleRows.length ? Math.min(...bundleRows) : feClamp(parseInt((leg || {}).repeat_start_index, 10) || Math.max(0, idx - 1), 0, Math.max(0, idx - 1));

  if (act === 'repeat_add_active' || act === 'repeat_add_rest'){
    if (!leg || leg.kind !== 'repeat') return;
    const newRow = feNormalizeLeg({
      kind: 'step',
      label: act === 'repeat_add_rest' ? 'Recovery' : 'Work',
      intensity: act === 'repeat_add_rest' ? 'rest' : 'active',
      duration_type: 'time',
      duration_value: act === 'repeat_add_rest' ? 90 : 120,
      target_type: act === 'repeat_add_rest' ? 'open' : 'speed',
      pace_slow_min_per_mi: act === 'repeat_add_rest' ? null : 7.0,
      pace_fast_min_per_mi: act === 'repeat_add_rest' ? null : 6.6,
    });
    FE_STATE.legs.splice(idx, 0, newRow);
    feRenderLegs();
    return;
  }

  if (act === 'repeat_remove_sub'){
    if (!leg || leg.kind !== 'repeat') return;
    const subIdx = parseInt(btn.getAttribute('data-sub-idx') || '-1', 10);
    if (!Number.isFinite(subIdx) || subIdx < 0 || subIdx >= FE_STATE.legs.length) return;
    if (bundleRows.length <= 1){
      feSetStatus('Repeat block must keep at least one row.');
      return;
    }
    FE_STATE.legs.splice(subIdx, 1);
    feRenderLegs();
    return;
  }

  if (act === 'del'){
    if (leg && leg.kind === 'repeat'){
      FE_STATE.legs.splice(blockStart, idx - blockStart + 1);
    } else {
      FE_STATE.legs.splice(idx, 1);
    }
  } else if (act === 'dup'){
    if (leg && leg.kind === 'repeat'){
      const seg = FE_STATE.legs.slice(blockStart, idx + 1).map((x) => feCopy(x));
      FE_STATE.legs.splice(idx + 1, 0, ...seg);
      const newControlIdx = idx + 1 + seg.length - 1;
      const ctrl = FE_STATE.legs[newControlIdx];
      if (ctrl && ctrl.kind === 'repeat'){
        const oldStart = parseInt(ctrl.repeat_start_index, 10);
        const rel = Number.isFinite(oldStart) ? feClamp(oldStart - blockStart, 0, seg.length - 1) : 0;
        ctrl.repeat_start_index = (idx + 1) + rel;
      }
    } else {
      FE_STATE.legs.splice(idx + 1, 0, feCopy(FE_STATE.legs[idx]));
    }
  }

  feRenderLegs();
}

function feClearDragUi(){
  const list = document.getElementById('fe_legs');
  if (!list) return;
  list.querySelectorAll('.leg-drop-slot.active').forEach((el) => el.classList.remove('active'));
  list.querySelectorAll('.repeat-drop-target.active').forEach((el) => el.classList.remove('active'));
  list.querySelectorAll('.repeat-sub-drop-slot.active').forEach((el) => el.classList.remove('active'));
  list.querySelectorAll('.leg.dragging').forEach((el) => el.classList.remove('dragging'));
  list.querySelectorAll('.repeat-row.dragging').forEach((el) => el.classList.remove('dragging'));
}

function feHandleDragStart(event){
  const subHandle = event.target && event.target.closest ? event.target.closest('.repeat-row-handle') : null;
  const subRow = event.target && event.target.closest ? event.target.closest('.repeat-row') : null;
  const subDragSource = subHandle || (subRow && !feIsInteractiveTarget(event.target) ? subRow : null);
  if (subDragSource){
    const stepIdx = parseInt(subDragSource.getAttribute('data-sub-step-idx') || '-1', 10);
    const repeatIdx = parseInt(subDragSource.getAttribute('data-repeat-idx') || '-1', 10);
    if (!Number.isFinite(stepIdx) || stepIdx < 0 || stepIdx >= FE_STATE.legs.length) return;
    if (!Number.isFinite(repeatIdx) || repeatIdx < 0 || repeatIdx >= FE_STATE.legs.length) return;
    FE_DND_STATE.dragIndex = null;
    FE_DND_STATE.subDrag = { stepIdx, repeatIdx };
    const row = subDragSource.closest('.repeat-row');
    if (row) row.classList.add('dragging');
    if (event.dataTransfer){
      event.dataTransfer.effectAllowed = 'move';
      try { event.dataTransfer.setData('text/plain', `sub:${stepIdx}:${repeatIdx}`); } catch (e) {}
    }
    feAutoScrollOnDrag(event);
    feStartAutoScrollLoop();
    return;
  }

  const card = event.target && event.target.closest ? event.target.closest('.leg') : null;
  if (subHandle){
    // handled above via subDragSource
    return;
  }

  const handle = event.target && event.target.closest ? event.target.closest('.drag-handle') : null;
  const dragSource = handle || (card && !feIsInteractiveTarget(event.target) ? card : null);
  if (!dragSource) return;
  const idx = parseInt(dragSource.getAttribute('data-drag-idx') || dragSource.getAttribute('data-idx') || '-1', 10);
  if (!Number.isFinite(idx) || idx < 0 || idx >= FE_STATE.legs.length) return;
  FE_DND_STATE.subDrag = null;
  FE_DND_STATE.dragIndex = idx;
  const dragCard = dragSource.closest('.leg');
  if (dragCard) dragCard.classList.add('dragging');
  if (event.dataTransfer){
    event.dataTransfer.effectAllowed = 'move';
    try { event.dataTransfer.setData('text/plain', String(idx)); } catch (e) {}
  }
  feAutoScrollOnDrag(event);
  feStartAutoScrollLoop();
}

function feGetDragIndex(event){
  if (Number.isFinite(FE_DND_STATE.dragIndex)) return FE_DND_STATE.dragIndex;
  try {
    if (event && event.dataTransfer){
      const raw = event.dataTransfer.getData('text/plain');
      const idx = parseInt(raw || '-1', 10);
      if (Number.isFinite(idx) && idx >= 0 && idx < FE_STATE.legs.length) return idx;
    }
  } catch (e) {}
  return null;
}

function feGetSubDrag(event){
  if (FE_DND_STATE.subDrag && Number.isFinite(FE_DND_STATE.subDrag.stepIdx)) return FE_DND_STATE.subDrag;
  try {
    if (event && event.dataTransfer){
      const raw = String(event.dataTransfer.getData('text/plain') || '');
      if (raw.startsWith('sub:')){
        const parts = raw.split(':');
        const stepIdx = parseInt(parts[1] || '-1', 10);
        const repeatIdx = parseInt(parts[2] || '-1', 10);
        if (Number.isFinite(stepIdx) && stepIdx >= 0 && stepIdx < FE_STATE.legs.length && Number.isFinite(repeatIdx) && repeatIdx >= 0 && repeatIdx < FE_STATE.legs.length){
          return { stepIdx, repeatIdx };
        }
      }
    }
  } catch (e) {}
  return null;
}

function feElementAtPointer(event){
  if (!event) return null;
  const x = Number(event.clientX);
  const y = Number(event.clientY);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  try {
    const el = document.elementFromPoint(x, y);
    return (el && typeof el.closest === 'function') ? el : null;
  } catch (e) {
    return null;
  }
}

function feClosestForDrop(event, selector){
  const fromTarget = event && event.target && event.target.closest ? event.target.closest(selector) : null;
  if (fromTarget) return fromTarget;
  const fromPoint = feElementAtPointer(event);
  if (fromPoint && fromPoint.closest) return fromPoint.closest(selector);
  return null;
}

function feResolveDropTarget(event){
  const rpt = feClosestForDrop(event, '.repeat-drop-target');
  if (rpt){
    return { kind: 'repeat', repeatIdx: parseInt(rpt.getAttribute('data-repeat-idx') || '-1', 10), el: rpt };
  }
  const slot = feClosestForDrop(event, '.leg-drop-slot');
  if (slot){
    return { kind: 'slot', dropIdx: parseInt(slot.getAttribute('data-drop-idx') || '-1', 10), el: slot };
  }
  const card = feClosestForDrop(event, '.leg');
  if (card){
    const idx = parseInt(card.getAttribute('data-idx') || '-1', 10);
    const startIdx = parseInt(card.getAttribute('data-start-idx') || String(idx), 10);
    if (Number.isFinite(idx) && idx >= 0){
      const leg = FE_STATE.legs[idx];
      if (leg && leg.kind === 'repeat'){
        return { kind: 'repeat_card', repeatIdx: idx, el: card };
      }
      const rect = card.getBoundingClientRect();
      const before = event.clientY < (rect.top + rect.height / 2);
      return { kind: 'slot', dropIdx: before ? (Number.isFinite(startIdx) ? startIdx : idx) : idx + 1, el: card };
    }
  }
  const list = document.getElementById('fe_legs');
  const pointerEl = feElementAtPointer(event);
  if (list && ((event.target && list.contains(event.target)) || (pointerEl && list.contains(pointerEl)))){
    const slots = Array.from(list.querySelectorAll('.leg-drop-slot[data-drop-idx]'));
    if (slots.length){
      let best = null;
      let bestDist = Number.POSITIVE_INFINITY;
      slots.forEach((el) => {
        const r = el.getBoundingClientRect();
        const cy = r.top + (r.height / 2);
        const d = Math.abs(event.clientY - cy);
        if (d < bestDist){
          bestDist = d;
          best = el;
        }
      });
      if (best){
        return { kind: 'slot', dropIdx: parseInt(best.getAttribute('data-drop-idx') || '-1', 10), el: best };
      }
    }
  }
  return null;
}

function feResolveSubDropTarget(event, repeatIdx){
  const slot = feClosestForDrop(event, '.repeat-sub-drop-slot');
  if (slot){
    const rpt = parseInt(slot.getAttribute('data-repeat-idx') || '-1', 10);
    if (rpt === repeatIdx){
      return {
        repeatIdx: rpt,
        dropIdx: parseInt(slot.getAttribute('data-sub-drop-idx') || '-1', 10),
        el: slot,
      };
    }
  }

  const row = feClosestForDrop(event, '.repeat-row');
  if (row){
    const rpt = parseInt(row.getAttribute('data-repeat-idx') || '-1', 10);
    const stepIdx = parseInt(row.getAttribute('data-sub-step-idx') || '-1', 10);
    if (rpt === repeatIdx && Number.isFinite(stepIdx) && stepIdx >= 0){
      const rect = row.getBoundingClientRect();
      const before = event.clientY < (rect.top + rect.height / 2);
      return {
        repeatIdx: rpt,
        dropIdx: before ? stepIdx : (stepIdx + 1),
        el: row,
      };
    }
  }

  const card = feClosestForDrop(event, '.leg');
  if (card){
    const cardIdx = parseInt(card.getAttribute('data-idx') || '-1', 10);
    if (cardIdx === repeatIdx){
      return { repeatIdx, dropIdx: repeatIdx, el: card };
    }
  }
  return null;
}

function feHandleDragOver(event){
  if (feHasActiveDrag()) feAutoScrollOnDrag(event);

  const subDrag = feGetSubDrag(event);
  if (subDrag){
    const subTarget = feResolveSubDropTarget(event, subDrag.repeatIdx);
    if (!subTarget) return;
    event.preventDefault();
    feClearDragUi();
    const list = document.getElementById('fe_legs');
    if (!list) return;
    const exactSlot = list.querySelector(`.repeat-sub-drop-slot[data-repeat-idx="${subDrag.repeatIdx}"][data-sub-drop-idx="${subTarget.dropIdx}"]`);
    if (exactSlot) exactSlot.classList.add('active');
    else if (subTarget.el) subTarget.el.classList.add('active');
    return;
  }

  const src = feGetDragIndex(event);
  if (!Number.isFinite(src)) return;
  const target = feResolveDropTarget(event);
  if (!target) return;
  event.preventDefault();
  feClearDragUi();
  const srcLeg = FE_STATE.legs[src];
  if ((target.kind === 'repeat' || target.kind === 'repeat_card') && target.el) {
    if (srcLeg && srcLeg.kind === 'step'){
      const card = target.kind === 'repeat_card' ? target.el : (target.el.closest('.leg'));
      const repeatDrop = card ? card.querySelector('.repeat-drop-target') : null;
      if (repeatDrop) repeatDrop.classList.add('active');
      else target.el.classList.add('active');
    } else {
      target.el.classList.add('active');
    }
    return;
  }
  const list = document.getElementById('fe_legs');
  if (!list) return;
  const slotEl = list.querySelector(`.leg-drop-slot[data-drop-idx="${target.dropIdx}"]`);
  if (slotEl) slotEl.classList.add('active');
}

function feHandleDrop(event){
  feStopAutoScrollLoop();
  const subDrag = feGetSubDrag(event);
  if (subDrag){
    const subTarget = feResolveSubDropTarget(event, subDrag.repeatIdx);
    if (!subTarget) return;
    const repeatIdx = subTarget.repeatIdx;
    const dropIdx = subTarget.dropIdx;
    if (!Number.isFinite(repeatIdx) || !Number.isFinite(dropIdx) || repeatIdx !== subDrag.repeatIdx) return;
    event.preventDefault();
    FE_DND_STATE.subDrag = null;
    FE_DND_STATE.dragIndex = null;
    feClearDragUi();
    const moved = feReorderLegs(subDrag.stepIdx, dropIdx);
    if (moved){
      feSetStatus(`Reordered row inside repeat block.`);
      feRenderLegs();
    } else {
      feSetStatus('Row drop landed in same position.');
    }
    return;
  }

  const src = feGetDragIndex(event);
  if (!Number.isFinite(src)) return;
  const target = feResolveDropTarget(event);
  if (!target) return;
  event.preventDefault();
  FE_DND_STATE.dragIndex = null;
  feClearDragUi();

  const srcLeg = FE_STATE.legs[src];
  if (target.kind === 'repeat' || target.kind === 'repeat_card'){
    if (srcLeg && srcLeg.kind === 'step'){
      feIncludeStepInRepeat(src, target.repeatIdx);
      return;
    }
    if (target.kind === 'repeat'){
      return;
    }
  }

  const moved = feReorderLegs(src, target.dropIdx);
  if (moved){
    feSetStatus(`Moved leg ${src + 1} to position ${moved.movedNew + 1}.`);
    feRenderLegs();
  } else {
    feSetStatus('Drop landed in the same position, so no reorder was needed.');
  }
}

function feHandleDragEnd(){
  feStopAutoScrollLoop();
  FE_DND_STATE.dragIndex = null;
  FE_DND_STATE.subDrag = null;
  feClearDragUi();
}

function feNewBlank(){
  FE_STATE.name = 'Workout';
  FE_STATE.legs = [];
  FE_CHART_STATE.selectedLegIdx = null;
  FE_CHART_STATE.pendingScrollToSelected = false;
  feSetNameInput();
  feRenderLegs();
  feSetStatus('Blank workout ready. Add your first leg.');
}

function feLoadTemplate(){
  FE_STATE.name = 'Intervals 5x2min';
  FE_STATE.legs = [
    {
      kind: 'step',
      label: 'Warmup',
      intensity: 'warmup',
      duration_type: 'time',
      duration_value: 900,
      target_type: 'open',
    },
    {
      kind: 'step',
      label: 'Work rep',
      intensity: 'active',
      duration_type: 'time',
      duration_value: 120,
      target_type: 'speed',
      pace_slow_min_per_mi: 6.5,
      pace_fast_min_per_mi: 6.1,
    },
    {
      kind: 'step',
      label: 'Jog recovery',
      intensity: 'rest',
      duration_type: 'time',
      duration_value: 180,
      target_type: 'open',
    },
    {
      kind: 'repeat',
      label: 'Repeat block',
      repeat_start_index: 1,
      repeat_count: 5,
      block_len: 2,
    },
    {
      kind: 'step',
      label: 'Cooldown',
      intensity: 'cooldown',
      duration_type: 'time',
      duration_value: 600,
      target_type: 'open',
    },
  ].map(feNormalizeLeg);
  FE_CHART_STATE.selectedLegIdx = null;
  FE_CHART_STATE.pendingScrollToSelected = false;
  feSetNameInput();
  feRenderLegs();
  feSetStatus('Loaded interval template. Edit any leg, then save it for review.');
}

function feAddLeg(kind){
  const k = (kind || 'step').toLowerCase();
  if (k === 'repeat'){
    let start = Math.max(0, FE_STATE.legs.length - 1);
    if (FE_STATE.legs.length >= 2){
      const a = FE_STATE.legs[FE_STATE.legs.length - 1];
      const b = FE_STATE.legs[FE_STATE.legs.length - 2];
      if (a && b && a.kind === 'step' && b.kind === 'step'){
        start = FE_STATE.legs.length - 2;
      }
    }
    FE_STATE.legs.push(feDefaultRepeat(start));
  }
  else FE_STATE.legs.push(feDefaultStep());
  feRenderLegs();
  feSetStatus(k === 'repeat'
    ? 'Added a repeat block. Choose the rows it should repeat.'
    : `Added leg ${FE_STATE.legs.length}. Edit its duration, intensity, and target.`);
}

async function feParseUploaded(){
  const fileInput = document.getElementById('fit_editor_file');
  if (!fileInput || !fileInput.files || !fileInput.files.length){
    feSetStatus('Choose a .fit file first.');
    return;
  }
  return feParseFileObject(fileInput.files[0]);
}

async function feParseFileObject(fileObj){
  if (!fileObj){
    feSetStatus('Choose a .fit file first.');
    return;
  }
  const fd = new FormData();
  fd.append('files', fileObj, fileObj.name || 'workout.fit');

  feSetStatus('Parsing FIT...');
  try {
    const res = await fetch('/api/fit-editor/parse', { method: 'POST', body: fd });
    const js = await res.json();
    if (!res.ok){
      feSetStatus('Error: ' + JSON.stringify(js));
      return;
    }
    const item = Array.isArray(js.results) && js.results.length ? js.results[0] : null;
    if (!item){
      feSetStatus('No parse results returned.');
      return;
    }
    if (item.error){
      feSetStatus('Parse error: ' + item.error);
      return;
    }

    FE_STATE.name = item.workout_name || (item.name ? String(item.name).replace(/\.fit$/i, '') : 'Workout');
    FE_STATE.legs = Array.isArray(item.legs) ? item.legs.map(feNormalizeLeg) : [];
    FE_CHART_STATE.selectedLegIdx = null;
    FE_CHART_STATE.pendingScrollToSelected = false;
    feSetNameInput();
    feRenderLegs();
    feSetStatus(`Loaded ${item.name || 'FIT'} with ${FE_STATE.legs.length} legs. Review it or make changes, then save it for approval.`);
  } catch (err) {
    feSetStatus('Error: ' + (err && err.message ? err.message : String(err)));
  }
}

function feBase64ToBytes(b64){
  const bin = atob(String(b64 || ''));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function feTryOpenTransferredFit(){
  let token = '';
  try {
    const u = new URL(window.location.href);
    token = u.searchParams.get('open_blob') || '';
  } catch (e) {}
  if (!token) return;
  const key = FE_OPEN_FIT_KEY_PREFIX + token;
  let raw = null;
  try { raw = sessionStorage.getItem(key); } catch (e) {}
  if (!raw) return;
  try {
    sessionStorage.removeItem(key);
  } catch (e) {}
  try {
    const payload = JSON.parse(raw);
    const bytes = feBase64ToBytes(payload.b64 || '');
    const blob = new Blob([bytes], { type: payload.mime || 'application/octet-stream' });
    const file = new File([blob], payload.filename || 'workout.fit', { type: payload.mime || 'application/octet-stream' });
    await feParseFileObject(file);
  } catch (e) {
    feSetStatus('Could not auto-open transferred FIT. You can still load it manually.');
  }
}

async function feExport(){
  feNormalizeState();
  const nameInput = document.getElementById('fe_name');
  const deterministic = !!((document.getElementById('fe_deterministic') || {}).checked);
  if (nameInput) FE_STATE.name = String(nameInput.value || 'Workout');

  if (!FE_STATE.legs.length){
    feSetStatus('Add at least one leg before saving for review.');
    return;
  }

  feSetStatus('Building FIT...');
  try {
    const res = await fetch('/api/fit-editor/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: FE_STATE.name,
        deterministic: deterministic,
        legs: FE_STATE.legs,
      }),
    });
    if (!res.ok){
      feSetStatus('Error: ' + (await res.text()));
      return;
    }

    let filename = 'workout.fit';
    try {
      const disp = res.headers.get('Content-Disposition') || '';
      const part = disp.split('filename=')[1];
      if (part) filename = part.replace(/"/g, '');
    } catch (e) {}

    const blob = await res.blob();
    if (typeof window.promptFitStageForReview !== 'function'){
      feSetStatus('The review queue is unavailable. Refresh the page and try again.');
      return;
    }
    await window.promptFitStageForReview(blob, filename, {
      source: 'editor',
      scrollToReview: true,
      title: FE_STATE.name,
    });
    feSetStatus(`Saved ${filename} for review. Nothing was downloaded.`);
  } catch (err) {
    feSetStatus('Error: ' + (err && err.message ? err.message : String(err)));
  }
}

function feBindDropzone(){
  const zone = document.getElementById('fe_drop');
  const input = document.getElementById('fit_editor_file');
  if (!zone || !input) return;

  const add = () => zone.classList.add('dragover');
  const rem = () => zone.classList.remove('dragover');

  zone.addEventListener('dragover', (e) => {
    e.preventDefault();
    add();
  });
  zone.addEventListener('dragleave', (e) => {
    e.preventDefault();
    rem();
  });
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    rem();
    const files = e.dataTransfer && e.dataTransfer.files ? e.dataTransfer.files : null;
    if (files && files.length){
      try {
        const dt = new DataTransfer();
        dt.items.add(files[0]);
        input.files = dt.files;
      } catch (err) {}
      feParseUploaded();
    }
  });
}

function feInit(){
  const list = document.getElementById('fe_legs');
  const nameInput = document.getElementById('fe_name');
  const chart = document.getElementById('fe_chart');

  const parseBtn = document.getElementById('fe_parse_btn');
  const blankBtn = document.getElementById('fe_blank_btn');
  const addStepBtn = document.getElementById('fe_add_step_btn');
  const addRepeatBtn = document.getElementById('fe_add_repeat_btn');
  const templateWuCdBtn = document.getElementById('fe_tpl_wu_cd_btn');
  const templateWorkBtn = document.getElementById('fe_tpl_work_btn');
  const templateRecoveryBtn = document.getElementById('fe_tpl_recovery_btn');
  const templateRestBtn = document.getElementById('fe_tpl_rest_btn');
  const templateBtn = document.getElementById('fe_template_btn');
  const exportBtn = document.getElementById('fe_export_btn');

  if (parseBtn) parseBtn.addEventListener('click', feParseUploaded);
  if (blankBtn) blankBtn.addEventListener('click', feNewBlank);
  if (addStepBtn) addStepBtn.addEventListener('click', () => feAddLeg('step'));
  if (addRepeatBtn) addRepeatBtn.addEventListener('click', () => feAddLeg('repeat'));
  if (templateWuCdBtn) templateWuCdBtn.addEventListener('click', () => feAddTemplateLeg('wu_cd'));
  if (templateWorkBtn) templateWorkBtn.addEventListener('click', () => feAddTemplateLeg('work_rep'));
  if (templateRecoveryBtn) templateRecoveryBtn.addEventListener('click', () => feAddTemplateLeg('recovery_jog'));
  if (templateRestBtn) templateRestBtn.addEventListener('click', () => feAddTemplateLeg('rest_2min'));
  if (templateBtn) templateBtn.addEventListener('click', feLoadTemplate);
  if (exportBtn) exportBtn.addEventListener('click', feExport);

  if (nameInput){
    nameInput.addEventListener('input', feUpdateNameFromInput);
  }

  if (list){
    list.addEventListener('change', feHandleLegInput);
    list.addEventListener('click', feHandleLegActions);
    list.addEventListener('dragstart', feHandleDragStart);
    list.addEventListener('dragover', feHandleDragOver);
    list.addEventListener('drop', feHandleDrop);
    list.addEventListener('dragend', feHandleDragEnd);
    list.addEventListener('dragleave', (e) => {
      const next = e.relatedTarget;
      if (!next || (list.contains(next) === false)) feClearDragUi();
    });
  }
  if (chart){
    chart.addEventListener('mousemove', feHandleChartHover);
    chart.addEventListener('click', feHandleChartClick);
    chart.addEventListener('mouseleave', feHideChartTip);
  }
  document.addEventListener('dragover', (e) => {
    if (!feHasActiveDrag()) return;
    feAutoScrollOnDrag(e);
  }, { passive: true });

  window.addEventListener('resize', () => {
    const segs = feBuildExpandedSegments();
    feRenderChart(segs);
  });

  feBindDropzone();
  feNewBlank();
  feTryOpenTransferredFit();
}

window.addEventListener('DOMContentLoaded', feInit);
