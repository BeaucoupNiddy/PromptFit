async function loadSecrets(){
  try{
    const res = await fetch('/api/secrets');
    if (!res.ok) return;
    const s = await res.json();
    const setAll = (id, val) => {
      if (!val) return;
      const nodes = document.querySelectorAll(`[id="${id}"]`);
      nodes.forEach((el) => { if (el) el.value = val; });
    };
    setAll('openai_api_key', s.openai_api_key);
    setAll('openai_model', s.openai_model);
    setAll('openrouter_api_key', s.openrouter_api_key);
    setAll('openrouter_model', s.openrouter_model);
  }catch(e){}
}

const UI_STATE_KEY = 'promptfit_ui_state_v1';
const UI_FIELDS = [
  { id: 'provider', type: 'value' },
  { id: 'race_distance', type: 'value' },
  { id: 'hmp', type: 'value' },
  { id: 'pace_easy', type: 'value' },
  { id: 'pace_marathon', type: 'value' },
  { id: 'pace_half_marathon', type: 'value' },
  { id: 'pace_threshold', type: 'value' },
  { id: 'pace_10k', type: 'value' },
  { id: 'pace_5k', type: 'value' },
  { id: 'pace_3k', type: 'value' },
  { id: 'pace_mile', type: 'value' },
  { id: 'tmode', type: 'value' },
  { id: 'targets', type: 'checked' },
  { id: 'margin', type: 'value' },
  { id: 'openai_model', type: 'value' },
  { id: 'openrouter_model', type: 'value' },
  { id: 'plan_provider', type: 'value' },
  { id: 'plan_race_date', type: 'value' },
  { id: 'plan_race_distance', type: 'value' },
  { id: 'plan_preset', type: 'value' },
  { id: 'plan_peak_mileage', type: 'value' },
  { id: 'plan_race_pace', type: 'value' },
  { id: 'plan_easy_pace', type: 'value' },
  { id: 'plan_base_name', type: 'value' },
  { id: 'plan_wu_cd_distance', type: 'value' },
  { id: 'plan_wu_cd_duration', type: 'value' },
  { id: 'plan_wf_mode', type: 'value' },
  { id: 'plan_rest_days', type: 'value' },
  { id: 'plan_include_wu', type: 'checked' },
  { id: 'plan_scale_wu', type: 'checked' },
  { id: 'plan_collapse_doubles', type: 'checked' },
  { id: 'plan_consolidate_workouts', type: 'checked' },
  { id: 'plan_fit_scope', type: 'value' },
  { id: 'plan_package_mode', type: 'value' },
  { id: 'plan_schedule_garmin', type: 'checked' },
  { id: 'plan_garmin_weeks', type: 'value' },
  { id: 'plan_garmin_replace', type: 'checked' },
  { id: 'plan_redistribute', type: 'checked' },
  { id: 'plan_normalize', type: 'checked' },
  { id: 'plan_norm_reduce', type: 'checked' },
];

function loadUiState(){
  try{
    const raw = localStorage.getItem(UI_STATE_KEY);
    if (!raw) return;
    const state = JSON.parse(raw);
    UI_FIELDS.forEach((field) => {
      const nodes = document.querySelectorAll(`[id="${field.id}"]`);
      if (!nodes || !nodes.length) return;
      nodes.forEach((el) => {
        if (!el) return;
        if (field.type === 'checked'){
          if (typeof state[field.id] === 'boolean') el.checked = state[field.id];
        } else if (state[field.id] !== undefined && state[field.id] !== null && state[field.id] !== ''){
          el.value = state[field.id];
        }
      });
    });
  }catch(e){}
}

function saveUiState(){
  try{
    const state = {};
    UI_FIELDS.forEach((field) => {
      const nodes = document.querySelectorAll(`[id="${field.id}"]`);
      if (!nodes || !nodes.length) return;
      const el = nodes[0];
      if (!el) return;
      if (field.type === 'checked') state[field.id] = !!el.checked;
      else state[field.id] = el.value;
    });
    localStorage.setItem(UI_STATE_KEY, JSON.stringify(state));
  }catch(e){}
}

function collectPaceProfile(){
  const keys = ['easy', 'marathon', 'half_marathon', 'threshold', '10k', '5k', '3k', 'mile'];
  const paces = {};
  keys.forEach((key) => {
    const el = document.getElementById('pace_' + key);
    const value = el ? String(el.value || '').trim() : '';
    if (value) paces[key] = value;
  });
  return paces;
}

let _fitChartResults = [];
let _fitChartCurrent = null;
let _quickFitGraphCurrent = null;
let _garminMfaToken = '';
let _garminLocalFits = [];
let _garminConnected = false;
let _garminUploadBusy = false;
let _garminConnectionStatus = null;
let _topGarminUploadActive = false;
let _garminCurrentFitId = '';
let _fitReviewPending = null;
let _fitReviewApproved = null;
const GARMIN_UPLOAD_STATUS_KEY = 'promptfit_garmin_upload_status_v1';
const GARMIN_CURRENT_FIT_KEY = 'promptfit_current_fit_id_v1';
const PROMPTFIT_OPEN_FIT_KEY_PREFIX = 'promptfit_open_fit_payload_';
const WORKOUT_PRESETS = Object.freeze({
  easy_strides: 'Run 45 minutes at an easy conversational effort. After 30 minutes, include 6 × 20-second relaxed strides with 60 seconds of easy jogging between each. Finish easy.',
  progression: 'Run 60 minutes as a progression: 25 minutes easy, 20 minutes steady, 10 minutes at threshold effort, then 5 minutes easy to cool down.',
  threshold_cruise: 'Warm up for 15 minutes easy, then run 3 × 10 minutes at threshold effort with 2 minutes of easy jogging between repetitions. Cool down for 10 minutes easy.',
  six_by_800: 'Warm up for 15 minutes easy, then run 6 × 800 meters at 5K effort with 400 meters of easy jogging after each repetition. Cool down for 10 minutes easy.',
  ten_k_repeats: 'Warm up for 15 minutes easy, then run 5 × 5 minutes at 10K effort with 2 minutes of easy jogging between repetitions. Cool down for 10 minutes easy.',
  hill_repeats: 'Warm up for 15 minutes easy, then run 10 × 60 seconds hard uphill with an easy jog back down after each repeat. Finish with 10 minutes easy.',
  fartlek: 'Warm up for 12 minutes easy, then run 10 × 1 minute fast with 1 minute easy between each effort. Cool down for 10 minutes easy.',
  long_fast_finish: 'Run 90 minutes easy, then 20 minutes at marathon effort, followed by 10 minutes easy to cool down.',
  race_sharpening: 'Warm up for 15 minutes easy, then run 4 × 2 minutes at 5K effort with 2 minutes easy jogging between repetitions. Add 4 × 20-second relaxed strides with 60 seconds easy, then cool down for 10 minutes.',
});

function applyWorkoutPreset(presetId){
  const prompt = document.getElementById('prompt');
  if (!prompt || !presetId || !WORKOUT_PRESETS[presetId]) return;
  prompt.value = WORKOUT_PRESETS[presetId];
  prompt.focus();
  prompt.setSelectionRange(prompt.value.length, prompt.value.length);
}

function _arrayBufferToBase64(buf){
  const bytes = new Uint8Array(buf);
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk){
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

async function _stashFitForEditor(blob, filename){
  try{
    const ab = await blob.arrayBuffer();
    const b64 = _arrayBufferToBase64(ab);
    const token = Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);
    const key = PROMPTFIT_OPEN_FIT_KEY_PREFIX + token;
    const payload = {
      filename: filename || 'workout.fit',
      mime: blob.type || 'application/octet-stream',
      b64: b64
    };
    sessionStorage.setItem(key, JSON.stringify(payload));
    return token;
  }catch(e){
    return null;
  }
}

async function run(){
  const log = document.getElementById('log');
  const generateBtn = document.getElementById('generate_fit_btn');
  try{
    if (generateBtn){
      generateBtn.disabled = true;
      generateBtn.textContent = 'Generating FIT…';
    }
    _fitReviewPending = null;
    _fitReviewApproved = null;
    _topGarminUploadActive = false;
    renderFitReviewCard();
    clearQuickFitGraph('Building the workout graph…');
    log.textContent = 'Parsing prompt and building a FIT for review...';
    const res = await fetch('/api/prompt-to-fit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        prompt: document.getElementById('prompt').value,
        provider: document.getElementById('provider').value,
        openai_api_key: document.getElementById('openai_api_key').value,
        openai_model: (document.getElementById('openai_model') || {}).value || '',
        openrouter_api_key: document.getElementById('openrouter_api_key').value,
        openrouter_model: (document.getElementById('openrouter_model') || {}).value || '',
        race_distance: document.getElementById('race_distance').value,
        hmp: document.getElementById('hmp').value,
        paces: collectPaceProfile(),
        targets: document.getElementById('targets').checked,
        target_mode: document.getElementById('tmode').value,
        target_margin: document.getElementById('margin').value,
        sideload: false
      })
    });
    if (!res.ok){
      clearQuickFitGraph('No graph was generated. Update the workout description and try again.');
      log.textContent = 'Error: ' + (await res.text());
      return;
    }
    let filename = 'workouts.fit';
    try{
      const disp = res.headers.get('Content-Disposition') || '';
      const part = disp.split('filename=')[1];
      if (part) filename = part.replace(/"/g,'');
    }catch(e){}
    const blob = await res.blob();
    let openEditorMsg = '';
    if (/\.fit$/i.test(filename || '') && !(filename || '').toLowerCase().endsWith('.zip')){
      await promptFitStageForReview(blob, filename, {source: 'generator'});
      if (typeof window.feParseFileObject === 'function'){
        try{
          const file = new File([blob], filename || 'workout.fit', {type: blob.type || 'application/octet-stream'});
          await window.feParseFileObject(file);
          openEditorMsg = '\nLoaded in the FIT Editor below for review.';
        }catch(e){}
      } else {
        const token = await _stashFitForEditor(blob, filename);
        if (token){
          const editorUrl = '/fit-editor?open_blob=' + encodeURIComponent(token);
          openEditorMsg = '\nOpen in FIT Editor: ' + editorUrl;
        }
      }
      log.textContent = 'Generated: ' + filename + '\nNothing was downloaded. Review it, then choose Approve & queue or Modify in editor.' + openEditorMsg;
    } else {
      _fitReviewPending = {blob, filename, kind: 'package'};
      _fitReviewApproved = null;
      _setCurrentFitId('');
      clearQuickFitGraph('A multi-workout package does not have a single combined graph. Generate one workout at a time to review its graph here.');
      renderFitReviewCard();
      log.textContent = 'Generated: ' + filename + '\nNothing was downloaded. This multi-workout package can be downloaded manually after you confirm it.';
    }
  }catch(err){
    clearQuickFitGraph('No graph was generated. Update the workout description and try again.');
    log.textContent = 'Error: ' + (err && err.message ? err.message : String(err));
  }finally{
    if (generateBtn){
      generateBtn.disabled = false;
      generateBtn.textContent = 'Generate FIT for review';
    }
  }
}

function _setCurrentFitId(fileId){
  _garminCurrentFitId = String(fileId || '');
  try{
    if (_garminCurrentFitId) sessionStorage.setItem(GARMIN_CURRENT_FIT_KEY, _garminCurrentFitId);
    else sessionStorage.removeItem(GARMIN_CURRENT_FIT_KEY);
  }catch(e){}
}

function _restoreCurrentFitId(){
  if (_garminCurrentFitId) return _garminCurrentFitId;
  try{
    _garminCurrentFitId = sessionStorage.getItem(GARMIN_CURRENT_FIT_KEY) || '';
  }catch(e){}
  return _garminCurrentFitId;
}

function renderFitReviewCard(){
  const card = document.getElementById('fit_review_card');
  if (!card) return;
  const current = _fitReviewApproved || _fitReviewPending;
  if (!current){
    card.classList.add('hidden');
    return;
  }
  const isApproved = !!_fitReviewApproved;
  const isFit = current.kind !== 'package';
  const name = document.getElementById('fit_review_name');
  const badge = document.getElementById('fit_review_badge');
  const detail = document.getElementById('fit_review_detail');
  const decision = document.getElementById('fit_review_decision');
  const delivery = document.getElementById('fit_review_delivery');
  const approveBtn = document.getElementById('fit_approve_btn');
  const garminBtn = document.getElementById('fit_review_garmin_btn');
  const downloadBtn = document.getElementById('fit_review_download_btn');
  const garminFeedback = document.getElementById('fit_review_garmin_feedback');

  card.classList.remove('hidden');
  card.classList.toggle('is-approved', isApproved);
  if (name) name.textContent = fitReviewDisplayTitle(current);
  if (badge) badge.textContent = isApproved ? 'Approved & selected' : (isFit ? 'Awaiting review' : 'Package ready');
  if (detail){
    detail.textContent = isApproved
      ? (isFit
        ? 'This FIT is at the top of the workout queue and selected. Choose a manual delivery action.'
        : 'The package was confirmed and its browser download was started.')
      : (isFit
        ? 'Review the workout, then approve it or return to the editor to make changes.'
        : 'This contains multiple workouts. Confirm it before starting a manual browser download.');
  }
  if (decision) decision.classList.toggle('hidden', isApproved || !isFit);
  if (delivery) delivery.classList.toggle('hidden', !isApproved && isFit);
  if (approveBtn) approveBtn.disabled = false;
  if (downloadBtn) downloadBtn.textContent = isFit ? 'Download FIT' : 'Confirm & download package';
  if (garminFeedback){
    const showGarminFeedback = isApproved && isFit && _topGarminUploadActive;
    garminFeedback.classList.toggle('hidden', !showGarminFeedback);
    if (showGarminFeedback) _renderTopGarminConnectionStatus(_garminConnectionStatus);
  }
  if (garminBtn){
    garminBtn.classList.toggle('hidden', !isFit);
    garminBtn.disabled = !_garminConnected || _garminUploadBusy;
    garminBtn.title = _garminConnected ? '' : 'Connect Garmin before uploading';
  }
}

function fitReviewDisplayTitle(current){
  const title = String((current && current.title) || '').trim();
  if (title) return title;
  if (current && current.kind === 'package') return 'Workout package';

  const filename = String((current && current.filename) || '').trim();
  const readableName = filename
    .replace(/\.(fit|zip)$/i, '')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return readableName || 'Workout';
}

function clearQuickFitGraph(message){
  _quickFitGraphCurrent = null;
  const canvas = document.getElementById('quick_fit_graph');
  if (canvas){
    const ctx = canvas.getContext('2d');
    if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
  }
  const note = document.getElementById('quick_fit_graph_note');
  if (note) note.textContent = message || 'Generate a FIT to see its workout graph here.';
}

async function loadQuickFitGraph(blob, filename){
  clearQuickFitGraph('Drawing the generated workout…');
  try{
    const fd = new FormData();
    fd.append('files', blob, filename || 'workout.fit');
    const res = await fetch('/api/fit-editor/parse', {method: 'POST', body: fd});
    const js = await res.json();
    if (!res.ok) throw new Error(js.detail || 'Could not parse the generated FIT');
    const item = Array.isArray(js.results) ? js.results[0] : null;
    if (!item || !item.graph || !Array.isArray(item.graph.segments) || !item.graph.segments.length){
      throw new Error((item && item.graph && item.graph.error) || 'No graphable workout steps were found');
    }
    _quickFitGraphCurrent = item;
    renderQuickFitGraph();
    return item;
  }catch(err){
    clearQuickFitGraph('Graph unavailable: ' + (err && err.message ? err.message : String(err)));
    return null;
  }
}

async function promptFitStageForReview(blob, filename, options){
  const opts = options || {};
  _fitReviewPending = {
    blob,
    filename: filename || 'workout.fit',
    kind: /\.fit$/i.test(filename || '') ? 'fit' : 'package',
  };
  _fitReviewApproved = null;
  _topGarminUploadActive = false;
  _setCurrentFitId('');
  if (_fitReviewPending.kind === 'fit'){
    const parsedFit = await loadQuickFitGraph(blob, _fitReviewPending.filename);
    _fitReviewPending.title = String(
      opts.title || (parsedFit && parsedFit.workout_name) || ''
    ).trim();
  }
  else clearQuickFitGraph();
  renderFitReviewCard();
  if (opts.scrollToReview){
    const reviewStart = document.getElementById('quick_fit_graph_wrap') || document.getElementById('fit_review_card');
    if (reviewStart) reviewStart.scrollIntoView({behavior: 'smooth', block: 'start'});
  }
  return _fitReviewPending;
}
window.promptFitStageForReview = promptFitStageForReview;

async function approvePendingFit(){
  if (!_fitReviewPending || _fitReviewPending.kind !== 'fit') return;
  const log = document.getElementById('log');
  const approveBtn = document.getElementById('fit_approve_btn');
  if (approveBtn){
    approveBtn.disabled = true;
    approveBtn.textContent = 'Approving…';
  }
  try{
    const fd = new FormData();
    fd.append('file', _fitReviewPending.blob, _fitReviewPending.filename || 'workout.fit');
    const res = await fetch('/api/fit-review/approve', {method: 'POST', body: fd});
    const js = await res.json();
    if (!res.ok) throw new Error(js.detail || 'Could not approve this FIT');
    const approvedFile = js.file || {};
    _fitReviewApproved = {
      ..._fitReviewPending,
      id: approvedFile.id || '',
      filename: approvedFile.name || _fitReviewPending.filename,
    };
    _fitReviewPending = null;
    _setCurrentFitId(_fitReviewApproved.id);
    renderFitReviewCard();
    await loadLocalFitLibrary(_fitReviewApproved.id);
    if (log) log.textContent = `Approved and queued: ${_fitReviewApproved.filename}\nIt is selected at the top of the delivery list.`;
  }catch(err){
    if (log) log.textContent = 'Approval error: ' + (err && err.message ? err.message : String(err));
  }finally{
    if (approveBtn){
      approveBtn.disabled = false;
      approveBtn.textContent = 'Approve & queue';
    }
  }
}

async function modifyPendingFit(){
  const current = _fitReviewPending || _fitReviewApproved;
  if (!current || current.kind !== 'fit') return;
  if (typeof window.feParseFileObject === 'function'){
    try{
      const file = new File([current.blob], current.filename || 'workout.fit', {
        type: current.blob.type || 'application/octet-stream'
      });
      await window.feParseFileObject(file);
    }catch(err){
      const log = document.getElementById('log');
      if (log) log.textContent = 'Could not open the FIT editor: ' + (err && err.message ? err.message : String(err));
      return;
    }
  }
  history.replaceState(null, '', '#editor');
  const editor = document.getElementById('editor');
  if (editor) editor.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function _startBrowserDownload(blob, filename){
  if (!blob) return;
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename || 'workout.fit';
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function downloadApprovedFit(){
  const current = _fitReviewApproved || (
    _fitReviewPending && _fitReviewPending.kind === 'package' ? _fitReviewPending : null
  );
  if (!current) return;
  if (_fitReviewPending && _fitReviewPending.kind === 'package'){
    _fitReviewApproved = _fitReviewPending;
    _fitReviewPending = null;
    renderFitReviewCard();
  }
  _startBrowserDownload(current.blob, current.filename);
}

async function uploadApprovedFitToGarmin(){
  if (!_fitReviewApproved || !_fitReviewApproved.id) return;
  _topGarminUploadActive = true;
  renderFitReviewCard();
  document.querySelectorAll('.gc-local-fit-check').forEach((el) => {
    el.checked = el.value === _fitReviewApproved.id;
  });
  _updateGarminUploadControls();
  await uploadSelectedLocalFits('top');
  const feedback = document.getElementById('fit_review_garmin_feedback');
  if (feedback) feedback.scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

async function saveSecrets(){
  const log = document.getElementById('log');
  try{
    const firstValue = (id) => {
      const nodes = document.querySelectorAll(`[id="${id}"]`);
      for (const el of nodes){
        if (el && el.value) return el.value;
      }
      return nodes.length ? nodes[0].value : '';
    };
    const body = {
      openai_api_key: firstValue('openai_api_key'),
      openai_model: firstValue('openai_model'),
      openrouter_api_key: firstValue('openrouter_api_key'),
      openrouter_model: firstValue('openrouter_model'),
    };
    const res = await fetch('/api/secrets', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    const text = await res.text();
    if (!res.ok){ log.textContent = 'Error: ' + text; return; }
    log.textContent = 'Secrets saved to Keychain (local only).';
  }catch(err){
    log.textContent = 'Error: ' + (err && err.message ? err.message : String(err));
  }
}

function _garminStatusElement(preferred){
  return document.getElementById(preferred || 'gc_log') || document.getElementById('log');
}

function _formatGarminTimestamp(value){
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return parsed.toLocaleString([], {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit'
  });
}

function _selectedGarminLocalFitInputs(){
  return Array.from(document.querySelectorAll('.gc-local-fit-check')).filter((el) => !!el.checked);
}

function _updateGarminUploadControls(){
  const selectedCount = _selectedGarminLocalFitInputs().length;
  const fileInput = document.getElementById('gc_fit_files');
  const deviceFileCount = fileInput && fileInput.files ? fileInput.files.length : 0;
  const localUploadBtn = document.getElementById('gc_local_upload_btn');
  const uploadBtn = document.getElementById('gc_upload_btn');
  const promptBtn = document.getElementById('gc_prompt_upload_btn');
  const selectionCount = document.getElementById('gc_selection_count');

  if (selectionCount) selectionCount.textContent = `${selectedCount} selected`;
  if (localUploadBtn){
    localUploadBtn.disabled = !_garminConnected || _garminUploadBusy || selectedCount === 0;
    localUploadBtn.textContent = selectedCount === 1
      ? 'Upload 1 checked workout'
      : (selectedCount > 1 ? `Upload ${selectedCount} checked workouts` : 'Upload checked workouts');
  }
  if (uploadBtn) uploadBtn.disabled = !_garminConnected || _garminUploadBusy || deviceFileCount === 0;
  if (promptBtn) promptBtn.disabled = !_garminConnected || _garminUploadBusy;
}

function _renderTopGarminConnectionStatus(js){
  const panel = document.getElementById('top_gc_connection_detail');
  if (!panel) return;
  const status = js && typeof js === 'object' ? js : {};
  const ready = status.connected === undefined
    ? _garminConnected
    : (!!status.connected && !!status.verified);
  const savedSession = status.saved_session === undefined ? !!status.connected : !!status.saved_session;
  const icon = document.getElementById('top_gc_connection_icon');
  const title = document.getElementById('top_gc_connection_title');
  const note = document.getElementById('top_gc_connection_note');
  const meta = document.getElementById('top_gc_connection_meta');

  panel.classList.remove('is-success', 'is-warning', 'is-error');
  panel.classList.add(ready ? 'is-success' : (savedSession ? 'is-error' : 'is-warning'));
  if (icon) icon.textContent = ready ? '✓' : (savedSession ? '!' : '—');
  if (title){
    title.textContent = ready
      ? (status.account_name ? `Connected as ${status.account_name}` : 'Garmin connection verified')
      : (savedSession ? 'Garmin connection needs attention' : 'Garmin is not connected');
  }
  if (note){
    note.textContent = status.message || (ready
      ? 'Garmin accepted the saved connection. This workout is ready to upload.'
      : 'Open the Garmin section below to reconnect.');
  }
  if (meta){
    const bits = [];
    if (ready) bits.push('Live check passed');
    const checkedAt = _formatGarminTimestamp(status.checked_at);
    if (checkedAt) bits.push(`Checked ${checkedAt}`);
    if (status.verification_error) bits.push(status.verification_error);
    meta.textContent = bits.join(' · ');
  }
}

function _applyGarminConnectionStatus(js){
  if (!js || typeof js !== 'object' || typeof js.connected !== 'boolean') return;
  const connected = !!js.connected;
  const verified = !!js.verified;
  const savedSession = js.saved_session === undefined ? connected : !!js.saved_session;
  const ready = connected && verified;
  const canManage = !!js.can_manage;
  const badge = document.getElementById('gc_connection_badge');
  const note = document.getElementById('gc_connection_note');
  const title = document.getElementById('gc_connection_title');
  const meta = document.getElementById('gc_connection_meta');
  const icon = document.getElementById('gc_connection_icon');
  const detail = document.getElementById('gc_connection_detail');
  const form = document.getElementById('gc_connect_form');
  const actions = document.getElementById('gc_connected_actions');
  _garminConnectionStatus = js;
  _garminConnected = ready;
  _renderTopGarminConnectionStatus(js);
  renderFitReviewCard();

  if (badge){
    badge.textContent = ready ? 'Verified' : (savedSession ? 'Not verified' : 'Not connected');
    badge.style.background = ready ? 'rgba(15,106,91,0.14)' : 'rgba(231,111,81,0.14)';
  }
  if (detail){
    detail.classList.remove('is-success', 'is-warning', 'is-error');
    detail.classList.add(ready ? 'is-success' : (savedSession ? 'is-error' : 'is-warning'));
  }
  if (icon) icon.textContent = ready ? '✓' : (savedSession ? '!' : '—');
  if (title){
    title.textContent = ready
      ? (js.account_name ? `Connected as ${js.account_name}` : 'Connection verified')
      : (savedSession ? 'Saved connection not verified' : 'Garmin is not connected');
  }
  if (note){
    note.textContent = js.message || (ready
      ? 'Garmin accepted the saved connection.'
      : (canManage ? 'Complete the one-time Garmin setup below.' : 'Connect Garmin once from localhost on the Mac.'));
  }
  if (meta){
    const bits = [];
    if (ready) bits.push('Live check passed');
    const checkedAt = _formatGarminTimestamp(js.checked_at);
    if (checkedAt) bits.push(`Checked ${checkedAt}`);
    if (js.verification_error) bits.push(js.verification_error);
    meta.textContent = bits.join(' · ');
  }
  if (form) form.style.display = (!ready && canManage) ? 'block' : 'none';
  if (actions) actions.style.display = (savedSession && canManage) ? 'flex' : 'none';
  _updateGarminUploadControls();
}

async function loadGarminStatus(){
  if (!document.getElementById('garmin-connect')) return;
  const log = _garminStatusElement('gc_log');
  const badge = document.getElementById('gc_connection_badge');
  const title = document.getElementById('gc_connection_title');
  const note = document.getElementById('gc_connection_note');
  const icon = document.getElementById('gc_connection_icon');
  const checkBtn = document.getElementById('gc_check_connection_btn');
  if (badge) badge.textContent = 'Checking…';
  if (title) title.textContent = 'Checking Garmin…';
  if (note) note.textContent = 'Confirming that Garmin accepts the saved connection on this Mac.';
  if (icon) icon.textContent = '…';
  if (checkBtn) checkBtn.disabled = true;
  if (log) log.textContent = '';
  try{
    const res = await fetch('/api/garmin/status');
    const js = await res.json();
    if (!res.ok) throw new Error(js.detail || 'Could not check Garmin connection');
    _applyGarminConnectionStatus(js);
  }catch(err){
    _garminConnected = false;
    _garminConnectionStatus = {
      connected: false,
      verified: false,
      saved_session: false,
      message: 'The app could not complete a live connection check.',
      verification_error: err && err.message ? err.message : String(err),
    };
    _renderTopGarminConnectionStatus(_garminConnectionStatus);
    renderFitReviewCard();
    _updateGarminUploadControls();
    if (badge) badge.textContent = 'Check failed';
    if (title) title.textContent = 'Could not verify Garmin';
    if (note) note.textContent = 'The app could not complete a live connection check.';
    if (icon) icon.textContent = '!';
    if (log) log.textContent = 'Connection check error: ' + (err && err.message ? err.message : String(err));
  }finally{
    if (checkBtn) checkBtn.disabled = false;
  }
}

async function connectGarmin(){
  const log = _garminStatusElement('gc_log');
  const usernameEl = document.getElementById('gc_username');
  const passwordEl = document.getElementById('gc_password');
  const username = (usernameEl && usernameEl.value || '').trim();
  const password = passwordEl && passwordEl.value || '';
  if (!username || !password){
    log.textContent = 'Enter your Garmin username and password for the one-time connection.';
    return;
  }
  try{
    log.textContent = 'Connecting Garmin and saving a secure session…';
    const res = await fetch('/api/garmin/connect', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({gc_username: username, gc_password: password})
    });
    if (passwordEl) passwordEl.value = '';
    await _handleGarminResponse(res, 'gc_log');
  }catch(err){
    if (passwordEl) passwordEl.value = '';
    log.textContent = 'Error: ' + (err && err.message ? err.message : String(err));
  }
}

async function disconnectGarmin(){
  const log = _garminStatusElement('gc_log');
  if (!window.confirm('Remove the saved Garmin connection from this Mac?')) return;
  try{
    const res = await fetch('/api/garmin/connection', {method: 'DELETE'});
    await _handleGarminResponse(res, 'gc_log');
    await loadGarminStatus();
  }catch(err){
    log.textContent = 'Error: ' + (err && err.message ? err.message : String(err));
  }
}

function _formatGarminResult(js){
  if (!js || typeof js !== 'object') return String(js || 'Garmin request completed.');
  const lines = [js.message || 'Garmin request completed.'];
  if (Array.isArray(js.results)){
    js.results.forEach((item) => {
      const name = item.source || item.workoutName || 'Workout';
      if (item.ok){
        let detail = item.scheduled && item.scheduleDate ? `scheduled ${item.scheduleDate}` : 'added to workout library';
        if (item.scheduleError) detail += ` (schedule warning: ${item.scheduleError})`;
        lines.push(`✓ ${name}: ${detail}`);
      } else {
        lines.push(`✕ ${name}: ${item.error || 'upload failed'}`);
      }
    });
  }
  return lines.join('\n');
}

function _setTopGarminUploadBusy(message, count){
  const panel = document.getElementById('top_gc_upload_status');
  if (!panel) return;
  const icon = document.getElementById('top_gc_upload_icon');
  const title = document.getElementById('top_gc_upload_title');
  const detail = document.getElementById('top_gc_upload_detail');
  const meta = document.getElementById('top_gc_upload_meta');
  const results = document.getElementById('top_gc_upload_results');
  panel.classList.remove('is-success', 'is-warning', 'is-error');
  panel.setAttribute('aria-busy', 'true');
  if (icon) icon.textContent = '…';
  if (title) title.textContent = message || 'Uploading to Garmin Connect…';
  if (detail) detail.textContent = 'Keep this page open until Garmin returns a confirmation.';
  if (meta) meta.textContent = count ? `${count} workout${count === 1 ? '' : 's'} in this request` : '';
  if (results) results.replaceChildren();
}

function _setGarminUploadBusy(busy, message, count){
  _garminUploadBusy = !!busy;
  _updateGarminUploadControls();
  renderFitReviewCard();
  if (!busy) return;
  if (_topGarminUploadActive) _setTopGarminUploadBusy(message, count);

  const panel = document.getElementById('gc_upload_status');
  const icon = document.getElementById('gc_upload_icon');
  const title = document.getElementById('gc_upload_title');
  const detail = document.getElementById('gc_upload_detail');
  const meta = document.getElementById('gc_upload_meta');
  const results = document.getElementById('gc_upload_results');
  if (panel){
    panel.classList.remove('is-success', 'is-warning', 'is-error');
    panel.setAttribute('aria-busy', 'true');
  }
  if (icon) icon.textContent = '…';
  if (title) title.textContent = message || 'Uploading to Garmin Connect…';
  if (detail) detail.textContent = 'Keep this page open until Garmin returns a confirmation.';
  if (meta) meta.textContent = count ? `${count} workout${count === 1 ? '' : 's'} in this request` : '';
  if (results) results.replaceChildren();
}

function _garminUploadResultDetail(item){
  if (!item || !item.ok) return (item && item.error) || 'Garmin did not accept this workout.';
  const details = [];
  if (item.scheduled && item.scheduleDate) details.push(`Scheduled for ${item.scheduleDate}`);
  else details.push('Added to your workout library');
  if (item.workoutId !== undefined && item.workoutId !== null && item.workoutId !== ''){
    details.push(`Garmin workout ID ${item.workoutId}`);
  }
  if (item.scheduleError) details.push(`Scheduling warning: ${item.scheduleError}`);
  return details.join(' · ');
}

function _renderTopGarminUploadStatus(js){
  if (!js || typeof js !== 'object') return;
  const panel = document.getElementById('top_gc_upload_status');
  if (!panel) return;
  const icon = document.getElementById('top_gc_upload_icon');
  const title = document.getElementById('top_gc_upload_title');
  const detail = document.getElementById('top_gc_upload_detail');
  const meta = document.getElementById('top_gc_upload_meta');
  const resultsEl = document.getElementById('top_gc_upload_results');
  const results = Array.isArray(js.results) ? js.results : [];
  const successful = Number(js.successful || 0);
  const failed = Number(js.failed || 0);
  const attempted = Number(js.attempted !== undefined ? js.attempted : (successful + failed));
  const isSuccess = attempted > 0 && successful === attempted && failed === 0;
  const isPartial = successful > 0 && failed > 0;

  panel.classList.remove('is-success', 'is-warning', 'is-error');
  panel.classList.add(isSuccess ? 'is-success' : (isPartial ? 'is-warning' : 'is-error'));
  panel.setAttribute('aria-busy', 'false');
  if (icon) icon.textContent = isSuccess ? '✓' : (isPartial ? '!' : '×');
  if (title){
    if (isSuccess) title.textContent = `Upload confirmed — ${successful} of ${attempted}`;
    else if (isPartial) title.textContent = `Partially uploaded — ${successful} of ${attempted}`;
    else title.textContent = attempted ? `Upload failed — 0 of ${attempted}` : 'Upload could not start';
  }
  if (detail){
    const confirmedIds = results.filter((item) => item && item.ok && item.workoutId !== undefined && item.workoutId !== null).length;
    const confirmation = confirmedIds
      ? ` Garmin returned ${confirmedIds} workout ID${confirmedIds === 1 ? '' : 's'} as confirmation.`
      : '';
    detail.textContent = `${js.message || 'Garmin upload request completed.'}${confirmation}`;
  }
  if (meta){
    const completedAt = _formatGarminTimestamp(js.completed_at);
    meta.textContent = completedAt ? `Completed ${completedAt}` : '';
  }
  if (resultsEl){
    resultsEl.replaceChildren();
    results.forEach((item) => {
      const row = document.createElement('div');
      row.className = `garmin-upload-result${item && item.ok ? '' : ' is-error'}`;
      const resultIcon = document.createElement('span');
      resultIcon.className = 'garmin-upload-result-icon';
      resultIcon.textContent = item && item.ok ? '✓' : '×';
      const copy = document.createElement('div');
      const name = document.createElement('b');
      name.textContent = (item && (item.source || item.workoutName)) || 'Workout';
      const resultDetail = document.createElement('span');
      resultDetail.textContent = _garminUploadResultDetail(item);
      copy.append(name, resultDetail);
      row.append(resultIcon, copy);
      resultsEl.append(row);
    });
  }
}

function _renderGarminUploadStatus(js, persist){
  if (!js || typeof js !== 'object') return;
  if (_topGarminUploadActive) _renderTopGarminUploadStatus(js);
  const panel = document.getElementById('gc_upload_status');
  if (!panel) return;
  const icon = document.getElementById('gc_upload_icon');
  const title = document.getElementById('gc_upload_title');
  const detail = document.getElementById('gc_upload_detail');
  const meta = document.getElementById('gc_upload_meta');
  const resultsEl = document.getElementById('gc_upload_results');
  const results = Array.isArray(js.results) ? js.results : [];
  const successful = Number(js.successful || 0);
  const failed = Number(js.failed || 0);
  const attempted = Number(js.attempted !== undefined ? js.attempted : (successful + failed));
  const isSuccess = attempted > 0 && successful === attempted && failed === 0;
  const isPartial = successful > 0 && failed > 0;

  panel.classList.remove('is-success', 'is-warning', 'is-error');
  panel.classList.add(isSuccess ? 'is-success' : (isPartial ? 'is-warning' : 'is-error'));
  panel.setAttribute('aria-busy', 'false');
  if (icon) icon.textContent = isSuccess ? '✓' : (isPartial ? '!' : '×');
  if (title){
    if (isSuccess) title.textContent = `Upload confirmed — ${successful} of ${attempted}`;
    else if (isPartial) title.textContent = `Partially uploaded — ${successful} of ${attempted}`;
    else title.textContent = attempted ? `Upload failed — 0 of ${attempted}` : 'Upload could not start';
  }
  if (detail){
    const confirmedIds = results.filter((item) => item && item.ok && item.workoutId !== undefined && item.workoutId !== null).length;
    const confirmation = confirmedIds
      ? ` Garmin returned ${confirmedIds} workout ID${confirmedIds === 1 ? '' : 's'} as confirmation.`
      : '';
    detail.textContent = `${js.message || 'Garmin upload request completed.'}${confirmation}`;
  }
  if (meta){
    const completedAt = _formatGarminTimestamp(js.completed_at);
    meta.textContent = completedAt ? `Completed ${completedAt}` : '';
  }
  if (resultsEl){
    resultsEl.replaceChildren();
    results.forEach((item) => {
      const row = document.createElement('div');
      row.className = `garmin-upload-result${item && item.ok ? '' : ' is-error'}`;
      const resultIcon = document.createElement('span');
      resultIcon.className = 'garmin-upload-result-icon';
      resultIcon.textContent = item && item.ok ? '✓' : '×';
      const copy = document.createElement('div');
      const name = document.createElement('b');
      name.textContent = (item && (item.source || item.workoutName)) || 'Workout';
      const resultDetail = document.createElement('span');
      resultDetail.textContent = _garminUploadResultDetail(item);
      copy.append(name, resultDetail);
      row.append(resultIcon, copy);
      resultsEl.append(row);
    });
  }
  if (persist){
    try { localStorage.setItem(GARMIN_UPLOAD_STATUS_KEY, JSON.stringify(js)); } catch (e) {}
  }
}

function _restoreGarminUploadStatus(){
  try{
    const raw = localStorage.getItem(GARMIN_UPLOAD_STATUS_KEY);
    if (!raw) return;
    const js = JSON.parse(raw);
    if (js && typeof js === 'object') _renderGarminUploadStatus(js, false);
  }catch(e){}
}

async function _handleGarminUploadResponse(res){
  const text = await res.text();
  let js = null;
  try { js = JSON.parse(text); } catch (e) {}
  if (!res.ok){
    const detail = js && js.detail ? js.detail : (text || 'Garmin did not accept the request.');
    const failedStatus = {
      status: 'failed', attempted: 0, successful: 0, failed: 0,
      completed_at: new Date().toISOString(),
      message: detail,
      results: []
    };
    _renderGarminUploadStatus(failedStatus, true);
    return {ok: false, data: failedStatus};
  }
  _renderGarminUploadStatus(js || {
    status: 'failed', attempted: 0, successful: 0, failed: 0,
    completed_at: new Date().toISOString(), message: text || 'Garmin returned no confirmation.', results: []
  }, true);
  return {ok: true, data: js || {}};
}

async function _handleGarminResponse(res, preferredLog){
  const status = _garminStatusElement(preferredLog);
  const text = await res.text();
  let js = null;
  try { js = JSON.parse(text); } catch (e) {}
  if (!res.ok){
    const detail = js && js.detail ? js.detail : text;
    status.textContent = 'Error: ' + detail;
    return false;
  }
  if (js && js.status === 'mfa_required'){
    _garminMfaToken = js.mfa_token || '';
    const box = document.getElementById('gc_mfa_box');
    if (box) box.style.display = 'block';
    status.textContent = js.message || 'Enter the verification code Garmin sent you.';
    const input = document.getElementById('gc_mfa_code');
    if (input) input.focus();
    return true;
  }
  _garminMfaToken = '';
  const box = document.getElementById('gc_mfa_box');
  if (box) box.style.display = 'none';
  if (js) _applyGarminConnectionStatus(js);
  status.textContent = js ? _formatGarminResult(js) : text;
  return true;
}

async function sendGarmin(){
  _topGarminUploadActive = false;
  renderFitReviewCard();
  _setGarminUploadBusy(true, 'Creating and uploading the current prompt workout…', 1);
  try{
    const res = await fetch('/api/prompt-to-garmin', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        prompt: document.getElementById('prompt').value,
        provider: document.getElementById('provider').value,
        openai_api_key: document.getElementById('openai_api_key').value,
        openai_model: (document.getElementById('openai_model') || {}).value || '',
        openrouter_api_key: document.getElementById('openrouter_api_key').value,
        openrouter_model: (document.getElementById('openrouter_model') || {}).value || '',
        race_distance: document.getElementById('race_distance').value,
        hmp: document.getElementById('hmp').value,
        paces: collectPaceProfile(),
        targets: document.getElementById('targets').checked,
        target_mode: document.getElementById('tmode').value,
        target_margin: document.getElementById('margin').value
      })
    });
    const outcome = await _handleGarminUploadResponse(res);
    if (!outcome.ok) loadGarminStatus();
  }catch(err){
    _renderGarminUploadStatus({
      status: 'failed', attempted: 0, successful: 0, failed: 0,
      completed_at: new Date().toISOString(),
      message: err && err.message ? err.message : String(err), results: []
    }, true);
  }finally{
    _setGarminUploadBusy(false);
  }
}

function _formatLocalFitDate(value){
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return parsed.toLocaleString([], {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit'
  });
}

function filterGarminLocalFits(){
  const query = (((document.getElementById('gc_local_fit_search') || {}).value) || '').trim().toLowerCase();
  const rows = document.querySelectorAll('#gc_local_fit_list [data-fit-search]');
  rows.forEach((row) => {
    row.style.display = !query || (row.dataset.fitSearch || '').includes(query) ? 'flex' : 'none';
  });
}

function renderGarminLocalFits(){
  const list = document.getElementById('gc_local_fit_list');
  if (!list) return;
  _restoreCurrentFitId();
  const selectedIds = new Set(_selectedGarminLocalFitInputs().map((el) => el.value));
  if (_garminCurrentFitId) selectedIds.add(_garminCurrentFitId);
  list.replaceChildren();
  if (!_garminLocalFits.length){
    list.textContent = 'No FIT workouts were found in fit_out_gui on this Mac yet.';
    _updateGarminUploadControls();
    return;
  }

  const orderedFits = _garminLocalFits.slice().sort((a, b) => {
    if (a.id === _garminCurrentFitId) return -1;
    if (b.id === _garminCurrentFitId) return 1;
    return 0;
  });

  orderedFits.forEach((fit) => {
    const row = document.createElement('div');
    row.dataset.fitSearch = `${fit.name || ''} ${fit.folder || ''} ${fit.modified || ''}`.toLowerCase();
    row.style.display = 'flex';
    row.style.alignItems = 'center';
    row.style.gap = '10px';
    row.style.padding = '10px 4px';
    row.style.borderBottom = '1px solid rgba(255,255,255,0.18)';

    const selectionLabel = document.createElement('label');
    selectionLabel.style.display = 'flex';
    selectionLabel.style.alignItems = 'flex-start';
    selectionLabel.style.gap = '10px';
    selectionLabel.style.minWidth = '0';
    selectionLabel.style.flex = '1';
    selectionLabel.style.cursor = 'pointer';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.className = 'gc-local-fit-check';
    checkbox.value = fit.id || '';
    checkbox.checked = selectedIds.has(fit.id || '');
    checkbox.style.marginTop = '3px';
    checkbox.addEventListener('change', _updateGarminUploadControls);

    const textBox = document.createElement('span');
    textBox.style.minWidth = '0';
    const title = document.createElement('span');
    title.textContent = fit.name || 'Workout';
    title.style.display = 'block';
    title.style.fontWeight = '600';
    title.style.overflowWrap = 'anywhere';
    if (fit.id === _garminCurrentFitId){
      const currentBadge = document.createElement('span');
      currentBadge.textContent = 'Current';
      currentBadge.style.marginLeft = '7px';
      currentBadge.style.padding = '2px 6px';
      currentBadge.style.borderRadius = '999px';
      currentBadge.style.color = '#102b27';
      currentBadge.style.background = '#c9f45a';
      currentBadge.style.fontSize = '0.62rem';
      currentBadge.style.fontWeight = '800';
      title.append(currentBadge);
    }
    const meta = document.createElement('span');
    const sizeKb = Math.max(1, Math.round(Number(fit.size || 0) / 1024));
    meta.textContent = `${_formatLocalFitDate(fit.modified)} · ${sizeKb} KB`;
    meta.style.display = 'block';
    meta.style.marginTop = '3px';
    meta.style.opacity = '0.76';
    meta.style.fontSize = '0.82rem';
    textBox.append(title, meta);

    const downloadLink = document.createElement('a');
    downloadLink.className = 'text-action';
    downloadLink.textContent = 'Download';
    downloadLink.href = '/api/garmin/local-fit-download?file=' + encodeURIComponent(fit.id || '');
    downloadLink.title = `Download ${fit.name || 'workout'}`;

    selectionLabel.append(checkbox, textBox);
    row.append(selectionLabel, downloadLink);
    list.append(row);
  });
  filterGarminLocalFits();
  _updateGarminUploadControls();
}

async function loadLocalFitLibrary(preselectId){
  const list = document.getElementById('gc_local_fit_list');
  if (!list) return;
  if (preselectId) _setCurrentFitId(preselectId);
  list.textContent = 'Loading workouts from this Mac…';
  _updateGarminUploadControls();
  try{
    const res = await fetch('/api/garmin/local-fits');
    const js = await res.json();
    if (!res.ok) throw new Error(js.detail || 'Could not load workouts');
    _garminLocalFits = Array.isArray(js.files) ? js.files : [];
    renderGarminLocalFits();
  }catch(err){
    list.textContent = 'Error loading workouts: ' + (err && err.message ? err.message : String(err));
    _updateGarminUploadControls();
  }
}

async function uploadSelectedLocalFits(source){
  if (source !== 'top'){
    _topGarminUploadActive = false;
    renderFitReviewCard();
  }
  const selectedInputs = _selectedGarminLocalFitInputs();
  const selected = selectedInputs.map((el) => el.value);
  if (!selected.length){
    _renderGarminUploadStatus({
      status: 'failed', attempted: 0, successful: 0, failed: 0,
      completed_at: new Date().toISOString(),
      message: 'Check at least one workout from the Mac list first.', results: []
    }, false);
    return;
  }
  _setGarminUploadBusy(true, `Uploading ${selected.length} checked workout${selected.length === 1 ? '' : 's'}…`, selected.length);
  try{
    const res = await fetch('/api/garmin/local-fit-upload', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        files: selected,
        schedule_date: ((document.getElementById('gc_schedule_date') || {}).value || '')
      })
    });
    const outcome = await _handleGarminUploadResponse(res);
    if (outcome.ok){
      const confirmedIds = new Set(
        (Array.isArray(outcome.data.results) ? outcome.data.results : [])
          .filter((item) => item && item.ok && item.sourceId)
          .map((item) => item.sourceId)
      );
      selectedInputs.forEach((el) => {
        if (confirmedIds.has(el.value)) el.checked = false;
      });
    } else {
      loadGarminStatus();
    }
  }catch(err){
    _renderGarminUploadStatus({
      status: 'failed', attempted: selected.length, successful: 0, failed: selected.length,
      completed_at: new Date().toISOString(),
      message: err && err.message ? err.message : String(err), results: []
    }, true);
  }finally{
    _setGarminUploadBusy(false);
    _updateGarminUploadControls();
  }
}

async function uploadFitsToGarmin(){
  _topGarminUploadActive = false;
  renderFitReviewCard();
  const input = document.getElementById('gc_fit_files');
  if (!input || !input.files || input.files.length === 0){
    _renderGarminUploadStatus({
      status: 'failed', attempted: 0, successful: 0, failed: 0,
      completed_at: new Date().toISOString(),
      message: 'Choose one or more workout FIT files first.', results: []
    }, false);
    return;
  }
  const fileCount = input.files.length;
  const fd = new FormData();
  Array.from(input.files).forEach((file) => fd.append('files', file));
  fd.append('schedule_date', ((document.getElementById('gc_schedule_date') || {}).value || ''));

  _setGarminUploadBusy(true, `Uploading ${fileCount} file${fileCount === 1 ? '' : 's'} from this device…`, fileCount);
  try{
    const res = await fetch('/api/garmin/fit-upload', { method: 'POST', body: fd });
    const outcome = await _handleGarminUploadResponse(res);
    if (outcome.ok && Number(outcome.data.successful || 0) > 0){
      input.value = '';
    } else if (!outcome.ok) {
      loadGarminStatus();
    }
  }catch(err){
    _renderGarminUploadStatus({
      status: 'failed', attempted: fileCount, successful: 0, failed: fileCount,
      completed_at: new Date().toISOString(),
      message: err && err.message ? err.message : String(err), results: []
    }, true);
  }finally{
    _setGarminUploadBusy(false);
    _updateGarminUploadControls();
  }
}

async function finishGarminMfa(){
  const log = _garminStatusElement('gc_log');
  const code = ((document.getElementById('gc_mfa_code') || {}).value || '').trim();
  if (!_garminMfaToken){
    log.textContent = 'The Garmin verification request expired. Start the upload again.';
    return;
  }
  if (!code){
    log.textContent = 'Enter the verification code Garmin sent you.';
    return;
  }
  try{
    log.textContent = 'Verifying with Garmin and saving the connection…';
    const res = await fetch('/api/garmin/mfa', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ mfa_token: _garminMfaToken, mfa_code: code })
    });
    const ok = await _handleGarminResponse(res, 'gc_log');
    if (ok){
      const input = document.getElementById('gc_mfa_code');
      if (input) input.value = '';
    }
  }catch(err){
    log.textContent = 'Error: ' + (err && err.message ? err.message : String(err));
  }
}

async function previewPlan(){
  const log = document.getElementById('log');
  const out = document.getElementById('plan_json');
  const details = document.getElementById('workout_json_details');
  try{
    if (details) details.open = true;
    out.textContent = 'Loading...';
    const res = await fetch('/api/preview-plan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        prompt: document.getElementById('prompt').value,
        provider: document.getElementById('provider').value,
        openai_api_key: document.getElementById('openai_api_key').value,
        openai_model: (document.getElementById('openai_model') || {}).value || '',
        openrouter_api_key: document.getElementById('openrouter_api_key').value,
        openrouter_model: (document.getElementById('openrouter_model') || {}).value || '',
        race_distance: document.getElementById('race_distance').value,
        hmp: document.getElementById('hmp').value,
        paces: collectPaceProfile()
      })
    });
    const txt = await res.text();
    if (!res.ok){
      out.textContent = '';
      log.textContent = 'Error: ' + txt;
      return;
    }
    try{
      const obj = JSON.parse(txt);
      out.textContent = JSON.stringify(obj, null, 2);
    }catch(e){
      out.textContent = txt;
    }
  }catch(err){
    out.textContent = '';
    log.textContent = 'Error: ' + (err && err.message ? err.message : String(err));
  }
}

async function parseFits(){
  const box = document.getElementById('fit_parse_out');
  try{
    const inp = document.getElementById('fit_files');
    if (!inp.files || inp.files.length === 0){ box.textContent = 'Choose one or more .fit files'; return; }
    const fd = new FormData();
    for (const f of inp.files){ fd.append('files', f, f.name); }
    const res = await fetch('/api/parse-fit', { method:'POST', body: fd });
    const js = await res.json();
    if (!res.ok){ box.textContent = JSON.stringify(js); return; }
    let outText = '';
    for (const item of js.results){
      outText += 'File: ' + item.name + '\n';
      outText += ((item.summary || '')) + '\n\n';
    }
    box.textContent = outText || '(no results)';
    updateFitChartResults(js.results || []);
  }catch(err){
    box.textContent = 'Error: ' + (err && err.message ? err.message : String(err));
  }
}

function updateFitChartResults(results){
  _fitChartResults = Array.isArray(results) ? results : [];
  const wrap = document.getElementById('fit_chart_wrap');
  const select = document.getElementById('fit_chart_select');
  const note = document.getElementById('fit_chart_note');
  if (!wrap) return;

  const valid = _fitChartResults.filter((item) => item && item.graph && Array.isArray(item.graph.segments) && item.graph.segments.length > 0);
  if (valid.length === 0){
    wrap.classList.add('hidden');
    _fitChartCurrent = null;
    if (note) note.textContent = '';
    return;
  }

  wrap.classList.remove('hidden');
  if (select){
    select.innerHTML = '';
    valid.forEach((item, idx) => {
      const opt = document.createElement('option');
      opt.value = String(idx);
      opt.textContent = item.name || ('Workout ' + (idx + 1));
      select.appendChild(opt);
    });
    if (valid.length > 1) select.classList.remove('hidden');
    else select.classList.add('hidden');
    select.onchange = () => {
      const idx = parseInt(select.value, 10);
      _fitChartCurrent = valid[idx] || valid[0];
      renderFitChart();
    };
  }
  _fitChartCurrent = valid[0];
  if (select) select.value = '0';
  renderFitChart();
}

function renderWorkoutGraph(graphItem, canvasId, noteId, darkMode){
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const note = document.getElementById(noteId);
  if (!graphItem || !graphItem.graph){
    ctx.clearRect(0, 0, rect.width, rect.height);
    return;
  }

  const graph = graphItem.graph;
  const segments = graph.segments || [];
  let total = graph.total_seconds || 0;
  if (!total){
    total = segments.reduce((acc, s) => acc + (s.duration_s || 0), 0);
  }
  const paces = segments.map((s) => s.pace_min_per_mi).filter((p) => typeof p === 'number' && isFinite(p));
  if (paces.length === 0 || total <= 0){
    ctx.clearRect(0, 0, rect.width, rect.height);
    return;
  }
  let paceMin = Math.min(...paces);
  let paceMax = Math.max(...paces);
  if (paceMax - paceMin < 0.5){
    paceMax += 0.25;
    paceMin -= 0.25;
  }
  paceMin = Math.max(0.1, paceMin);

  const pad = { left: 52, right: 16, top: 16, bottom: 30 };
  const w = rect.width - pad.left - pad.right;
  const h = rect.height - pad.top - pad.bottom;
  const styles = getComputedStyle(document.documentElement);
  const lineColor = darkMode ? '#c9f45a' : (styles.getPropertyValue('--chart-line') || '#e76f51').trim();
  const fillColor = darkMode ? 'rgba(201,244,90,0.18)' : (styles.getPropertyValue('--chart-fill') || 'rgba(231,111,81,0.22)').trim();
  const gridColor = darkMode ? 'rgba(255,255,255,0.12)' : (styles.getPropertyValue('--chart-grid') || 'rgba(38,70,83,0.16)').trim();
  const axisColor = darkMode ? 'rgba(255,255,255,0.28)' : (styles.getPropertyValue('--chart-axis') || 'rgba(28,28,28,0.55)').trim();
  const textColor = darkMode ? 'rgba(255,255,255,0.7)' : (styles.getPropertyValue('--muted') || '#5a5957').trim();

  const xAt = (t) => pad.left + (t / total) * w;
  const yAt = (p) => pad.top + ((p - paceMin) / (paceMax - paceMin)) * h;
  const formatPace = (p) => {
    const totalSec = Math.max(0, Math.round(p * 60));
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return m + ':' + String(s).padStart(2, '0');
  };
  const formatTime = (s) => {
    const sec = Math.max(0, Math.round(s));
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const r = sec % 60;
    if (h > 0) return h + ':' + String(m).padStart(2, '0') + ':' + String(r).padStart(2, '0');
    return m + ':' + String(r).padStart(2, '0');
  };

  ctx.clearRect(0, 0, rect.width, rect.height);

  ctx.strokeStyle = gridColor;
  ctx.lineWidth = 1;
  const gridLines = 4;
  ctx.font = '12px "Space Grotesk", "Trebuchet MS", sans-serif';
  ctx.fillStyle = textColor;
  for (let i = 0; i <= gridLines; i++){
    const y = pad.top + (i / gridLines) * h;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(pad.left + w, y);
    ctx.stroke();
    const paceLabel = formatPace(paceMin + (i / gridLines) * (paceMax - paceMin));
    ctx.fillText(paceLabel, 6, y + 4);
  }

  ctx.strokeStyle = axisColor;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad.left, pad.top + h);
  ctx.lineTo(pad.left + w, pad.top + h);
  ctx.stroke();

  const timeTicks = 4;
  for (let i = 0; i <= timeTicks; i++){
    const t = total * (i / timeTicks);
    const x = xAt(t);
    ctx.beginPath();
    ctx.moveTo(x, pad.top + h);
    ctx.lineTo(x, pad.top + h + 6);
    ctx.stroke();
    ctx.fillText(formatTime(t), x - 12, pad.top + h + 20);
  }

  let t = 0;
  ctx.beginPath();
  segments.forEach((seg) => {
    const dur = seg.duration_s || 0;
    if (dur <= 0) return;
    const pace = seg.pace_min_per_mi;
    if (!pace) { t += dur; return; }
    const x0 = xAt(t);
    const x1 = xAt(t + dur);
    const y = yAt(pace);
    if (t === 0){
      ctx.moveTo(x0, y);
    }else{
      ctx.lineTo(x0, y);
    }
    ctx.lineTo(x1, y);
    t += dur;
  });
  ctx.lineTo(xAt(total), pad.top + h);
  ctx.lineTo(pad.left, pad.top + h);
  ctx.closePath();
  ctx.fillStyle = fillColor;
  ctx.fill();

  t = 0;
  ctx.beginPath();
  segments.forEach((seg) => {
    const dur = seg.duration_s || 0;
    if (dur <= 0) return;
    const pace = seg.pace_min_per_mi;
    if (!pace) { t += dur; return; }
    const x0 = xAt(t);
    const x1 = xAt(t + dur);
    const y = yAt(pace);
    if (t === 0){
      ctx.moveTo(x0, y);
    }else{
      ctx.lineTo(x0, y);
    }
    ctx.lineTo(x1, y);
    t += dur;
  });
  ctx.strokeStyle = lineColor;
  ctx.lineWidth = 2;
  ctx.stroke();

  if (note){
    const label = graphItem.name ? graphItem.name + ' • ' : '';
    let extra = '';
    if (graph.inferred_seconds && graph.inferred_seconds > 0){
      extra = ' • includes inferred rest';
    }
    let hint = '';
    if (graph.total_hint_seconds && Math.abs((graph.total_hint_seconds || 0) - total) > 30){
      hint = ' • name time ' + formatTime(graph.total_hint_seconds);
    }
    note.textContent = label + formatTime(total) + ' total' + extra + hint;
  }
}

function renderFitChart(){
  renderWorkoutGraph(_fitChartCurrent, 'fit_chart', 'fit_chart_note', false);
}

function renderQuickFitGraph(){
  renderWorkoutGraph(_quickFitGraphCurrent, 'quick_fit_graph', 'quick_fit_graph_note', true);
}

window.addEventListener('resize', () => {
  if (_fitChartCurrent) renderFitChart();
  if (_quickFitGraphCurrent) renderQuickFitGraph();
});

window.addEventListener('DOMContentLoaded', loadSecrets);
window.addEventListener('DOMContentLoaded', loadUiState);
window.addEventListener('DOMContentLoaded', loadGarminStatus);
window.addEventListener('DOMContentLoaded', loadLocalFitLibrary);
window.addEventListener('DOMContentLoaded', _restoreGarminUploadStatus);
window.addEventListener('DOMContentLoaded', () => {
  const search = document.getElementById('gc_local_fit_search');
  if (search) search.addEventListener('input', filterGarminLocalFits);
  const localFitList = document.getElementById('gc_local_fit_list');
  if (localFitList){
    localFitList.addEventListener('change', (event) => {
      const target = event.target;
      if (target && target.classList && target.classList.contains('gc-local-fit-check')){
        _updateGarminUploadControls();
      }
    });
    localFitList.addEventListener('click', (event) => {
      const target = event.target;
      if (target && target.classList && target.classList.contains('gc-local-fit-check')){
        window.requestAnimationFrame(_updateGarminUploadControls);
      }
    });
  }
  const deviceFiles = document.getElementById('gc_fit_files');
  if (deviceFiles) deviceFiles.addEventListener('change', _updateGarminUploadControls);
  _updateGarminUploadControls();
});
window.addEventListener('pageshow', () => {
  window.requestAnimationFrame(_updateGarminUploadControls);
});
window.addEventListener('DOMContentLoaded', () => {
  UI_FIELDS.forEach((field) => {
    const nodes = document.querySelectorAll(`[id="${field.id}"]`);
    if (!nodes || !nodes.length) return;
    nodes.forEach((el) => {
      if (!el) return;
      el.addEventListener('change', saveUiState);
      if (el.tagName === 'INPUT' && el.type !== 'checkbox') {
        el.addEventListener('input', saveUiState);
      }
    });
  });
});

window.addEventListener('DOMContentLoaded', () => {
  const peakInput = document.getElementById('plan_peak_mileage');
  if (peakInput){
    peakInput.addEventListener('input', updatePeakRatioNote);
  }
  updatePeakRatioNote();
});

window.addEventListener('DOMContentLoaded', () => {
  const presetSelect = document.getElementById('plan_preset');
  if (presetSelect && presetSelect.tagName === 'SELECT'){
    presetSelect.addEventListener('change', () => applyPlanPreset(presetSelect.value || ''));
  }
  const filterIds = ['plan_filter_search', 'plan_filter_race', 'plan_filter_family', 'plan_filter_weeks', 'plan_filter_mileage'];
  filterIds.forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener(id === 'plan_filter_search' ? 'input' : 'change', renderPlanPresetResults);
  });
  const clear = document.getElementById('plan_preset_clear');
  if (clear) clear.addEventListener('click', () => selectPlanPreset(''));
  loadPlanPresets();
});
let _planJsonCache = null;
let _planPeakMileage = null;
let _planPresets = [];
const DEFAULT_PLAN_PRESET_ID = 'pfitzinger_18wk_up_to_55_3rd_ed.json';

function formatPresetDescription(preset){
  if (!preset) return '';
  const parts = [];
  if (preset.summary) parts.push(preset.summary);
  if (preset.race_label) parts.push(preset.race_label);
  if (preset.reference_peak_mileage) parts.push(`peak ${preset.reference_peak_mileage} mi`);
  if (preset.weeks) parts.push(`${preset.weeks} wks`);
  return parts.join(' · ');
}

function applyPlanPreset(presetId){
  const preset = _planPresets.find((p) => p.id === presetId) || null;
  const desc = document.getElementById('plan_preset_desc');
  const selectedName = document.getElementById('plan_selected_name');
  const status = document.getElementById('plan_status');
  const fileInput = document.getElementById('plan_file');
  const fileLabel = document.getElementById('plan_file_label');
  if (preset){
    if (desc) desc.textContent = formatPresetDescription(preset) || 'Preset selected.';
    if (selectedName) selectedName.textContent = preset.title || preset.id;
    if (status) status.textContent = 'Plan selected. Confirm the race setup, then generate your preview.';
    if (fileInput) fileInput.value = '';
    _planPeakMileage = preset.reference_peak_mileage || null;
  } else {
    if (desc) desc.textContent = 'Use search or filters to narrow the library.';
    if (selectedName) selectedName.textContent = 'Choose a plan from the library';
    _planPeakMileage = null;
  }
  updatePeakRatioNote();
  document.querySelectorAll('.plan-preset-option').forEach((option) => {
    const selected = option.dataset.presetId === presetId;
    option.classList.toggle('is-selected', selected);
    option.setAttribute('aria-selected', selected ? 'true' : 'false');
  });
  const clear = document.getElementById('plan_preset_clear');
  if (clear) clear.hidden = !preset;
}

function presetFamily(preset){
  if (preset && preset.family) return preset.family;
  const text = `${preset && preset.title || ''} ${preset && preset.id || ''}`.toLowerCase();
  if (text.includes('pfitz')) return 'Pfitzinger';
  if (text.includes('hanson')) return 'Hansons';
  if (text.includes('daniels') || text.includes('vdot')) return 'Daniels';
  if (text.includes('davis')) return 'Davis';
  if (text.includes('marathon_excellence') || text.includes('marathon excellence')) return 'Marathon Excellence';
  return 'Other';
}

function selectPlanPreset(presetId){
  const field = document.getElementById('plan_preset');
  if (field) field.value = presetId || '';
  applyPlanPreset(presetId || '');
  renderPlanPresetResults();
  saveUiState();
}

function mileageMatches(peak, band){
  if (!band) return true;
  const miles = Number(peak || 0);
  if (!miles) return false;
  if (band === '40') return miles <= 40;
  if (band === '55') return miles > 40 && miles <= 55;
  if (band === '70') return miles > 55 && miles <= 70;
  return miles > 70;
}

function filteredPlanPresets(){
  const value = (id) => (document.getElementById(id)?.value || '').trim();
  const query = value('plan_filter_search').toLowerCase();
  const race = value('plan_filter_race');
  const family = value('plan_filter_family');
  const weeks = value('plan_filter_weeks');
  const mileage = value('plan_filter_mileage');
  return _planPresets.filter((preset) => {
    const searchable = `${preset.title || ''} ${preset.summary || ''} ${presetFamily(preset)}`.toLowerCase();
    return (!query || searchable.includes(query))
      && (!race || preset.race_distance === race)
      && (!family || presetFamily(preset) === family)
      && (!weeks || String(preset.weeks || '') === weeks)
      && mileageMatches(preset.reference_peak_mileage, mileage);
  });
}

function renderPlanPresetResults(){
  const results = document.getElementById('plan_preset_results');
  if (!results) return;
  const visible = filteredPlanPresets();
  const selectedId = document.getElementById('plan_preset')?.value || '';
  const count = document.getElementById('plan_preset_count');
  if (count) count.textContent = `${visible.length} plan${visible.length === 1 ? '' : 's'}`;
  results.innerHTML = '';
  if (!visible.length){
    const empty = document.createElement('div');
    empty.className = 'plan-preset-empty';
    empty.textContent = 'No plans match these filters.';
    results.appendChild(empty);
    return;
  }
  visible.forEach((preset) => {
    const option = document.createElement('button');
    option.type = 'button';
    option.className = 'plan-preset-option';
    option.dataset.presetId = preset.id;
    option.setAttribute('role', 'option');
    option.setAttribute('aria-selected', preset.id === selectedId ? 'true' : 'false');
    if (preset.id === selectedId) option.classList.add('is-selected');

    const title = document.createElement('strong');
    title.textContent = preset.title || preset.id;
    const meta = document.createElement('span');
    const bits = [presetFamily(preset), preset.race_label, preset.weeks ? `${preset.weeks} weeks` : '', preset.reference_peak_mileage ? `${preset.reference_peak_mileage} mi peak` : ''].filter(Boolean);
    meta.textContent = bits.join(' · ');
    option.append(title, meta);
    option.addEventListener('click', () => selectPlanPreset(preset.id));
    results.appendChild(option);
  });
}

function populatePresetFilterOptions(presets){
  const family = document.getElementById('plan_filter_family');
  const weeks = document.getElementById('plan_filter_weeks');
  if (family){
    const current = family.value;
    family.innerHTML = '<option value="">All families</option>';
    [...new Set(presets.map(presetFamily))].sort().forEach((value) => family.add(new Option(value, value)));
    family.value = current;
  }
  if (weeks){
    const current = weeks.value;
    weeks.innerHTML = '<option value="">Any length</option>';
    [...new Set(presets.map((preset) => preset.weeks).filter(Boolean))].sort((a, b) => a - b).forEach((value) => weeks.add(new Option(`${value} weeks`, String(value))));
    weeks.value = current;
  }
}

function populatePlanPresets(presets){
  const select = document.getElementById('plan_preset');
  if (!select) return;
  const current = select.value;
  if (select.tagName !== 'SELECT'){
    populatePresetFilterOptions(presets);
    if (current && presets.some((preset) => preset.id === current)) {
      select.value = current;
    } else if (presets.some((preset) => preset.id === DEFAULT_PLAN_PRESET_ID)) {
      select.value = DEFAULT_PLAN_PRESET_ID;
    }
    renderPlanPresetResults();
    applyPlanPreset(select.value || '');
    return;
  }
  select.innerHTML = '';
  const opt = document.createElement('option');
  opt.value = '';
  opt.textContent = 'Select a preset plan...';
  select.appendChild(opt);
  presets.forEach((preset) => {
    const option = document.createElement('option');
    option.value = preset.id;
    option.textContent = preset.label || preset.title || preset.id;
    select.appendChild(option);
  });
  if (current) select.value = current;
  else if (presets.some((preset) => preset.id === DEFAULT_PLAN_PRESET_ID)) select.value = DEFAULT_PLAN_PRESET_ID;
  applyPlanPreset(select.value || '');
}

async function loadPlanPresets(){
  try{
    const res = await fetch('/api/plan-presets');
    if (!res.ok) return;
    const data = await res.json();
    _planPresets = Array.isArray(data) ? data : [];
    populatePlanPresets(_planPresets);
  }catch(e){}
}

function _setPlanGarminStatus(message, visible){
  const box = document.getElementById('plan_garmin_status');
  if (!box) return;
  box.style.display = visible === false ? 'none' : 'block';
  box.textContent = message || '';
}

async function uploadGeneratedPlanToGarmin(planToken, filename, options){
  const status = document.getElementById('plan_status');
  _setPlanGarminStatus('Adding generated workouts to Garmin on their calculated plan dates…', true);
  try{
    const res = await fetch('/api/garmin/plan-upload/' + encodeURIComponent(planToken), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(options || {})
    });
    let data = {};
    try{
      data = await res.json();
    }catch(e){
      data = {detail: 'Garmin returned an unreadable response.'};
    }
    if (!res.ok){
      throw new Error(data.detail || data.message || 'Could not add this plan to Garmin');
    }
    const attempted = Number(data.attempted || 0);
    const scheduled = Number(data.scheduled || 0);
    const results = Array.isArray(data.results) ? data.results : [];
    const problems = results
      .filter((item) => !item.ok || !item.scheduled)
      .slice(0, 5)
      .map((item) => {
        const date = item.requestedScheduleDate ? ` (${item.requestedScheduleDate})` : '';
        return `${item.source || 'Workout'}${date}: ${item.error || item.scheduleError || 'not scheduled'}`;
      });
    let detail = data.message || `${scheduled} of ${attempted} workouts added to the Garmin calendar.`;
    if (Number(data.replaced || 0) > 0){
      detail = `Replaced ${Number(data.replaced)} earlier PromptFit workout${Number(data.replaced) === 1 ? '' : 's'}.\n` + detail;
    }
    if (problems.length){
      detail += '\n\nNeeds attention:\n' + problems.join('\n');
      if (results.length - scheduled > problems.length){
        detail += `\n…and ${results.length - scheduled - problems.length} more.`;
      }
    }
    _setPlanGarminStatus(detail, true);
    if (status){
      status.textContent = `Download ready: ${filename}. Garmin calendar: ${scheduled} of ${attempted} workouts scheduled.`;
    }
    loadGarminStatus();
  }catch(err){
    const message = err && err.message ? err.message : String(err);
    _setPlanGarminStatus(
      `The calendar package downloaded, but Garmin was not updated.\n${message}`,
      true
    );
    if (status) status.textContent = `Download ready: ${filename}. Garmin calendar needs attention.`;
    loadGarminStatus();
  }
}

async function generatePlan(){
  const status = document.getElementById('plan_status');
  const fileInput = document.getElementById('plan_file');
  const presetSelect = document.getElementById('plan_preset');
  if (!status || !fileInput) return;
  const presetId = presetSelect ? presetSelect.value : '';
  const hasFile = !!(fileInput.files && fileInput.files.length);
  const activeSource = document.querySelector('[data-plan-source].is-active')?.dataset.planSource || 'preset';
  if (activeSource === 'preset' && !presetId){
    status.textContent = 'Choose a plan from the library first.';
    return;
  }
  if (activeSource !== 'preset' && !hasFile){
    status.textContent = activeSource === 'text'
      ? 'Build the written plan into JSON first.'
      : 'Choose a JSON plan file first.';
    return;
  }
  const scheduleGarmin = !!((document.getElementById('plan_schedule_garmin') || {}).checked);
  const fitScope = (document.getElementById('plan_fit_scope') || {}).value || 'workouts';
  const packageMode = (document.getElementById('plan_package_mode') || {}).value || 'full';
  if (scheduleGarmin && fitScope === 'none'){
    status.textContent = 'Choose workout days or all running days under FIT file scope before uploading to Garmin.';
    return;
  }
  _setPlanGarminStatus('', false);
  status.textContent = 'Generating calendar...';
  try{
    const fd = new FormData();
    if (activeSource !== 'preset' && hasFile) fd.append('plan_file', fileInput.files[0]);
    if (activeSource === 'preset' && presetId) fd.append('plan_preset', presetId);
    fd.append('race_date', document.getElementById('plan_race_date').value);
    fd.append('race_distance', document.getElementById('plan_race_distance').value);
    fd.append('race_pace', document.getElementById('plan_race_pace').value);
    fd.append('easy_pace', document.getElementById('plan_easy_pace').value);
    fd.append('pace_profile', JSON.stringify(collectPaceProfile()));
    fd.append('peak_mileage', document.getElementById('plan_peak_mileage').value);
    fd.append('base_name', document.getElementById('plan_base_name').value);
    fd.append('include_wu_cd', document.getElementById('plan_include_wu').checked ? 'true' : '');
    fd.append('scale_wu_cd', document.getElementById('plan_scale_wu').checked ? 'true' : '');
    fd.append('collapse_doubles', document.getElementById('plan_collapse_doubles').checked ? 'true' : '');
    fd.append('consolidate_workouts', document.getElementById('plan_consolidate_workouts').checked ? 'true' : '');
    fd.append('generate_fits', fitScope === 'none' ? 'false' : 'true');
    fd.append('include_easy_fits', fitScope === 'all_runs' ? 'true' : '');
    fd.append('package_mode', packageMode);
    fd.append('wu_cd_distance', document.getElementById('plan_wu_cd_distance').value);
    fd.append('wu_cd_duration', document.getElementById('plan_wu_cd_duration').value);
    fd.append('rest_days', document.getElementById('plan_rest_days').value);
    fd.append('redistribute', document.getElementById('plan_redistribute').checked ? 'true' : 'false');
    fd.append('normalize', document.getElementById('plan_normalize').checked ? 'true' : 'false');
    fd.append('norm_reduce', document.getElementById('plan_norm_reduce').checked ? 'true' : '');
    fd.append('wf_mode', document.getElementById('plan_wf_mode').value);
    fd.append('wf_value', document.getElementById('plan_wf_value').value);

    const res = await fetch('/api/generate-plan', { method: 'POST', body: fd });
    if (!res.ok){
      status.textContent = 'Error: ' + (await res.text());
      return;
    }
    let filename = 'training_plan.zip';
    const previewLink = document.getElementById('plan_preview_link');
    if (previewLink) previewLink.style.display = 'none';
    try{
      const disp = res.headers.get('Content-Disposition') || '';
      const part = disp.split('filename=')[1];
      if (part) filename = part.replace(/"/g,'');
    }catch(e){}
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const packageDownload = document.getElementById('plan_package_download');
    const previousPackageUrl = packageDownload ? packageDownload.dataset.objectUrl : '';
    if (previousPackageUrl) URL.revokeObjectURL(previousPackageUrl);
    if (packageDownload){
      packageDownload.href = url;
      packageDownload.download = filename;
      packageDownload.dataset.objectUrl = url;
    }
    status.textContent = 'Plan ready. Review the complete schedule below or choose a download.';
    const previewUrl = res.headers.get('X-Plan-Preview') || '';
    const planToken = res.headers.get('X-Plan-Token') || '';
    if (previewUrl && previewLink){
      previewLink.style.display = 'flex';
    }
    if (previewUrl){
      const output = document.getElementById('plan_output');
      const frame = document.getElementById('plan_preview_frame');
      const htmlDownload = document.getElementById('plan_html_download');
      const openPreview = document.getElementById('plan_open_preview');
      if (htmlDownload) htmlDownload.href = `${previewUrl}/download`;
      if (openPreview) openPreview.href = previewUrl;
      if (output) output.hidden = false;
      if (frame){
        frame.onload = () => {
          try{
            const doc = frame.contentDocument;
            if (!doc) return;
            const resize = () => {
              const height = Math.max(doc.documentElement.scrollHeight, doc.body ? doc.body.scrollHeight : 0);
              if (height > 0) frame.style.height = `${height}px`;
            };
            resize();
            if (window.ResizeObserver){
              if (frame._promptFitResizeObserver) frame._promptFitResizeObserver.disconnect();
              frame._promptFitResizeObserver = new ResizeObserver(resize);
              frame._promptFitResizeObserver.observe(doc.documentElement);
            }
          }catch(e){}
        };
        frame.src = `${previewUrl}?embed=1`;
      }
      if (output) output.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    if (scheduleGarmin){
      if (planToken){
        const weeks = parseInt((document.getElementById('plan_garmin_weeks') || {}).value || '4', 10);
        const replace = !!((document.getElementById('plan_garmin_replace') || {}).checked);
        await uploadGeneratedPlanToGarmin(planToken, filename, {
          weeks: Number.isFinite(weeks) ? weeks : 4,
          replace: replace
        });
      }else{
        _setPlanGarminStatus(
          'The calendar package downloaded, but its generated workouts are no longer available for Garmin upload.',
          true
        );
        status.textContent = `Download ready: ${filename}. Garmin calendar was not updated.`;
      }
    }
  }catch(err){
    status.textContent = 'Error: ' + (err && err.message ? err.message : String(err));
  }
}

function resetPlanForm(){
  const status = document.getElementById('plan_status');
  if (status) status.textContent = 'Choose a source, confirm the race setup, then generate.';
  _setPlanGarminStatus('', false);
  const output = document.getElementById('plan_output');
  if (output) output.hidden = true;
  const frame = document.getElementById('plan_preview_frame');
  if (frame){
    if (frame._promptFitResizeObserver) frame._promptFitResizeObserver.disconnect();
    frame.removeAttribute('src');
    frame.style.height = '';
  }
  const label = document.getElementById('plan_file_label');
  if (label) label.textContent = 'Drag & drop a JSON plan here';
  const ids = [
    'plan_file','plan_race_date','plan_race_distance','plan_preset','plan_peak_mileage','plan_race_pace',
    'plan_easy_pace','plan_base_name','plan_include_wu','plan_scale_wu','plan_collapse_doubles',
    'plan_consolidate_workouts','plan_fit_scope','plan_package_mode','plan_schedule_garmin','plan_garmin_weeks','plan_garmin_replace','plan_wu_cd_distance','plan_wu_cd_duration',
    'plan_rest_days','plan_redistribute','plan_normalize','plan_norm_reduce','plan_wf_mode','plan_wf_value'
  ];
  ids.forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (el.type === 'checkbox') el.checked = false;
    else if (el.type === 'file') el.value = '';
    else if (el.tagName === 'SELECT') {
      if (id === 'plan_race_distance') el.value = 'half marathon';
      else if (id === 'plan_wf_mode') el.value = 'same';
      else if (id === 'plan_fit_scope') el.value = 'workouts';
      else if (id === 'plan_package_mode') el.value = 'full';
    } else if (id === 'plan_base_name') el.value = 'training_plan';
    else if (id === 'plan_rest_days') el.value = '0';
    else if (id === 'plan_garmin_weeks') el.value = '4';
    else el.value = '';
  });
  const redistrib = document.getElementById('plan_redistribute');
  const norm = document.getElementById('plan_normalize');
  if (redistrib) redistrib.checked = true;
  if (norm) norm.checked = true;
  const replaceGarmin = document.getElementById('plan_garmin_replace');
  if (replaceGarmin) replaceGarmin.checked = true;
  _planPeakMileage = null;
  applyPlanPreset('');
  updatePeakRatioNote();
  saveUiState();
}

function triggerPlanFile(){
  const inp = document.getElementById('plan_file');
  if (inp) inp.click();
}

function updatePlanFileLabel(file){
  const label = document.getElementById('plan_file_label');
  if (!label) return;
  if (!file){
    label.textContent = 'Drop a JSON plan here';
    return;
  }
  label.textContent = file.name || 'Plan JSON selected';
  const status = document.getElementById('plan_status');
  if (status) status.textContent = 'JSON plan selected. Confirm the race setup, then generate your preview.';
}

function updatePeakRatioNote(){
  const note = document.getElementById('plan_wf_ratio_note');
  if (!note) return;
  const peakInput = document.getElementById('plan_peak_mileage');
  const userPeak = peakInput ? parseFloat(peakInput.value) : NaN;
  if (!_planPeakMileage){
    note.textContent = 'Peak ratio: —';
    return;
  }
  if (!isFinite(userPeak) || userPeak <= 0){
    note.textContent = `Plan peak: ${_planPeakMileage} mi (enter target peak to see ratio)`;
    return;
  }
  const ratio = userPeak / _planPeakMileage;
  const pct = Math.round(ratio * 100);
  note.textContent = `Peak ratio: ${userPeak} / ${_planPeakMileage} = ${ratio.toFixed(2)} (${pct}%)`;
}

function updatePlanBaseLabelFromJson(jsonText){
  try{
    const data = JSON.parse(jsonText);
    const meta = data.plan_meta || {};
    let peak = meta.reference_peak_mileage || meta.peak_mileage || meta.base_peak_mileage || '';
    let parsed = null;
    if (typeof peak === 'number') parsed = peak;
    else if (peak){
      const m = String(peak).match(/(\\d+(?:\\.\\d+)?)/);
      if (m) parsed = parseFloat(m[1]);
    }
    _planPeakMileage = (parsed && isFinite(parsed)) ? parsed : null;
    updatePeakRatioNote();
  }catch(e){}
}

async function buildPlanJson(){
  const out = document.getElementById('plan_json_out');
  const promptEl = document.getElementById('plan_text');
  if (!out || !promptEl) return;
  const prompt = promptEl.value || '';
  if (!prompt.trim()){
    out.textContent = 'Paste a plan to generate JSON.';
    return;
  }
  out.textContent = 'Building JSON...';
  try{
    const res = await fetch('/api/plan-text-to-json', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        prompt: prompt,
        provider: (document.getElementById('plan_provider') || {}).value || 'auto',
        openai_api_key: (document.getElementById('openai_api_key') || {}).value || '',
        openai_model: (document.getElementById('openai_model') || {}).value || '',
        openrouter_api_key: (document.getElementById('openrouter_api_key') || {}).value || '',
        openrouter_model: (document.getElementById('openrouter_model') || {}).value || '',
        race_distance: (document.getElementById('plan_race_distance') || {}).value || '',
        paces: collectPaceProfile()
      })
    });
    const txt = await res.text();
    if (!res.ok){
      out.textContent = 'Error: ' + txt;
      return;
    }
    let obj = null;
    try{
      obj = JSON.parse(txt);
    }catch(e){
      out.textContent = txt;
      return;
    }
    _planJsonCache = obj;
    out.textContent = JSON.stringify(obj, null, 2);
    updatePlanBaseLabelFromJson(out.textContent);

    // Keep the written-plan workflow on one page: prepare the generated JSON
    // as the selected plan file so the next action can generate the calendar.
    try{
      const base = (document.getElementById('plan_base_name') || {}).value || 'training_plan';
      const filename = base.replace(/\s+/g, '_') + '.json';
      const file = new File([JSON.stringify(obj, null, 2)], filename, {type: 'application/json'});
      const input = document.getElementById('plan_file');
      if (input && typeof DataTransfer !== 'undefined'){
        const dt = new DataTransfer();
        dt.items.add(file);
        input.files = dt.files;
        updatePlanFileLabel(file);
      }
      const preset = document.getElementById('plan_preset');
      if (preset){
        preset.value = '';
        applyPlanPreset('');
      }
      const status = document.getElementById('plan_status');
      if (status) status.textContent = 'Written plan is structured and ready. Review race setup, then generate the calendar package.';
    }catch(e){}
  }catch(err){
    out.textContent = 'Error: ' + (err && err.message ? err.message : String(err));
  }
}

function downloadPlanJson(){
  if (!_planJsonCache) return;
  try{
    const base = (document.getElementById('plan_base_name') || {}).value || 'training_plan';
    const blob = new Blob([JSON.stringify(_planJsonCache, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = base.replace(/\\s+/g, '_') + '.json';
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  }catch(e){}
}

window.addEventListener('DOMContentLoaded', () => {
  const dz = document.getElementById('fit_dropzone');
  const inp = document.getElementById('fit_files');
  if (!dz || !inp) return;

  const addHighlight = () => dz.classList.add('dragover');
  const removeHighlight = () => dz.classList.remove('dragover');

  dz.addEventListener('dragover', (e) => {
    e.preventDefault();
    addHighlight();
  });
  dz.addEventListener('dragleave', (e) => {
    e.preventDefault();
    removeHighlight();
  });
  dz.addEventListener('drop', (e) => {
    e.preventDefault();
    removeHighlight();
    const files = e.dataTransfer && e.dataTransfer.files ? e.dataTransfer.files : null;
    if (files && files.length) {
      try {
        const dt = new DataTransfer();
        for (const f of files) dt.items.add(f);
        inp.files = dt.files;
      } catch (err) {
        // Fallback: browser may block programmatic assignment; just parse directly
      }
      parseFits();
    }
  });
});

window.addEventListener('DOMContentLoaded', () => {
  const dz = document.getElementById('plan_dropzone');
  const inp = document.getElementById('plan_file');
  const status = document.getElementById('plan_status');
  const presetSelect = document.getElementById('plan_preset');
  if (!dz || !inp) return;

  const addHighlight = () => dz.classList.add('dragover');
  const removeHighlight = () => dz.classList.remove('dragover');

  dz.addEventListener('dragover', (e) => {
    e.preventDefault();
    addHighlight();
  });
  dz.addEventListener('dragleave', (e) => {
    e.preventDefault();
    removeHighlight();
  });
  dz.addEventListener('drop', (e) => {
    e.preventDefault();
    removeHighlight();
    const files = e.dataTransfer && e.dataTransfer.files ? e.dataTransfer.files : null;
    if (files && files.length) {
      try {
        const dt = new DataTransfer();
        for (const f of files) dt.items.add(f);
        inp.files = dt.files;
      } catch (err) {}
      updatePlanFileLabel(files[0]);
      if (presetSelect) {
        presetSelect.value = '';
        applyPlanPreset('');
      }
      try{
        const reader = new FileReader();
        reader.onload = () => updatePlanBaseLabelFromJson(reader.result || '');
        reader.readAsText(files[0]);
      }catch(e){}
    }
  });

  dz.addEventListener('click', (e) => {
    if (e.target && e.target.tagName === 'BUTTON') return;
    if (e.target && e.target.tagName === 'INPUT') return;
    triggerPlanFile();
  });

  inp.addEventListener('change', () => {
    const file = inp.files && inp.files.length ? inp.files[0] : null;
    updatePlanFileLabel(file);
    if (presetSelect) {
      presetSelect.value = '';
      applyPlanPreset('');
    }
    if (file){
      try{
        const reader = new FileReader();
        reader.onload = () => updatePlanBaseLabelFromJson(reader.result || '');
        reader.readAsText(file);
      }catch(e){}
    }
  });

  const bindDropTarget = (el) => {
    if (!el) return;
    el.addEventListener('dragover', (e) => {
      e.preventDefault();
      addHighlight();
    });
    el.addEventListener('dragleave', (e) => {
      e.preventDefault();
      removeHighlight();
    });
    el.addEventListener('drop', (e) => {
      e.preventDefault();
      removeHighlight();
      const files = e.dataTransfer && e.dataTransfer.files ? e.dataTransfer.files : null;
      if (files && files.length) {
        try {
          const dt = new DataTransfer();
          for (const f of files) dt.items.add(f);
          inp.files = dt.files;
        } catch (err) {}
        updatePlanFileLabel(files[0]);
      }
    });
  };

  bindDropTarget(status);
});
