"""Unified PromptFit Studio page.

The application used to expose the workout composer, plan builder, and FIT
editor as three separate pages.  Keeping the shell here lets the backend stay
focused on conversion while all workflows share one predictable workspace.
"""

STUDIO_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#102b27">
  <meta name="description" content="Build running plans, create and edit Garmin FIT workouts, and deliver them to your calendar or Garmin Connect.">
  <title>PromptFit Studio</title>
  <link rel="stylesheet" href="/static/studio.css?v=__ASSET_VERSION__">
</head>
<body>
  <a class="skip-link" href="#workspace">Skip to workspace</a>

  <header class="app-header">
    <a class="brand-lockup" href="#start" aria-label="PromptFit Studio home">
      <img class="brand-mark" src="/app-icon.png" alt="" width="38" height="38">
      <span>
        <strong>PromptFit</strong>
        <small>Running plan studio</small>
      </span>
    </a>
    <nav class="top-nav" aria-label="Primary navigation">
      <a href="#workout">Quick workout</a>
      <a href="#plan">Full plan</a>
      <a href="#editor">FIT editor</a>
      <a href="#deliver">Deliver</a>
    </nav>
    <a class="header-action" href="#settings">Settings</a>
  </header>

  <div class="app-shell">
    <aside class="side-rail" aria-label="Workflow">
      <p class="rail-label">Workflow</p>
      <a class="rail-link is-active" href="#start" data-section-link="start">
        <span>01</span><strong>Start</strong>
      </a>
      <a class="rail-link" href="#workout" data-section-link="workout">
        <span>02</span><strong>Build</strong>
      </a>
      <a class="rail-link" href="#editor" data-section-link="editor">
        <span>03</span><strong>Review</strong>
      </a>
      <a class="rail-link" href="#deliver" data-section-link="deliver">
        <span>04</span><strong>Deliver</strong>
      </a>
      <div class="rail-note">
        <span class="status-dot" aria-hidden="true"></span>
        <span>Local-first<br><small>Nothing uploads automatically</small></span>
      </div>
    </aside>

    <main id="workspace" class="workspace">
      <section id="start" class="hero-section observe-section" aria-labelledby="start-title">
        <div class="eyebrow">One workspace. Every mile.</div>
        <h1 id="start-title">From training idea to watch-ready workout.</h1>
        <p class="hero-copy">Create one workout, shape an entire race plan, or fine-tune an existing FIT file—then download it or send only the workouts you choose.</p>

        <div class="path-grid" aria-label="Choose how to begin">
          <a class="path-card path-card-primary" href="#workout">
            <span class="path-number">01</span>
            <span class="path-icon" aria-hidden="true">↗</span>
            <strong>Make a workout</strong>
            <small>Describe a session in plain language and get a Garmin-ready FIT.</small>
            <span class="path-cta">Start with a prompt <b>→</b></span>
          </a>
          <a class="path-card" href="#plan">
            <span class="path-number">02</span>
            <span class="path-icon" aria-hidden="true">≋</span>
            <strong>Build a race plan</strong>
            <small>Use a preset, import JSON, or convert written weeks into a full calendar.</small>
            <span class="path-cta">Open plan builder <b>→</b></span>
          </a>
          <a class="path-card" href="#editor">
            <span class="path-number">03</span>
            <span class="path-icon" aria-hidden="true">⌁</span>
            <strong>Edit a FIT file</strong>
            <small>Inspect every leg, reorder repeats, preview pace, and export cleanly.</small>
            <span class="path-cta">Open FIT editor <b>→</b></span>
          </a>
        </div>

        <div class="trust-row" aria-label="Product principles">
          <span><b>Local-first</b> keys and files stay on this computer</span>
          <span><b>Garmin-aware</b> repeats and pace targets are preserved</span>
          <span><b>Manual delivery</b> you choose exactly what leaves the app</span>
        </div>
      </section>

      <section id="workout" class="workflow-section observe-section" aria-labelledby="workout-title">
        <div class="section-heading">
          <div>
            <span class="step-kicker">Build · Quick workout</span>
            <h2 id="workout-title">Describe the session you want to run.</h2>
            <p>Natural language works well. Include distance or time, pace, recovery, and repeats when they matter.</p>
          </div>
          <span class="section-badge">Prompt → FIT</span>
        </div>

        <div class="split-layout split-layout-wide">
          <div class="surface surface-main">
            <label class="field workout-preset-field">
              <span>Enter your own workout, or choose a preset below to get started</span>
              <select id="workout_preset" onchange="applyWorkoutPreset(this.value)">
                <option value="">Choose a natural-language workout…</option>
                <option value="easy_strides">45-minute easy run with relaxed strides</option>
                <option value="progression">60-minute progression run</option>
                <option value="threshold_cruise">Threshold cruise intervals</option>
                <option value="six_by_800">6 × 800 meters at 5K effort</option>
                <option value="ten_k_repeats">5 × 5 minutes at 10K effort</option>
                <option value="hill_repeats">Short hill repeats with jog-down recovery</option>
                <option value="fartlek">10 × 1-minute fartlek</option>
                <option value="long_fast_finish">Long run with a fast finish</option>
                <option value="race_sharpening">Race-week sharpening session</option>
              </select>
              <small>Choosing a preset fills the description below. Edit any wording before generating.</small>
            </label>

            <label class="field field-prompt">
              <span>Workout description</span>
              <textarea id="prompt" placeholder="Example: 15 min easy, then 6 × 3 min at 10K effort with 90 sec easy jog, finish with 10 min easy."></textarea>
              <small>Tip: write it the way a coach would explain it.</small>
            </label>

            <div class="field-grid field-grid-3">
              <label class="field">
                <span>Race distance</span>
                <select id="race_distance">
                  <option value="5k">5K</option>
                  <option value="10k">10K</option>
                  <option value="half marathon" selected>Half marathon</option>
                  <option value="marathon">Marathon</option>
                </select>
              </label>
              <label class="field">
                <span>Reference race pace</span>
                <input id="hmp" type="text" placeholder="6:30 /mi" inputmode="decimal">
              </label>
              <label class="field">
                <span>AI provider</span>
                <select id="provider">
                  <option value="auto">Choose automatically</option>
                  <option value="openai">OpenAI</option>
                  <option value="openrouter">OpenRouter</option>
                </select>
              </label>
            </div>

            <details class="disclosure pace-profile-disclosure">
              <summary>
                <span><b>Your pace profile</b><small>Add any paces you know · saved automatically on this device</small></span>
                <span class="summary-icon" aria-hidden="true">+</span>
              </summary>
              <div class="disclosure-body">
                <p class="pace-profile-note">The reference pace above applies to the selected race. Add as many exact anchors as you know; entered paces always take priority over estimates.</p>
                <div class="field-grid field-grid-2 pace-profile-grid">
                  <label class="field"><span>Easy / conversational</span><input id="pace_easy" type="text" placeholder="8:15 /mi" inputmode="decimal"></label>
                  <label class="field"><span>Marathon pace</span><input id="pace_marathon" type="text" placeholder="7:05 /mi" inputmode="decimal"></label>
                  <label class="field"><span>Half marathon pace</span><input id="pace_half_marathon" type="text" placeholder="6:45 /mi" inputmode="decimal"></label>
                  <label class="field"><span>Lactate threshold / T</span><input id="pace_threshold" type="text" placeholder="6:38 /mi" inputmode="decimal"></label>
                  <label class="field"><span>10K pace</span><input id="pace_10k" type="text" placeholder="6:30 /mi" inputmode="decimal"></label>
                  <label class="field"><span>5K pace</span><input id="pace_5k" type="text" placeholder="6:12 /mi" inputmode="decimal"></label>
                  <label class="field"><span>3K pace</span><input id="pace_3k" type="text" placeholder="6:00 /mi" inputmode="decimal"></label>
                  <label class="field"><span>Mile / repetition pace</span><input id="pace_mile" type="text" placeholder="5:40 /mi" inputmode="decimal"></label>
                </div>
              </div>
            </details>

            <details class="disclosure">
              <summary>
                <span><b>Pace targets & watch options</b><small>Optional controls for experienced users</small></span>
                <span class="summary-icon" aria-hidden="true">+</span>
              </summary>
              <div class="disclosure-body">
                <div class="field-grid field-grid-2">
                  <label class="field">
                    <span>Target margin</span>
                    <div class="input-suffix">
                      <input id="margin" type="text" value="30" inputmode="numeric">
                      <span>sec/mi</span>
                    </div>
                  </label>
                  <label class="field">
                    <span>Target display</span>
                    <select id="tmode">
                      <option value="pace">Pace</option>
                      <option value="speed">Speed</option>
                    </select>
                  </label>
                </div>
                <div class="choice-row">
                  <label class="choice"><input id="targets" type="checkbox"><span><b>Add pace targets</b><small>Include target ranges in each supported step.</small></span></label>
                </div>
              </div>
            </details>

            <div class="primary-actions">
              <button class="btn btn-primary" id="generate_fit_btn" type="button" onclick="run()">Generate FIT for review</button>
              <button class="btn btn-secondary" type="button" onclick="previewPlan()">Show interpreted JSON</button>
              <a class="text-action" href="#settings">AI settings</a>
            </div>
          </div>

          <aside class="surface output-panel" aria-label="Quick workout result">
            <div class="panel-heading">
              <div>
                <span class="mini-label">Result</span>
                <h3>Workout preview</h3>
              </div>
              <span class="live-indicator"><i aria-hidden="true"></i> Ready</span>
            </div>
            <div id="quick_fit_graph_wrap" class="quick-fit-graph">
              <div class="quick-fit-graph-head">
                <span>Pace over time</span>
                <span>Faster ↑</span>
              </div>
              <canvas id="quick_fit_graph" height="230" role="img" aria-label="Generated workout pace graph"></canvas>
              <div id="quick_fit_graph_note">Generate a FIT to see its workout graph here.</div>
            </div>
            <div id="fit_review_card" class="fit-review-card hidden" aria-live="polite">
              <div class="fit-review-heading">
                <div>
                  <span class="mini-label">Review decision</span>
                  <strong id="fit_review_name">Workout</strong>
                </div>
                <span id="fit_review_badge" class="review-badge">Awaiting review</span>
              </div>
              <p id="fit_review_detail">Review the workout, then approve it or return to the editor to make changes.</p>
              <div id="fit_review_decision" class="inline-actions">
                <button class="btn btn-primary" id="fit_approve_btn" type="button" onclick="approvePendingFit()">Approve & queue</button>
                <button class="btn btn-secondary" type="button" onclick="modifyPendingFit()">Modify in editor</button>
              </div>
              <div id="fit_review_delivery" class="inline-actions hidden">
                <button class="btn btn-primary" id="fit_review_download_btn" type="button" onclick="downloadApprovedFit()">Download FIT</button>
                <button class="btn btn-secondary" id="fit_review_garmin_btn" type="button" onclick="uploadApprovedFitToGarmin()">Upload this FIT to Garmin</button>
                <a class="text-action" href="#deliver">View selected queue</a>
              </div>
              <div id="fit_review_garmin_feedback" class="top-garmin-feedback hidden" role="status" aria-live="polite">
                <div id="top_gc_connection_detail" class="garmin-connection-detail">
                  <span class="garmin-state-icon" id="top_gc_connection_icon" aria-hidden="true">…</span>
                  <div class="garmin-state-copy">
                    <strong id="top_gc_connection_title">Checking Garmin connection…</strong>
                    <span id="top_gc_connection_note">Confirming the saved connection on this computer.</span>
                    <span class="garmin-state-meta" id="top_gc_connection_meta"></span>
                  </div>
                </div>
                <div id="top_gc_upload_status" class="garmin-upload-status">
                  <span class="garmin-state-icon" id="top_gc_upload_icon" aria-hidden="true">↑</span>
                  <div class="garmin-state-copy">
                    <strong id="top_gc_upload_title">Preparing upload…</strong>
                    <span id="top_gc_upload_detail">Keep this page open until Garmin confirms the workout.</span>
                    <span class="garmin-state-meta" id="top_gc_upload_meta"></span>
                    <div class="garmin-upload-results" id="top_gc_upload_results"></div>
                  </div>
                </div>
              </div>
            </div>
            <details id="workout_json_details" class="disclosure output-disclosure">
              <summary>
                <span><b>Interpreted JSON</b><small>Optional technical view of the workout structure</small></span>
                <span class="summary-icon" aria-hidden="true">+</span>
              </summary>
              <div class="disclosure-body">
                <div id="plan_json" class="log empty-state" data-empty="Choose Show interpreted JSON to inspect the workout structure."></div>
              </div>
            </details>
            <div class="status-block">
              <span class="mini-label">Activity</span>
              <div id="log" class="log compact-log" role="status" aria-live="polite">Ready for a workout description.</div>
            </div>
            <p class="privacy-note">Nothing downloads or uploads automatically. Approving a reviewed FIT adds it to your local delivery queue.</p>
          </aside>
        </div>
      </section>

      <section id="plan" class="workflow-section observe-section" aria-labelledby="plan-title">
        <div class="section-heading">
          <div>
            <span class="step-kicker">Build · Full plan</span>
            <h2 id="plan-title">Turn a plan into a calendar and workout library.</h2>
            <p>Start from a proven preset, bring your own JSON, or convert a written plan. Set the race once and export the whole package.</p>
          </div>
          <span class="section-badge">ICS + HTML + FIT</span>
        </div>

        <div class="source-switcher" role="group" aria-label="Plan source">
          <button class="source-tab is-active" type="button" data-plan-source="preset">Plan library</button>
          <button class="source-tab" type="button" data-plan-source="upload">Upload JSON</button>
          <button class="source-tab" type="button" data-plan-source="text">Written plan</button>
        </div>

        <div class="plan-builder-layout">
          <div class="surface surface-main">
            <div data-plan-panel="preset">
              <div class="plan-preset-field">
                <div class="plan-preset-picker">
                  <div class="plan-library-controls">
                    <label class="plan-preset-search">
                      <span class="visually-hidden">Search preset plans</span>
                      <input id="plan_filter_search" type="search" placeholder="Search plans…" autocomplete="off">
                    </label>
                    <div class="plan-filter-grid" aria-label="Filter preset plans">
                      <label>
                        <span>Race</span>
                        <select id="plan_filter_race">
                          <option value="">All races</option>
                          <option value="5k">5K</option>
                          <option value="10k">10K</option>
                          <option value="half marathon">Half marathon</option>
                          <option value="marathon">Marathon</option>
                        </select>
                      </label>
                      <label>
                        <span>Family</span>
                        <select id="plan_filter_family"><option value="">All families</option></select>
                      </label>
                      <label>
                        <span>Length</span>
                        <select id="plan_filter_weeks"><option value="">Any length</option></select>
                      </label>
                      <label>
                        <span>Peak mileage</span>
                        <select id="plan_filter_mileage">
                          <option value="">Any mileage</option>
                          <option value="40">Up to 40 mi</option>
                          <option value="55">41–55 mi</option>
                          <option value="70">56–70 mi</option>
                          <option value="71">71+ mi</option>
                        </select>
                      </label>
                    </div>
                  </div>
                  <div class="plan-results-heading">
                    <span id="plan_preset_count">Loading plans…</span>
                    <button id="plan_preset_clear" class="text-button" type="button" hidden>Clear selection</button>
                  </div>
                  <input id="plan_preset" type="hidden" value="">
                  <div id="plan_preset_results" class="plan-preset-results" role="listbox" aria-label="Preset plans">
                    <div class="plan-preset-empty">Loading preset plans…</div>
                  </div>
                </div>
                <div class="selected-plan-bar">
                  <span class="selected-plan-check" aria-hidden="true">✓</span>
                  <div>
                    <span class="mini-label">Selected plan</span>
                    <strong id="plan_selected_name">Choose a plan from the library</strong>
                    <small id="plan_preset_desc">Use search or filters to narrow the library.</small>
                  </div>
                </div>
              </div>
            </div>

            <div data-plan-panel="upload" hidden>
              <div class="plan-upload-layout">
                <div>
                  <span class="mini-label">Bring your own structure</span>
                  <h3>Upload a plan JSON.</h3>
                  <p>Use a previously exported PromptFit plan or another compatible plan file. We’ll read its weeks and workouts, then apply the race setup below.</p>
                </div>
                <div id="plan_dropzone" class="dropzone plan-dropzone-large">
                  <input id="plan_file" class="visually-hidden-input" type="file" accept=".json">
                  <span class="drop-icon" aria-hidden="true">⇧</span>
                  <span id="plan_file_label">Drop a JSON plan here</span>
                  <button class="btn btn-secondary" type="button" onclick="triggerPlanFile()">Choose JSON file</button>
                </div>
              </div>
            </div>

            <div data-plan-panel="text" hidden>
              <label class="field field-prompt">
                <span>Written training plan</span>
                <textarea id="plan_text" placeholder="Week 1: Monday 45 min easy, Tuesday 6 × 800 m…"></textarea>
                <small>Weeks, days, and workout details can be informal—the builder will structure them.</small>
              </label>
              <div class="field-grid field-grid-2">
                <label class="field">
                  <span>AI provider</span>
                  <select id="plan_provider">
                    <option value="auto">Choose automatically</option>
                    <option value="openai">OpenAI</option>
                    <option value="openrouter">OpenRouter</option>
                  </select>
                </label>
                <div class="field action-field">
                  <span>Convert to plan JSON</span>
                  <div class="inline-actions">
                    <button class="btn btn-primary" type="button" onclick="buildPlanJson()">Build JSON</button>
                    <button class="btn btn-secondary" type="button" onclick="downloadPlanJson()">Download JSON</button>
                  </div>
                </div>
              </div>
              <div id="plan_json_out" class="log empty-state code-log" data-empty="Structured plan JSON will appear here."></div>
              <p class="helper-banner">After building JSON, it is selected automatically for the calendar package. Review race setup below, then generate.</p>
            </div>

            <div class="subsection-title">
              <div>
                <span class="mini-label">Race setup</span>
                <h3>Anchor the plan to race day</h3>
              </div>
            </div>
            <div class="field-grid field-grid-3">
              <label class="field">
                <span>Race date</span>
                <input id="plan_race_date" type="date">
              </label>
              <label class="field">
                <span>Race distance</span>
                <select id="plan_race_distance">
                  <option value="5k">5K</option>
                  <option value="10k">10K</option>
                  <option value="half marathon" selected>Half marathon</option>
                  <option value="marathon">Marathon</option>
                </select>
              </label>
              <label class="field">
                <span>Target peak mileage</span>
                <div class="input-suffix">
                  <input id="plan_peak_mileage" type="text" placeholder="70" inputmode="decimal">
                  <span>mi/wk</span>
                </div>
              </label>
              <label class="field">
                <span>Race pace</span>
                <input id="plan_race_pace" type="text" placeholder="6:30 /mi">
              </label>
              <label class="field">
                <span>Easy pace <em>optional</em></span>
                <input id="plan_easy_pace" type="text" placeholder="8:15 /mi">
              </label>
              <label class="field">
                <span>Package name</span>
                <input id="plan_base_name" type="text" value="training_plan">
              </label>
            </div>

            <details class="disclosure">
              <summary>
                <span><b>Plan scaling & workout rules</b><small>Warmups, doubles, mileage normalization, and FIT coverage</small></span>
                <span class="summary-icon" aria-hidden="true">+</span>
              </summary>
              <div class="disclosure-body">
                <div class="choice-grid">
                  <label class="choice"><input id="plan_include_wu" type="checkbox"><span><b>Add implicit warmup/cooldown</b><small>Use about one mile when the plan does not specify it.</small></span></label>
                  <label class="choice"><input id="plan_scale_wu" type="checkbox"><span><b>Scale explicit warmup/cooldown</b><small>Adjust named WU/CD segments with mileage.</small></span></label>
                  <label class="choice"><input id="plan_collapse_doubles" type="checkbox"><span><b>Collapse double days</b><small>Combine two daily entries where possible.</small></span></label>
                  <label class="choice"><input id="plan_consolidate_workouts" type="checkbox"><span><b>Combine workout doubles</b><small>Merge quality sessions into one workout.</small></span></label>
                </div>

                <div class="field-grid field-grid-3">
                  <label class="field">
                    <span>FIT file scope</span>
                    <select id="plan_fit_scope">
                      <option value="workouts" selected>Workout days only</option>
                      <option value="all_runs">All running days</option>
                      <option value="none">Do not generate FIT files</option>
                    </select>
                    <small>Workout days only is the cleanest Garmin library.</small>
                  </label>
                  <label class="field">
                    <span>Download contents</span>
                    <select id="plan_package_mode">
                      <option value="full" selected>Calendar + preview + FITs</option>
                      <option value="fits">Workout FITs only</option>
                      <option value="calendar">Calendar + preview only</option>
                    </select>
                  </label>
                  <label class="field">
                    <span>WU + CD distance</span>
                    <div class="input-suffix">
                      <input id="plan_wu_cd_distance" type="text" placeholder="1.0">
                      <span>mi</span>
                    </div>
                  </label>
                  <label class="field">
                    <span>WU + CD time</span>
                    <div class="input-suffix">
                      <input id="plan_wu_cd_duration" type="text" placeholder="12">
                      <span>min</span>
                    </div>
                  </label>
                  <label class="field">
                    <span>Rest days per week</span>
                    <input id="plan_rest_days" type="text" value="0" inputmode="numeric">
                  </label>
                  <label class="field">
                    <span>Workout scaling</span>
                    <select id="plan_wf_mode">
                      <option value="same">Keep base workout load</option>
                      <option value="normalize">Scale to peak mileage</option>
                      <option value="custom">Use custom multiplier</option>
                    </select>
                    <small id="plan_wf_ratio_note">Peak ratio: —</small>
                  </label>
                  <label class="field">
                    <span>Custom multiplier</span>
                    <input id="plan_wf_value" type="text" placeholder="0.9 or 1.1" inputmode="decimal">
                  </label>
                </div>

                <div class="choice-row">
                  <label class="choice choice-compact"><input id="plan_redistribute" type="checkbox" checked><span><b>Redistribute removed mileage</b></span></label>
                  <label class="choice choice-compact"><input id="plan_normalize" type="checkbox" checked><span><b>Normalize weekly miles</b></span></label>
                  <label class="choice choice-compact"><input id="plan_norm_reduce" type="checkbox"><span><b>Reduce for faster athletes</b></span></label>
                </div>

                <div class="subsection-title plan-garmin-heading">
                  <div><span class="mini-label">Garmin window</span><h3>Keep only the near-term plan on your watch</h3></div>
                </div>
                <div class="choice-grid">
                  <label class="choice"><input id="plan_schedule_garmin" type="checkbox"><span><b>Upload after generation</b><small>Schedule only the rolling window below.</small></span></label>
                  <label class="choice"><input id="plan_garmin_replace" type="checkbox" checked><span><b>Replace earlier PromptFit uploads</b><small>Remove only app-tracked workouts in the window before adding refreshed versions.</small></span></label>
                </div>
                <div class="field-grid field-grid-2">
                  <label class="field">
                    <span>Upload the next</span>
                    <div class="input-suffix"><input id="plan_garmin_weeks" type="number" min="1" max="52" value="4"><span>weeks</span></div>
                    <small>The window starts today. Unrelated Garmin workouts are never removed.</small>
                  </label>
                </div>
              </div>
            </details>

            <div class="plan-generate-row">
              <div class="plan-generate-state">
                <span class="status-dot" aria-hidden="true"></span>
                <div>
                  <strong>Build your plan</strong>
                  <div id="plan_status" class="plan-status-text" role="status" aria-live="polite">Choose a source, confirm the race setup, then generate.</div>
                </div>
              </div>
              <div class="primary-actions">
                <button class="btn btn-primary" type="button" onclick="generatePlan()">Generate plan preview</button>
                <button class="btn btn-secondary" type="button" onclick="resetPlanForm()">Reset</button>
              </div>
            </div>
            <div id="plan_garmin_status" class="log compact-log" role="status" aria-live="polite" style="display:none;margin-top:12px;"></div>
            <div id="plan_preview_link" class="preview-link" style="display:none;">
              <a class="text-action" href="#plan_output">Jump to generated plan ↓</a>
            </div>
            <p class="privacy-note plan-generation-note">Nothing downloads automatically. After generation, choose the complete package, standalone HTML, or separate preview.</p>
          </div>
        </div>

        <section id="plan_output" class="plan-output-section" aria-labelledby="plan-output-title" hidden>
          <div class="plan-output-heading">
            <div>
              <span class="step-kicker">Your generated plan</span>
              <h3 id="plan-output-title">Calendar and workout schedule</h3>
              <p>The complete interactive output stays in this workspace. Expand weeks, review days, and download only what you need.</p>
            </div>
            <div class="plan-output-actions">
              <a id="plan_package_download" class="btn btn-primary" href="#">Download package</a>
              <a id="plan_html_download" class="btn btn-secondary" href="#">Download HTML</a>
              <a id="plan_open_preview" class="text-action" href="#" target="_blank" rel="noopener">Open separately ↗</a>
            </div>
          </div>
          <div class="plan-output-frame-wrap">
            <iframe id="plan_preview_frame" title="Generated training plan" loading="eager"></iframe>
          </div>
        </section>
      </section>

      <section id="editor" class="workflow-section observe-section editor-section" aria-labelledby="editor-title">
        <div class="section-heading">
          <div>
            <span class="step-kicker">Review · FIT editor</span>
            <h2 id="editor-title">See the workout your watch will receive.</h2>
            <p>Load a FIT file or start blank. Adjust individual legs, reorder repeat blocks, and verify the workout shape before approval.</p>
          </div>
          <span class="section-badge">Live structure</span>
        </div>

        <div class="editor-toolbar surface">
          <div id="fe_drop" class="dropzone dropzone-inline">
            <input id="fit_editor_file" class="visually-hidden-input" type="file" accept=".fit">
            <span class="drop-icon" aria-hidden="true">⇧</span>
            <span>Drop a FIT file here</span>
            <button class="text-button" type="button" id="fe_parse_btn">choose a file</button>
          </div>
          <span class="toolbar-or">or</span>
          <button class="btn btn-secondary" type="button" id="fe_blank_btn">Start blank</button>
          <label class="field toolbar-name">
            <span>Workout name</span>
            <input id="fe_name" type="text" value="Workout">
          </label>
          <label class="choice choice-compact toolbar-choice">
            <input id="fe_deterministic" type="checkbox" checked>
            <span><b>Consistent export metadata</b></span>
          </label>
          <button class="btn btn-primary" type="button" id="fe_export_btn">Save changes for review</button>
        </div>

        <div class="editor-metrics" aria-label="Workout summary">
          <div><b id="fe_metric_legs">0</b><span>legs</span></div>
          <div><b id="fe_metric_time">0:00</b><span>duration</span></div>
          <div><b id="fe_metric_reps">0</b><span>repeat blocks</span></div>
          <div id="fe_status" class="editor-status" role="status" aria-live="polite">Start with a blank workout or load a FIT file.</div>
        </div>

        <div class="editor-workbench">
          <section class="surface builder-pane" aria-labelledby="leg-builder-title">
            <div class="panel-heading">
              <div>
                <span class="mini-label">Structure</span>
                <h3 id="leg-builder-title">Workout legs</h3>
              </div>
              <div class="inline-actions">
                <button class="icon-button" type="button" id="fe_add_step_btn" title="Add step">+ Step</button>
                <button class="icon-button" type="button" id="fe_add_repeat_btn" title="Add repeat block">+ Repeat</button>
              </div>
            </div>
            <div class="template-row">
              <button class="template-chip" type="button" id="fe_tpl_wu_cd_btn">Warmup / cooldown</button>
              <button class="template-chip" type="button" id="fe_tpl_work_btn">Work rep</button>
              <button class="template-chip" type="button" id="fe_tpl_recovery_btn">Recovery jog</button>
              <button class="template-chip" type="button" id="fe_tpl_rest_btn">Rest 2 min</button>
              <button class="template-chip template-chip-strong" type="button" id="fe_template_btn">Interval template</button>
            </div>
            <div id="fe_legs"></div>
          </section>

          <section class="surface preview-pane" aria-labelledby="fit-preview-title">
            <div class="panel-heading">
              <div>
                <span class="mini-label">Preview</span>
                <h3 id="fit-preview-title">Pace over time</h3>
              </div>
              <span class="live-indicator"><i aria-hidden="true"></i> Live</span>
            </div>
            <div class="chart-wrap">
              <canvas id="fe_chart" height="420"></canvas>
              <div id="fe_chart_tip" class="chart-tip hidden"></div>
              <div id="fe_chart_note">No workout loaded.</div>
            </div>
          </section>
        </div>

        <details class="disclosure json-disclosure">
          <summary>
            <span><b>Technical FIT structure</b><small>Round-trip JSON for inspection and troubleshooting</small></span>
            <span class="summary-icon" aria-hidden="true">+</span>
          </summary>
          <div class="disclosure-body">
            <div id="fe_json" class="code-log"></div>
          </div>
        </details>
      </section>

      <section id="deliver" class="workflow-section observe-section" aria-labelledby="deliver-title">
        <div class="section-heading">
          <div>
            <span class="step-kicker">Deliver · Download or Garmin</span>
            <h2 id="deliver-title">Verify first. Send only what you choose.</h2>
            <p>Inspect local FIT files, then keep them for USB sideloading or upload selected workouts to Garmin Connect.</p>
          </div>
          <span class="section-badge">Manual control</span>
        </div>

        <div class="deliver-grid">
          <section class="surface" aria-labelledby="verify-title">
            <div class="panel-heading">
              <div>
                <span class="mini-label">1 · Verify</span>
                <h3 id="verify-title">Inspect FIT files</h3>
              </div>
            </div>
            <div id="fit_dropzone" class="dropzone">
              <input id="fit_files" type="file" accept=".fit" multiple>
              <button class="btn btn-secondary" type="button" onclick="parseFits()">Parse selected FIT</button>
            </div>
            <div id="fit_parse_out" class="log compact-log empty-state" data-empty="The parsed steps and metadata will appear here."></div>
            <div id="fit_chart_wrap" class="fit-chart hidden">
              <div class="fit-chart-head">
                <span>Workout graph</span>
                <select id="fit_chart_select" class="fit-select hidden"></select>
              </div>
              <canvas id="fit_chart" height="220"></canvas>
              <div id="fit_chart_note" class="footnote"></div>
            </div>
          </section>

          <section id="garmin-connect" class="surface garmin-panel" aria-labelledby="garmin-title">
            <div class="panel-heading">
              <div>
                <span class="mini-label">2 · Deliver</span>
                <h3 id="garmin-title">Garmin Connect</h3>
              </div>
              <span class="section-badge" id="gc_connection_badge">Checking…</span>
            </div>

            <div class="garmin-connection-detail" id="gc_connection_detail" role="status" aria-live="polite">
              <span class="garmin-state-icon" id="gc_connection_icon" aria-hidden="true">…</span>
              <div class="garmin-state-copy">
                <strong id="gc_connection_title">Checking Garmin…</strong>
                <span id="gc_connection_note">Confirming the saved connection on this computer.</span>
                <span class="garmin-state-meta" id="gc_connection_meta"></span>
              </div>
            </div>

            <div id="gc_connect_form" class="connection-form" style="display:none;">
              <p class="helper-banner">One-time setup is available only at <b>localhost</b>. Your password is discarded after Garmin creates a reusable session.</p>
              <div class="field-grid field-grid-2">
                <label class="field">
                  <span>Garmin username</span>
                  <input id="gc_username" type="text" placeholder="you@example.com" autocomplete="username">
                </label>
                <label class="field">
                  <span>Garmin password</span>
                  <input id="gc_password" type="password" placeholder="••••••••" autocomplete="current-password">
                </label>
              </div>
              <button class="btn btn-primary" type="button" onclick="connectGarmin()">Connect Garmin once</button>
            </div>

            <div class="inline-actions" id="gc_connected_actions" style="display:none;">
              <button class="btn btn-secondary" id="gc_check_connection_btn" type="button" onclick="loadGarminStatus()">Check connection</button>
              <button class="text-action danger-action" type="button" onclick="disconnectGarmin()">Disconnect</button>
            </div>

            <div id="gc_mfa_box" class="connection-form" style="display:none;">
              <label class="field">
                <span>Garmin verification code</span>
                <input id="gc_mfa_code" type="text" inputmode="numeric" autocomplete="one-time-code" placeholder="Enter the code Garmin sent">
              </label>
              <button class="btn btn-primary" type="button" onclick="finishGarminMfa()">Verify & save connection</button>
            </div>
            <div id="gc_log" class="footnote" role="status" aria-live="polite"></div>

            <hr>

            <div class="panel-heading panel-heading-compact">
              <div>
                <span class="mini-label">Workout library</span>
                <h3>Choose workouts</h3>
              </div>
              <span class="garmin-selection-count" id="gc_selection_count">0 selected</span>
            </div>
            <p class="footnote">Nothing uploads automatically. Check the exact local workouts you want to send.</p>
            <div class="field-grid field-grid-2">
              <label class="field">
                <span>Find a workout</span>
                <input id="gc_local_fit_search" type="search" placeholder="Search name or date">
              </label>
              <label class="field">
                <span>Schedule date <em>optional</em></span>
                <input id="gc_schedule_date" type="date">
              </label>
            </div>
            <div id="gc_local_fit_list" class="log workout-library">Loading workouts from this computer…</div>
            <div class="primary-actions">
              <button class="btn btn-primary" id="gc_local_upload_btn" type="button" onclick="uploadSelectedLocalFits()">Upload checked workouts</button>
              <button class="btn btn-secondary" type="button" onclick="loadLocalFitLibrary()">Refresh</button>
            </div>

            <div class="garmin-upload-status" id="gc_upload_status" role="status" aria-live="polite">
              <span class="garmin-state-icon" id="gc_upload_icon" aria-hidden="true">↑</span>
              <div class="garmin-state-copy">
                <strong id="gc_upload_title">No upload attempted yet</strong>
                <span id="gc_upload_detail">Garmin’s response and each workout ID will appear here.</span>
                <span class="garmin-state-meta" id="gc_upload_meta"></span>
                <div class="garmin-upload-results" id="gc_upload_results"></div>
              </div>
            </div>

            <details class="disclosure delivery-disclosure">
              <summary>
                <span><b>Upload other files</b><small>Use a FIT file from this phone or computer</small></span>
                <span class="summary-icon" aria-hidden="true">+</span>
              </summary>
              <div class="disclosure-body">
                <label class="field">
                  <span>FIT files on this device</span>
                  <input id="gc_fit_files" type="file" accept=".fit,application/octet-stream" multiple>
                </label>
                <div class="inline-actions">
                  <button class="btn btn-secondary" id="gc_upload_btn" type="button" onclick="uploadFitsToGarmin()">Upload chosen files</button>
                  <button class="btn btn-secondary" id="gc_prompt_upload_btn" type="button" onclick="sendGarmin()">Upload prompt workout</button>
                </div>
              </div>
            </details>
          </section>
        </div>
      </section>

      <section id="settings" class="workflow-section observe-section settings-section" aria-labelledby="settings-title">
        <div class="section-heading">
          <div>
            <span class="step-kicker">Settings · Optional</span>
            <h2 id="settings-title">Connect your preferred AI provider.</h2>
            <p>Keys are used only for converting plain-language workouts and plans. On macOS localhost, you can store them in Keychain.</p>
          </div>
          <span class="section-badge">Local credentials</span>
        </div>
        <div class="surface settings-surface">
          <div class="provider-column">
            <div class="provider-heading"><span class="provider-mark">O</span><div><h3>OpenAI</h3><small>Direct OpenAI API access</small></div></div>
            <label class="field">
              <span>API key</span>
              <input id="openai_api_key" type="password" placeholder="sk-…" autocomplete="off">
            </label>
            <label class="field">
              <span>Model <em>optional</em></span>
              <input id="openai_model" type="text" placeholder="Use the app default">
            </label>
          </div>
          <div class="provider-column">
            <div class="provider-heading"><span class="provider-mark provider-mark-alt">R</span><div><h3>OpenRouter</h3><small>Use a supported model through OpenRouter</small></div></div>
            <label class="field">
              <span>API key</span>
              <input id="openrouter_api_key" type="password" placeholder="or-…" autocomplete="off">
            </label>
            <label class="field">
              <span>Model <em>optional</em></span>
              <input id="openrouter_model" type="text" placeholder="Use the app default">
            </label>
          </div>
          <div class="settings-action">
            <p><b>Using this Mac?</b><br><span>Save these values to the local Keychain so you do not have to paste them again.</span></p>
            <button class="btn btn-primary" type="button" onclick="saveSecrets()">Save to Keychain</button>
          </div>
        </div>
      </section>

      <footer class="app-footer">
        <div>
          <strong>PromptFit Studio</strong>
          <span>Built for deliberate training and explicit control.</span>
        </div>
        <a href="#start">Back to top ↑</a>
      </footer>
    </main>
  </div>

  <script src="/static/app.js?v=__ASSET_VERSION__"></script>
  <script src="/static/fit_editor.js?v=__ASSET_VERSION__"></script>
  <script src="/static/studio.js?v=__ASSET_VERSION__"></script>
</body>
</html>
"""
