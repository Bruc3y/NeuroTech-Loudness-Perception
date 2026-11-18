const main = document.querySelector('#main');
const steps = ['Check-In', 'Station Check', 'Screening', 'Sound Discrimination', 'Loudness Ranking', 'Researcher Review', 'PsychoPy Task', 'Session Summary'];
// 'psychopy' starts when the PsychoPy task is launched. Older sessions may be in the retired browser
// task phase 'main', which occupies the same step.
const phaseOrder = ['setup', 'screening', 'discrimination', 'ranking', 'review', 'psychopy', 'complete'];
const stepPhase = phase => phase === 'main' ? 'psychopy' : phase;
// Discrimination staircase (dB difference between the two tones). Keep in step with server.py.
const DISC_START_DB = 6;
const DISC_MIN_DB = 0.5;
const DISC_MAX_DB = 10;
const DISC_STEP_DOWN_DB = 0.5;
const DISC_STEP_UP_DB = 1.5;
// The letter-to-level mapping is shuffled per session (state.ranking.assignment). All five
// play the 70 dB WAV; the supplied 90 dB WAV is exactly that file at +20 dB, so the 70 and
// 90 levels match main-task playback at gain 0.1.
const RANKING_LABELS = ['A', 'B', 'C', 'D', 'E'];
const RANKING_LEVELS = [70, 75, 80, 85, 90];
const rankingOptions = () => RANKING_LABELS.map(label => ({label, level_db: state().ranking.assignment[label]}));
const validAssignment = assignment => !!assignment && typeof assignment === 'object'
  && RANKING_LABELS.map(label => assignment[label]).sort((a, b) => a - b).every((level, i) => level === RANKING_LEVELS[i]);
function randomAssignment() {
  const levels = [...RANKING_LEVELS];
  for (let i = levels.length - 1; i > 0; i--) {
    const j = crypto.getRandomValues(new Uint32Array(1))[0] % (i + 1);
    [levels[i], levels[j]] = [levels[j], levels[i]];
  }
  return Object.fromEntries(RANKING_LABELS.map((label, i) => [label, levels[i]]));
}
const rankingGain = levelDb => Math.round(0.1 * Math.pow(10, (levelDb - 70) / 20) * 1e6) / 1e6;
const rankingAudioPath = () => stimuli.audio['70'];
let rankingPlaying = null;
let session = null;
let station = null;
let stimuli = null;
const audioBuffers = new Map();
let recent = [];
let audio = null;
let busy = false;
let paused = false;
let readyForAnswer = false;
let responseStart = 0;
let playback = null;
let saveQueue = Promise.resolve();
let saveFailure = false;
const oscillators = new Set();
let taskPoll = null;
let taskInfo = null;
const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const state = () => session.state;
const button = (label, action, extra = '') => `<button class="button" data-action="${action}" ${extra}>${label}</button>`;
const primary = (label, action, extra = '') => `<button class="button primary" data-action="${action}" ${extra}>${label}<span aria-hidden="true">→</span></button>`;
const pad = number => String(number).padStart(2, '0');
// Line icons drawn in currentColor, so CSS sets their color.
const svg = paths => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
const ICON = {
  speaker: svg('<path d="M11 5 6 9H3v6h3l5 4V5z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/>'),
  wave: svg('<path d="M2 12h3l2.5-6 4 12 4-12 2.5 6h4"/>'),
  check: svg('<path d="M5 12.5 9.5 17 19 7.5"/>'),
  pause: svg('<path d="M9 6v12M15 6v12"/>'),
};

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The local server could not complete this request.');
  return result;
}

function notify(message, error = true) {
  const notice = document.querySelector('#notice');
  notice.className = error ? 'error' : 'info';
  notice.innerHTML = `<span>${escape(message)}</span><button data-action="dismiss" aria-label="Dismiss message">×</button>`;
  notice.hidden = false;
}

function event(name, detail = {}) {
  state().events.push({utc: new Date().toISOString(), browser_ms: Math.round(performance.now() * 100) / 100,
    event: name, detail: JSON.stringify(detail)});
}

function save() {
  const snapshot = structuredClone(state());
  const id = session.id;
  const operation = saveQueue.catch(() => {}).then(async () => {
    const result = await request(`/api/sessions/${id}`, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({revision: session.revision, state: snapshot})});
    session.revision = result.revision;
    session.updated_at = result.updated_at;
    saveFailure = false;
    if (result.export_warning) notify(result.export_warning);
    return result;
  });
  saveQueue = operation;
  operation.catch(() => { saveFailure = true; });
  return operation;
}

async function ensureAudio() {
  if (!audio) audio = new (window.AudioContext || window.webkitAudioContext)();
  await audio.resume();
  if (audio.state !== 'running') throw new Error('Audio is paused by your browser. Click Play again.');
}

function wait(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(new DOMException('Playback interrupted', 'AbortError'));
    const onAbort = () => { clearTimeout(timer); reject(new DOMException('Playback interrupted', 'AbortError')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', onAbort); resolve(); }, milliseconds);
    signal.addEventListener('abort', onAbort, {once: true});
  });
}

function tone(gain, offset = 0) {
  const start = audio.currentTime + 0.06 + offset;
  const oscillator = audio.createOscillator();
  const envelope = audio.createGain();
  oscillator.frequency.value = 1000;
  envelope.gain.setValueAtTime(0, start);
  envelope.gain.linearRampToValueAtTime(gain, start + 0.01);
  envelope.gain.setValueAtTime(gain, start + 0.99);
  envelope.gain.linearRampToValueAtTime(0, start + 1.0);
  oscillator.connect(envelope).connect(audio.destination);
  oscillator.start(start);
  oscillator.stop(start + 1.0);
  oscillators.add(oscillator);
  oscillator.onended = () => { oscillators.delete(oscillator); oscillator.disconnect(); envelope.disconnect(); };
  return start;
}

function stopAudio() {
  playback?.abort();
  for (const oscillator of oscillators) {
    try { oscillator.stop(); } catch (_) { /* The oscillator already ended. */ }
  }
}

function artwork(trial) {
  return `<img class="stimulus-art" src="${escape(encodeURI(trial.image_path))}" alt="Experiment image" style="object-fit:contain;width:100%;height:340px">`;
}

async function loadAudio(path) {
  if (audioBuffers.has(path)) return;
  const response = await fetch(encodeURI(path));
  if (!response.ok) throw new Error('Could not load the trial audio file.');
  audioBuffers.set(path, await audio.decodeAudioData(await response.arrayBuffer()));
}

async function prepareTrial(trial) {
  const image = new Image();
  image.src = encodeURI(trial.image_path);
  await image.decode();
  await loadAudio(trial.audio_path);
}

function fileTone(trial, start = audio.currentTime + 0.06) {
  const source = audio.createBufferSource();
  source.buffer = audioBuffers.get(trial.audio_path);
  const gain = audio.createGain();
  gain.gain.value = trial.gain;
  source.connect(gain).connect(audio.destination);
  source.start(start);
  oscillators.add(source);
  source.onended = () => { oscillators.delete(source); source.disconnect(); gain.disconnect(); };
  return start;
}

function gallery() {
  const dialog = document.createElement('dialog');
  dialog.style.maxWidth = '1000px';
  dialog.innerHTML = `<div class="dialog-heading"><h2>Supplied image set · 72 images</h2><button class="close-button" aria-label="Close">×</button></div><p>24 images per category. The unpleasant category includes disturbing scenes.</p><label>Category <select id="gallery-category"><option value="pleasant">Pleasant</option><option value="neutral">Neutral</option><option value="unpleasant">Unpleasant</option></select></label><div id="gallery-images" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:20px"></div>`;
  document.body.append(dialog);
  const update = () => { dialog.querySelector('#gallery-images').innerHTML = stimuli.images.filter(item=>item.valence===dialog.querySelector('select').value).map(item=>`<figure style="margin:0"><img loading="lazy" src="${escape(encodeURI(item.path))}" alt="${escape(item.filename)}" style="width:100%;height:110px;object-fit:contain"><figcaption class="small">${escape(item.filename)}</figcaption></figure>`).join(''); };
  dialog.querySelector('select').onchange = update;
  dialog.querySelector('.close-button').onclick = () => dialog.close();
  dialog.onclose = () => dialog.remove();
  update(); dialog.showModal();
}

// lead: the panel is the page's primary element (Researcher review), not a footnote to the browser task.
function taskPanel(lead = false) {
  return `<section class="panel task-launch${lead ? ' lead' : ''}" id="task-panel"><div class="panel-top"><span class="pill">EEG SESSION</span></div>
    <h2>Run the image-and-tone task in PsychoPy.</h2>
    <p>${lead ? '' : 'For EEG data, use this instead of the browser simulation (which sends no triggers). '}PsychoPy opens full screen on this Mac, shows the images, plays the tones and sends the Trigger Hub markers. Its setup dialog asks for the Trigger Hub port, trial count, audio output and equipment checks.</p>
    <ol class="run-steps"><li>Start the raw EEG + Trigger recording in DSI-Streamer on Windows.</li><li>Launch PsychoPy below, complete its setup dialog, then press SPACE once the recording is running.</li><li>After the completion screen, wait 5 seconds, then stop the Windows recording.</li></ol>
    <div id="task-status" class="task-status" aria-live="polite">${taskStatusHtml()}</div></section>`;
}

function taskStatusHtml() {
  const info = taskInfo;
  if (!info) return '<p class="small">Checking PsychoPy…</p>';
  const running = info.launcher?.state === 'running';
  const runs = info.runs.map(run => `<div class="connection"><span><strong>${escape(run.run_id)}</strong><br><small>${escape(run.status)} · ${run.trials_done}/${run.trials_total} trials · ${escape(run.audio_device || 'audio device pending')}${run.fullscreen === false ? ' · NOT full screen' : ''}${run.timing_warnings ? ` · ${run.timing_warnings} timing warning(s)` : ''}</small>${run.error ? `<br><small class="disconnected">${escape(run.error)}</small>` : ''}</span>${run.status === 'completed' && !state().psychopy_run ? button('Finish session with this run', 'link-run', `data-run="${escape(run.run_id)}"`) : ''}</div>`).join('');
  const exited = info.launcher?.state === 'exited' ? `<p class="small">PsychoPy closed${info.launcher.exit_code ? ` with an error (exit code ${info.launcher.exit_code}). See ${escape(info.launcher.log)}` : ''}.</p>` : '';
  const launch = !info.psychopy_available ? '<p class="small disconnected">PsychoPy was not found in Applications. Install it, or start the server with PSYCHOPY_PYTHON set.</p>'
    : info.other_session_running ? '<p class="small">PsychoPy is already running for another session.</p>'
    : `<div class="launch-row">${primary(running ? 'PsychoPy is running…' : 'Launch PsychoPy task', 'launch-task', running ? 'disabled' : '')}<span>${running ? 'Complete the setup dialog on this Mac. It may open behind the browser.' : 'Opens the PsychoPy setup dialog on this Mac.'}</span></div>`;
  return `${launch}${exited}${runs ? `<div style="margin-top:16px">${runs}</div>` : ''}`;
}

async function refreshTask() {
  if (!session || !document.querySelector('#task-status')) { clearInterval(taskPoll); taskPoll = null; return; }
  try {
    taskInfo = await request(`/api/sessions/${session.id}/task`);
    const target = document.querySelector('#task-status');
    if (target) target.innerHTML = taskStatusHtml();
  } catch (error) { /* A transient poll failure is retried on the next interval. */ }
}

function watchTask() {
  if (!document.querySelector('#task-status')) return;
  refreshTask();
  if (!taskPoll) taskPoll = setInterval(refreshTask, 2000);
}

// Case history items from the official hearing screening form, in form order.
const SCREENING_QUESTIONS = [
  {key:'hearing_loss', label:'Do you think you have a hearing loss?'},
  {key:'hearing_aid_recommended', label:'Have hearing aid(s) ever been recommended for you?'},
  {key:'better_ear', label:'Is your hearing better in one ear?',
    followUp:{key:'better_ear_side', label:'If yes, which is the better ear?', options:['Right','Left']}},
  {key:'sudden_progression', label:'Have you ever had a sudden or rapid progression of hearing loss?'},
  {key:'tinnitus', label:'Do you have ringing or noises in your ears?',
    followUp:{key:'tinnitus_side', label:'If yes, which ear?', options:['Right','Left','Both']}},
  {key:'dizziness', label:'Do you consider dizziness to be a problem for you?'},
  {key:'drainage', label:'Have you had recent drainage from your ear(s)?',
    followUp:{key:'drainage_side', label:'If yes, which ear?', options:['Right','Left','Both']}},
  {key:'ear_pain', label:'Do you have pain or discomfort in your ear(s)?',
    followUp:{key:'ear_pain_side', label:'If yes, which ear?', options:['Right','Left','Both']}},
  {key:'medical_consultation', label:'Have you received medical consultation for any of the above conditions?'}
];

// eyebrow is used only on the home page; session pages show their step number instead.
function shell(title, description, content, eyebrow = '') {
  const index = session ? (state().phase === 'aborted' ? steps.length - 1 : phaseOrder.indexOf(stepPhase(state().phase)) + 1) : 0;
  document.querySelector('#step-list').innerHTML = steps.map((name, i) => `<div class="step ${i === index ? 'current' : ''} ${i < index ? 'done' : ''}"${i === index ? ' aria-current="step"' : ''}><span>${i < index ? ICON.check : pad(i + 1)}</span><div>${name}</div></div>`).join('');
  document.querySelector('#breadcrumb').innerHTML = `Study station <span>/</span> ${session ? `<b class="mono">${escape(session.participant)}</b>` : 'New session'}`;
  const controls = session ? `<div class="session-controls"><span class="save-status ${saveFailure ? 'failed' : ''}"><i></i>${saveFailure ? 'Save needs attention' : 'Checkpoints saved locally'}</span>${button('Session details', 'details')}${!['complete', 'aborted'].includes(state().phase) ? button('Pause', 'pause') : ''}</div>` : '';
  const label = session ? `Step ${pad(index + 1)} / ${pad(steps.length)}` : eyebrow;
  main.innerHTML = `<div class="page-heading"><div><p class="eyebrow">${label}</p><h1>${title}</h1><p class="intro">${description}</p></div>${controls}</div>${content}`;
}

// Steps the researcher runs after check-in, as listed on the home page.
const PROTOCOL = [
  ['Station Check', 'Audio level and equipment confirmation', '1 kHz tone'],
  ['Screening', 'Hearing case history questionnaire', '9 items'],
  ['Sound Discrimination', 'Adaptive two-interval loudness comparison', '12 trials'],
  ['Loudness Ranking', 'Order five levels, quietest to loudest', '70–90 dB'],
  ['Researcher Review', 'Screening results and PsychoPy launch', 'Review'],
  ['PsychoPy Task', 'Image and tone trials with Trigger Hub markers', '72 trials'],
];

function home() {
  shell('Study Station', 'Check in a participant to start a session, or resume a saved one.', `
    <div class="home-grid"><section class="panel checkin-panel"><h2>New session</h2><p>Enter the participant’s study ID to begin.</p>
      <form id="checkin"><label for="participant">Participant ID</label><input id="participant" class="mono" name="participant" placeholder="e.g. DEMO014" maxlength="32" pattern="[A-Za-z0-9][A-Za-z0-9_-]*" required autocomplete="off"><span class="field-hint">Letters, numbers, underscores and hyphens.</span>
      <label class="check-row"><input type="checkbox" name="ack" required><span>I’m testing a prototype with demo data.</span></label><button class="button primary wide" type="submit">Begin session<span aria-hidden="true">→</span></button></form>
      <p class="privacy-note">Responses are saved to the local data folder, one directory per session.</p></section>
    <section class="panel protocol-panel"><h2>Session protocol</h2><ol class="protocol-list">${PROTOCOL.map(([name, detail, meta], i) => `<li><span class="protocol-index">${pad(i + 2)}</span><div><strong>${name}</strong><span>${detail}</span></div><span class="protocol-meta">${meta}</span></li>`).join('')}</ol>
      <div class="protocol-footer">${button('Browse images', 'gallery')}</div></section></div>
    <section class="recent"><div class="section-heading"><h2>Recent sessions</h2><span>${recent.length} saved</span></div>${recent.length ? `<div class="session-list">${recent.slice(0, 6).map(item => `<div class="session-row"><strong class="mono">${escape(item.participant)}</strong><small>${new Date(item.created_at).toLocaleString()}</small><span class="phase-pill">${escape(item.phase.replaceAll('_',' '))}</span><button class="button text-button" data-action="resume" data-id="${escape(item.id)}">${['complete','aborted'].includes(item.phase) ? 'View summary' : 'Resume'} →</button></div>`).join('')}</div>` : '<div class="empty-state">Saved sessions appear here. Incomplete sessions can be resumed.</div>'}</section>`, 'Loudness perception study');
}

function render() {
  try { renderPage(); } finally { watchTask(); }
}

function renderPage() {
  if (!session) return home();
  const s = state();
  const count = s.discrimination.length;
  if (s.phase === 'setup') shell('Station Check', 'Confirm the audio level and equipment before the participant begins.', `
    <div class="content-grid"><section class="panel"><h2>Audio level</h2><p>Turn the computer volume down, then play the test tone. Adjust to a comfortable level.</p><div class="sound-preview"><span class="sound-icon">${ICON.speaker}</span><div><strong>1,000 Hz · 1 second</strong><span>Quiet starting gain · 10 ms ramps</span></div>${button('Play test tone', 'test-tone', busy ? 'disabled' : '')}</div>
    <form id="station-check"><label class="check-row"><input name="comfortable" type="checkbox" required ${s.checks.comfortable?'checked':''}><span>The test tone is audible and comfortable.</span></label><label class="check-row"><input name="demo" type="checkbox" required ${s.checks.demo?'checked':''}><span>I understand this session uses demo stimuli and records no EEG.</span></label><button class="button primary" type="submit">Continue to screening <span>→</span></button></form></section>
    <aside class="panel muted-panel"><h2>Connections</h2><div class="connection"><span>Browser audio</span><b>Available</b></div><div class="connection"><span>PsychoPy (native task)</span>${station.psychopy_available ? '<b>Launcher ready</b>' : '<b class="disconnected">Not found</b>'}</div><div class="connection"><span>DSI-7 / DSI Streamer</span><b class="disconnected">Disconnected</b></div><div class="connection"><span>Session storage</span><b>Local folder</b></div><p class="small">For EEG sessions, launch PsychoPy from the Researcher review step. The Windows DSI-Streamer recording is still started and stopped by hand; this browser does not monitor it.</p></aside></div>`);
  if (s.phase === 'screening') shell('Screening Questionnaire', 'Hearing case history. Responses are saved for researcher review.', `
    <section class="panel narrow"><form id="screening-form"><p class="section-label">Case history</p>${SCREENING_QUESTIONS.map(({key,label,followUp})=>{
      const choices=(name,options,disabled='')=>`<div class="choices">${options.map(value=>`<label class="choice"><input type="radio" name="${name}" value="${value}" required ${disabled} ${s.screening[name]===value?'checked':''}><span>${value}</span></label>`).join('')}</div>`;
      const open=s.screening[key]==='Yes';
      const follow=followUp?`<fieldset class="follow-up" data-parent="${key}" ${open?'':'hidden'}><legend>${followUp.label}</legend>${choices(followUp.key,followUp.options,open?'':'disabled')}</fieldset>`:'';
      return `<fieldset><legend>${label}</legend>${choices(key,['Yes','No'])}</fieldset>${follow}`;
    }).join('')}<button class="button primary" type="submit">Continue to sound comparisons <span>→</span></button></form></section>`);
  if (s.phase === 'discrimination') {
    const fraction = count / 12 * 100;
    shell('Sound Discrimination', 'Two tones play in sequence. Choose the louder one after both have finished.', `
      <section class="panel task-panel"><div class="panel-top"><span>Comparison <b class="mono">${pad(Math.min(count+1,12))} / 12</b></span></div><div class="progress-track" role="progressbar" aria-label="Comparisons completed" aria-valuenow="${count}" aria-valuemin="0" aria-valuemax="12"><span style="width:${fraction}%"></span></div>
      <div class="listening-stage"><div class="listening-symbol ${busy ? 'active' : ''}">${busy ? ICON.speaker : ICON.wave}</div><h2 id="play-status" aria-live="polite">${readyForAnswer?'Choose the louder interval.':busy?'Listen to both tones…':'Ready when you are.'}</h2><p>${readyForAnswer?'There is no feedback between comparisons.':'The difference adjusts as you go. Keep the computer volume fixed.'}</p></div>
      <div class="center">${primary('Play tone pair', 'disc-play', busy || readyForAnswer ? 'disabled' : '')}</div><div class="answer-grid">${button('<small>INTERVAL 01</small>First tone', 'answer-1', !readyForAnswer?'disabled':'')}${button('<small>INTERVAL 02</small>Second tone', 'answer-2', !readyForAnswer?'disabled':'')}</div></section>`);
  }
  if (s.phase === 'ranking') {
    const r = s.ranking;
    const heard = RANKING_LABELS.filter(label => r.plays[label] > 0).length;
    const allHeard = heard === RANKING_LABELS.length;
    const status = rankingPlaying ? `Playing ${rankingPlaying}…` : allHeard ? 'Rank from 1 (quietest) to 5 (loudest).' : `Listen to all five tones, then rank them. ${heard} of 5 heard.`;
    shell('Loudness Ranking', 'Play each tone, then rank them from quietest to loudest. Tones can be replayed.', `
      <section class="panel task-panel"><div class="panel-top"><span>Tones played <b class="mono">${heard} / 5</b></span></div>
      <div class="listening-stage"><div class="listening-symbol ${rankingPlaying ? 'active' : ''}">${rankingPlaying ? ICON.speaker : ICON.wave}</div><h2 id="play-status" aria-live="polite">${status}</h2><p>Use each rank once. Choosing a rank that is already taken clears it from the other tone. Keep the computer volume fixed.</p></div>
      <form id="ranking-form"><div class="rank-list"><div class="rank-row rank-header" aria-hidden="true"><span></span><span></span><div class="scale-labels"><span>1 · Quietest</span><span>5 · Loudest</span></div></div>
      ${RANKING_LABELS.map(label => `<div class="rank-row" role="group" aria-label="Tone ${label}"><span class="rank-letter" aria-hidden="true">${label}</span>${button(rankingPlaying === label ? 'Playing…' : r.plays[label] ? `Replay ${label} <span aria-hidden="true">✓</span>` : `Play ${label}`, 'rank-play', `data-label="${label}" ${busy ? 'disabled' : ''}`)}<div class="rating-options">${[1,2,3,4,5].map(value => `<label><input type="radio" name="rank-${label}" value="${value}" aria-label="Tone ${label}, rank ${value}" required ${r.ranks[label] === value ? 'checked' : ''} ${allHeard ? '' : 'disabled'}><span>${value}</span></label>`).join('')}</div></div>`).join('')}</div>
      <div class="center"><button class="button primary" type="submit" ${allHeard && !busy ? '' : 'disabled'}>Save ranking & continue<span>→</span></button></div></form></section>`);
  }
  if (s.phase === 'review') {
    const ranking = s.ranking?.results ? s.ranking : null;
    const correct = s.discrimination.filter(item=>item.correct).length;
    const flags = Object.values(s.screening).filter(value=>value==='Yes').length;
    const reviewed = !!s.review.demo_approved;
    shell('Researcher Review', 'Run the PsychoPy task with the Trigger Hub, then record the screening review below.', `
    ${taskPanel(true)}
    <div class="review-grid"><section class="panel compact-panel"><div class="panel-top"><h2>Screening results</h2><span class="pill ${reviewed ? 'ok' : 'amber'}">${reviewed ? 'Reviewed' : 'Review required'}</span></div><div class="metrics-row compact"><div><strong>${s.threshold == null ? '—' : `${Number(s.threshold).toFixed(2)} dB`}</strong><span>demo threshold estimate</span></div><div><strong>${correct} / 12</strong><span>correct responses</span></div><div><strong>${flags}</strong><span>questionnaire items to review</span></div><div><strong>${ranking ? `${ranking.exact_matches} / 5` : '—'}</strong><span>tones ranked in place</span></div></div>
    <form id="review-form"><label for="review-note">Researcher note</label><textarea id="review-note" name="note" rows="2" placeholder="Optional note about this session">${escape(s.review.note || '')}</textarea><div class="review-actions"><label class="check-row"><input name="reviewed" type="checkbox" required ${reviewed ? 'checked' : ''}><span>I’ve reviewed the screening results.</span></label><button class="button" type="submit">Save review</button></div></form></section>
    <aside class="panel muted-panel compact-panel"><p class="section-label">Protocol decision</p><h2>Eligibility stays open</h2><p>The study’s screening cutoff, full staircase length, and repeatability rule still need to be defined.</p><p class="small">Main-task gains are fixed across sessions. This threshold does not change them.</p><div class="summary-actions">${button('Repeat comparisons', 'repeat-disc')}${button('Repeat ranking', 'repeat-ranking')}</div></aside></div>`);
  }
  if (s.phase === 'psychopy') shell('PsychoPy Task', 'The image and tone task runs in PsychoPy with Trigger Hub markers. Finish the session here once the run completes.', `
    ${taskPanel(true)}<div class="page-actions">${button('← Back to Researcher Review', 'back-review')}</div>`);
  if (s.phase === 'main') {
    const trial = s.schedule[s.ratings.length];
    shell('Image and Tone Task', 'View the image, hear the tone, then rate its perceived loudness.', `
      <section class="panel task-panel"><div class="panel-top"><span>Trial <b class="mono">${pad(s.ratings.length+1)} / ${s.schedule.length}</b></span><span>Browser simulation</span></div><div class="progress-track"><span style="width:${s.ratings.length/s.schedule.length*100}%"></span></div>
      ${busy || readyForAnswer ? `<div class="stimulus-frame">${artwork(trial)}</div>` : '<div class="trial-ready"><div class="fixation">+</div><h2>Ready for the next trial</h2><p>The image appears first. A tone follows one to two seconds later.</p></div>'}
      ${readyForAnswer ? `<form id="rating-form"><fieldset class="rating-field"><legend>How loud did the tone feel?</legend><div class="rating-options">${[1,2,3,4,5,6,7].map(value=>`<label><input type="radio" name="rating" value="${value}" required><span>${value}</span></label>`).join('')}</div><div class="scale-labels"><span>Very quiet</span><span>Very loud</span></div></fieldset><div class="center"><button class="button primary" type="submit">${s.ratings.length===s.schedule.length-1?'Finish session':'Save rating & continue'}<span>→</span></button></div></form>` : `<div class="center">${primary(busy?'Playing trial…':'Begin trial', 'main-play', busy?'disabled':'')}</div>`}
      <p class="task-footnote">Browser simulation · no triggers · supplied 70/90-labeled WAVs · playback gain 0.1 · uncalibrated</p></section>${s.ratings.length === 0 && !busy && !readyForAnswer ? taskPanel() : ''}`);
  }
  if (['complete','aborted'].includes(s.phase)) shell(s.phase==='complete'?'Session Complete':'Session Ended Early', 'Responses and the event log are saved on this computer.', `
    <section class="panel summary-panel"><span class="completion-mark ${s.phase}">${s.phase==='complete'?ICON.check:ICON.pause}</span><p class="section-label">${s.phase==='complete'?'Complete':'Partial session'}</p><h2 class="mono">${escape(session.participant)}</h2><div class="summary-metrics"><div><strong>${s.discrimination.length}</strong><span>comparisons saved</span></div><div><strong>${s.psychopy_run ? s.psychopy_run.trials_done : s.ratings.length}</strong><span>ratings saved${s.psychopy_run ? ' (PsychoPy)' : ''}</span></div><div><strong>${s.psychopy_run ? 'Linked' : '0'}</strong><span>${s.psychopy_run ? 'PsychoPy run · verify EEG file' : 'EEG samples acquired'}</span></div></div>${s.psychopy_run ? `<p class="small storage-path">PsychoPy run ${escape(s.psychopy_run.run_id)} → ${escape(s.psychopy_run.folder)}</p>` : ''}<div class="inline-note">Simulation record. This session contains demo responses and browser event times. The image set and screening estimate require study validation.</div><div class="summary-actions"><a class="button primary" href="/api/sessions/${session.id}/export">Download session ZIP <span>↓</span></a>${button('New participant', 'home')}</div><p class="small storage-path">${escape(station.data_directory)} / sub-${escape(session.participant)} / ses-${escape(session.id)}</p></section>`);
}

async function enterSession(item) {
  session = item;
  readyForAnswer = false;
  busy = false;
  paused = false;
  saveFailure = false;
  saveQueue = Promise.resolve();
  rankingPlaying = null;
  // Sessions created before the ranking step have no ranking record.
  if (!state().ranking || typeof state().ranking !== 'object') state().ranking = {};
  state().ranking.ranks ??= {};
  state().ranking.plays ??= {};
  // Sessions created before randomization get a shuffled mapping before ranking starts.
  if (!state().ranking.results && !['complete','aborted'].includes(state().phase) && !validAssignment(state().ranking.assignment)) {
    state().ranking.assignment = randomAssignment();
    event('ranking_assignment_created', {assignment: state().ranking.assignment, source: 'browser'});
  }
  sessionStorage.setItem('neuronerds-session', item.id);
  if (state().activeTask && !['complete','aborted'].includes(state().phase)) {
    event('attempt_interrupted', {attempt: state().activeTask, reason: 'page_reopened'});
    state().activeTask = null;
    await save();
    notify('The interrupted attempt was logged. Begin again when you are ready.', false);
  }
  render();
}

async function playTask(kind) {
  if (busy || readyForAnswer) return;
  busy = true;
  await ensureAudio();
  if (kind === 'main') await prepareTrial(state().schedule[state().ratings.length]);
  const signal = new AbortController();
  playback = signal;
  busy = true;
  const s = state();
  const index = kind==='discrimination' ? s.discrimination.length : s.ratings.length;
  const task = {kind, index, attempt_id: crypto.randomUUID()};
  if (kind==='discrimination') {
    task.delta_db = s.delta;
    task.louder_interval = crypto.getRandomValues(new Uint8Array(1))[0] % 2 + 1;
  }
  s.activeTask = task;
  event('attempt_started', task);
  try {
    await save();
    if (signal.signal.aborted) return;
    render();
    if (kind==='discrimination') {
      const low = 0.028;
      const high = low * Math.pow(10, task.delta_db / 20);
      const first = tone(task.louder_interval===1?high:low);
      const second = tone(task.louder_interval===2?high:low, 1.3);
      event('audio_pair_scheduled', {attempt_id: task.attempt_id, first_audio_context_s:first, second_audio_context_s:second, measured:false});
      await wait(2430, signal.signal);
    } else {
      const trial = s.schedule[index];
      // Finish image decoding and allow a paint before starting the lead interval.
      await document.querySelector('.stimulus-art').decode();
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      if (signal.signal.aborted) return;
      event('image_render_observed', {attempt_id:task.attempt_id, image_id:trial.image_id, measured:false});
      const start = fileTone(trial, audio.currentTime + (trial.soa_s ?? session.protocol.image_lead_seconds ?? 1.0));
      event('tone_scheduled', {attempt_id:task.attempt_id, ...trial, audio_context_s:start, measured:false});
      await wait(Math.max(0, (start + 1.05 - audio.currentTime) * 1000), signal.signal);
    }
    await save();
    if (signal.signal.aborted) return;
    readyForAnswer = true;
    responseStart = performance.now();
  } catch (error) {
    if (error.name !== 'AbortError') throw error;
  } finally {
    busy = false;
    if (!paused) render();
  }
}

async function answer(value) {
  if (!readyForAnswer || busy || saveFailure) return;
  busy = true;
  readyForAnswer = false;
  const s = state();
  const task = s.activeTask;
  const correct = value === task.louder_interval;
  s.discrimination.push({trial:s.discrimination.length+1, attempt_id:task.attempt_id,
    delta_db:task.delta_db, louder_interval:task.louder_interval, response:value, correct, rt_ms:Math.round(performance.now()-responseStart)});
  event('comparison_answered', s.discrimination.at(-1));
  // Weighted up-down (Kaernbach 1991): a 3:1 up/down step ratio converges on ~75% correct.
  // From the 6 dB start, 11 correct answers in a row reach the 0.5 dB floor on trial 12.
  s.delta = correct ? Math.max(DISC_MIN_DB, s.delta - DISC_STEP_DOWN_DB) : Math.min(DISC_MAX_DB, s.delta + DISC_STEP_UP_DB);
  s.activeTask = null;
  if (s.discrimination.length===12) {
    s.threshold = s.discrimination.slice(-4).reduce((total,item)=>total+item.delta_db,0)/4;
    s.phase = s.ranking?.results ? 'review' : 'ranking';
  }
  await save();
  busy = false;
  render();
}

async function playRankingOption(label) {
  const option = rankingOptions().find(item => item.label === label);
  if (busy || !option) return;
  busy = true;
  const controller = new AbortController();
  playback = controller;
  try {
    await ensureAudio();
    const audioPath = rankingAudioPath();
    await loadAudio(audioPath);
    if (controller.signal.aborted) return;
    rankingPlaying = label;
    render();
    const gain = rankingGain(option.level_db);
    const start = fileTone({audio_path: audioPath, gain});
    await wait(Math.max(0, (start + 1.05 - audio.currentTime) * 1000), controller.signal);
    const r = state().ranking;
    r.plays[label] = (r.plays[label] || 0) + 1;
    event('ranking_tone_played', {label, level_db: option.level_db, gain, audio_path: audioPath,
      audio_context_s: start, play_count: r.plays[label], measured: false});
    rankingPlaying = null;
    await save();
  } catch (error) {
    if (error.name !== 'AbortError') throw error;
  } finally {
    busy = false;
    rankingPlaying = null;
    if (!paused) render();
  }
}

function rankingProblem() {
  const r = state().ranking;
  if (!validAssignment(r.assignment)) return 'The tone assignment is missing. Reload to resume the session.';
  if (RANKING_LABELS.some(label => !(r.plays[label] > 0))) return 'Play all five tones before ranking them.';
  const ranks = RANKING_LABELS.map(label => r.ranks[label]);
  if (ranks.some(rank => !Number.isInteger(rank)) || new Set(ranks).size !== RANKING_LABELS.length) return 'Give each tone a different rank from 1 to 5.';
  return null;
}

function details() {
  const dialog = document.createElement('dialog');
  dialog.innerHTML=`<div class="dialog-heading"><h2>Session details</h2><button class="close-button" aria-label="Close">×</button></div><span class="pill">SIMULATION ONLY</span><dl><dt>Participant</dt><dd>${escape(session.participant)}</dd><dt>Session</dt><dd>${escape(session.id)}</dd><dt>Protocol</dt><dd>${escape(session.protocol.version)}</dd><dt>PsychoPy / EEG</dt><dd>Disconnected / no EEG collected</dd><dt>Checkpoint revision</dt><dd>${session.revision}</dd><dt>Data folder</dt><dd class="storage-path">${escape(station.data_directory)} / sub-${escape(session.participant)} / ses-${escape(session.id)}</dd></dl><a class="button primary" href="/api/sessions/${session.id}/export">Download current ZIP ↓</a><p class="small">Incomplete attempts remain in the event log. The ZIP contains a complete checkpoint and the saved responses so far.</p>`;
  document.body.append(dialog);
  dialog.querySelector('.close-button').onclick=()=>dialog.close();
  dialog.onclose=()=>dialog.remove();
  dialog.showModal();
}

async function pause() {
  if (paused) return;
  paused=true;
  stopAudio();
  if (state().activeTask) event('attempt_interrupted', {attempt:state().activeTask, reason:'operator_pause'});
  state().activeTask=null;
  readyForAnswer=false;
  event('session_paused');
  await save();
  busy=false;
  const dialog=document.createElement('dialog');
  dialog.innerHTML=`<div class="dialog-heading"><h2>Take a moment.</h2></div><p>Audio has stopped. Saved responses are preserved. An interrupted trial will restart with a new attempt ID.</p><div class="dialog-actions"><button class="button primary" id="resume-button">Resume demo →</button><button class="button danger" id="end-button">End and save partial session</button></div>`;
  document.body.append(dialog);
  dialog.addEventListener('cancel',e=>e.preventDefault());
  dialog.querySelector('#resume-button').onclick=async()=>{
    try { event('session_resumed'); await save(); paused=false; dialog.close(); dialog.remove(); render(); }
    catch(error){notify(error.message);}
  };
  dialog.querySelector('#end-button').onclick=async()=>{
    try { state().phase='aborted'; event('session_aborted'); await save(); paused=false; dialog.close(); dialog.remove(); render(); }
    catch(error){notify(error.message);}
  };
  dialog.showModal();
}

document.addEventListener('submit', async eventObject => {
  eventObject.preventDefault();
  if (busy || saveFailure) return;
  const form=eventObject.target;
  if (!form.reportValidity()) return;
  if (form.id==='ranking-form') { const problem=rankingProblem(); if (problem) return notify(problem); }
  const values=new FormData(form);
  busy=true;
  form.querySelector('button[type=submit]')?.setAttribute('disabled','');
  try {
    if (form.id==='checkin') {
      const item=await request('/api/sessions', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({participant:values.get('participant')})});
      await enterSession(item);
      return;
    }
    const s=state();
    if (form.id==='station-check') {
      s.checks={comfortable:true,demo:true}; s.phase='screening'; event('station_check_confirmed',s.checks);
    }
    if (form.id==='screening-form') {
      s.screening=Object.fromEntries(values); s.phase='discrimination'; event('questionnaire_saved',s.screening);
    }
    if (form.id==='ranking-form') {
      const r=s.ranking;
      const results=rankingOptions().map(({label,level_db})=>({label,level_db,gain:rankingGain(level_db),
        audio_path:rankingAudioPath(),expected_rank:RANKING_LEVELS.indexOf(level_db)+1,rank:r.ranks[label],correct:r.ranks[label]===RANKING_LEVELS.indexOf(level_db)+1,plays:r.plays[label]}));
      const squared=results.reduce((total,item)=>total+(item.rank-item.expected_rank)**2,0);
      s.ranking={assignment:{...r.assignment},ranks:{...r.ranks},plays:{...r.plays},results,exact_matches:results.filter(item=>item.correct).length,
        spearman_rho:1-6*squared/120,submitted_at:new Date().toISOString()};
      s.phase='review'; event('ranking_submitted',s.ranking);
    }
    if (form.id==='review-form') {
      // Saves the note and sign-off in place; the participant continues in PsychoPy, not the browser task.
      s.review={note:values.get('note').trim(),demo_approved:true,at:new Date().toISOString()};
      event('demo_review_approved',s.review);
    }
    if (form.id==='rating-form' && readyForAnswer && s.activeTask) {
      const trial=s.schedule[s.ratings.length];
      s.ratings.push({trial:s.ratings.length+1,attempt_id:s.activeTask.attempt_id,...trial,
        rating:Number(values.get('rating')),rt_ms:Math.round(performance.now()-responseStart)});
      event('rating_saved',s.ratings.at(-1)); s.activeTask=null; readyForAnswer=false;
      if (s.ratings.length===s.schedule.length) {s.phase='complete'; event('session_completed');}
    }
    await save(); busy=false; render();
  } catch(error) {notify(error.message + (session?' Reload to resume the last saved checkpoint.':''));}
  finally {busy=false; form.querySelector('button[type=submit]')?.removeAttribute('disabled');}
});

document.addEventListener('click', async click => {
  const target=click.target.closest('[data-action]');
  if (!target || target.disabled) return;
  const action=target.dataset.action;
  if (busy && !['pause','details','dismiss'].includes(action)) return;
  if (saveFailure && !['details','dismiss','home','resume'].includes(action)) return notify('Saving needs attention. Reload to resume the last checkpoint.');
  try {
    if (action==='dismiss') document.querySelector('#notice').hidden=true;
    if (action==='details') details();
    if (action==='gallery') gallery();
    if (action==='pause') await pause();
    if (action==='resume') await enterSession(await request(`/api/sessions/${target.dataset.id}`));
    if (action==='home') {session=null;sessionStorage.removeItem('neuronerds-session');recent=await request('/api/sessions');render();}
    if (action==='test-tone') {
      busy=true; target.disabled=true;
      await ensureAudio(); tone(0.028);
      await new Promise(resolve=>setTimeout(resolve,1150));
      event('test_tone_played',{gain:0.028}); await save(); busy=false; target.disabled=false;
    }
    if (action==='launch-task') {
      target.disabled=true;
      event('psychopy_launch_requested', {});
      await save();
      taskInfo = await request(`/api/sessions/${session.id}/task`, {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
      notify('PsychoPy is starting. Complete its setup dialog on this Mac (it may open behind the browser).', false);
      if (state().phase === 'review') { state().phase = 'psychopy'; event('psychopy_step_started', {}); await save(); render(); }
      await refreshTask();
    }
    if (action==='back-review') {
      state().phase = 'review'; event('returned_to_review', {});
      await save(); render();
    }
    if (action==='link-run') {
      const run = taskInfo?.runs.find(item => item.run_id === target.dataset.run);
      if (!run || run.status !== 'completed') throw new Error('That PsychoPy run is not complete.');
      const s = state();
      s.psychopy_run = run; s.phase = 'complete';
      event('psychopy_run_linked', run); event('session_completed', {via: 'psychopy'});
      await save(); render();
    }
    if (action==='rank-play') await playRankingOption(target.dataset.label);
    if (action==='repeat-ranking') {
      event('ranking_repeated',{previous:state().ranking});
      state().ranking={assignment:randomAssignment(),ranks:{},plays:{}}; state().phase='ranking';
      event('ranking_assignment_created',{assignment:state().ranking.assignment,source:'browser_repeat'});
      await save(); render();
    }
    if (action==='disc-play') await playTask('discrimination');
    if (action==='main-play') await playTask('main');
    if (action==='answer-1') await answer(1);
    if (action==='answer-2') await answer(2);
    if (action==='repeat-disc') {
      event('discrimination_repeated',{previous_trials:state().discrimination,previous_estimate:state().threshold});
      Object.assign(state(),{discrimination:[],delta:DISC_START_DB,threshold:null,phase:'discrimination'});
      await save(); render();
    }
  } catch(error) {busy=false; notify(error.message + (session?' Reload to resume the saved checkpoint.':''));}
});

document.addEventListener('change', changeEvent => {
  const input=changeEvent.target;
  // Case-history follow-ups (which ear) appear and become required only after a Yes.
  const followUp=input.closest('#screening-form')?.querySelector(`.follow-up[data-parent="${input.name}"]`);
  if (followUp) {
    const open=input.value==='Yes';
    followUp.hidden=!open;
    followUp.querySelectorAll('input').forEach(option=>{option.disabled=!open; if (!open) option.checked=false;});
    return;
  }
  if (!session || state().phase!=='ranking' || !input.closest('#ranking-form') || !input.name?.startsWith('rank-')) return;
  const label=input.name.slice(5);
  const value=Number(input.value);
  const ranks=state().ranking.ranks;
  // Keep ranks unique: taking a rank clears it from whichever tone held it.
  for (const [other,rank] of Object.entries(ranks)) {
    if (other!==label && rank===value) {
      delete ranks[other];
      const previous=input.form.querySelector(`input[name="rank-${other}"][value="${value}"]`);
      if (previous) previous.checked=false;
    }
  }
  ranks[label]=value;
});

window.addEventListener('beforeunload', eventObject => {
  if (busy || readyForAnswer) {eventObject.preventDefault(); eventObject.returnValue='';}
});

async function boot() {
  try {
    [station,recent,stimuli]=await Promise.all([request('/api/status'),request('/api/sessions'),request('/api/stimuli')]);
    const previous=sessionStorage.getItem('neuronerds-session');
    if (previous) await enterSession(await request(`/api/sessions/${previous}`));
    else render();
  } catch(error) {
    main.innerHTML='<div class="panel"><h1>The local station needs attention.</h1><p>Keep the server terminal open, then reload this page.</p><button class="button" onclick="location.reload()">Reload station</button></div>';
    notify(error.message);
  }
}
boot();
