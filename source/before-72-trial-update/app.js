const main = document.querySelector('#main');
const steps = ['Check-in', 'Station check', 'Screening', 'Sound discrimination', 'Researcher review', 'Main task', 'Session summary'];
const phaseOrder = ['setup', 'screening', 'discrimination', 'review', 'main', 'complete'];
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
const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const state = () => session.state;
const button = (label, action, extra = '') => `<button class="button" data-action="${action}" ${extra}>${label}</button>`;
const primary = (label, action, extra = '') => `<button class="button primary" data-action="${action}" ${extra}>${label}<span aria-hidden="true">↗</span></button>`;

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

async function prepareTrial(trial) {
  const image = new Image();
  image.src = encodeURI(trial.image_path);
  await image.decode();
  if (!audioBuffers.has(trial.audio_path)) {
    const response = await fetch(encodeURI(trial.audio_path));
    if (!response.ok) throw new Error('Could not load the trial audio file.');
    audioBuffers.set(trial.audio_path, await audio.decodeAudioData(await response.arrayBuffer()));
  }
}

function fileTone(trial) {
  const source = audio.createBufferSource();
  source.buffer = audioBuffers.get(trial.audio_path);
  const gain = audio.createGain();
  gain.gain.value = trial.gain;
  source.connect(gain).connect(audio.destination);
  const start = audio.currentTime + 0.06;
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

function shell(title, description, content, eyebrow = 'PARTICIPANT SESSION') {
  const index = session ? (state().phase === 'aborted' ? 6 : phaseOrder.indexOf(state().phase) + 1) : 0;
  document.querySelector('#step-list').innerHTML = steps.map((name, i) => `<div class="step ${i === index ? 'current' : ''} ${i < index ? 'done' : ''}"><span>${i < index ? '✓' : String(i + 1).padStart(2, '0')}</span><div>${name}${i === index ? '<small>In progress</small>' : ''}</div></div>`).join('');
  document.querySelector('#breadcrumb').innerHTML = `Study station <span>/</span> ${session ? escape(session.participant) : 'New session'}`;
  const controls = session ? `<div class="session-controls"><span class="save-status"><i></i>${saveFailure ? 'Save needs attention' : 'Local checkpoints enabled'}</span>${button('Session details', 'details')}${!['complete', 'aborted'].includes(state().phase) ? button('Pause', 'pause') : ''}</div>` : '';
  main.innerHTML = `<div class="page-heading"><div><p class="eyebrow">${eyebrow}</p><h1>${title}</h1><p class="intro">${description}</p></div>${controls}</div>${content}`;
}

function home() {
  shell('A calmer way to collect data.', 'One guided session, from first instructions to the final saved response.', `
    <div class="home-grid"><section class="panel checkin-panel"><div class="panel-top"><span class="pill">NEW SESSION</span><span class="subtle">Headset-free testing</span></div><h2>Let’s check you in.</h2><p>Use a test participant ID to explore the collection workflow.</p>
      <form id="checkin"><label for="participant">Participant ID</label><input id="participant" name="participant" placeholder="e.g. DEMO014" maxlength="32" pattern="[A-Za-z0-9][A-Za-z0-9_-]*" required autocomplete="off"><span class="field-hint">Letters, numbers, underscores and hyphens. Use a study ID.</span>
      <label class="check-row"><input type="checkbox" name="ack" required><span>I’m testing a prototype with demo data.</span></label><button class="button primary wide" type="submit">Begin session<span>↗</span></button></form>
      <div class="privacy-note"><span>◈</span> Responses stay in the local data folder. Each session gets its own unique directory.</div></section>
    <section class="overview"><div class="orbital" aria-hidden="true"><div class="orbit o1"></div><div class="orbit o2"></div><div class="orbit o3"></div><div class="orbit o4"></div><div class="wave-bars">${[16,32,48,70,40,58,85,52,34,60,42,22].map(h=>`<i style="height:${h}px"></i>`).join('')}</div><span class="orb-label">PERCEPTION / CONTEXT / RESPONSE</span></div>
      <div class="overview-copy"><p class="eyebrow">THE PILOT FLOW</p><h2>Small steps. A complete session.</h2><p>Practice sound discrimination, explore three visual contexts, and save every response.</p><div class="study-stats"><div><strong>12</strong><span>sound comparisons</span></div><div><strong>6</strong><span>image + tone trials</span></div><div><strong>~3 min</strong><span>guided walkthrough</span></div></div></div></section></div>
    <section class="simulation-note"><strong>Built for testing the workflow.</strong> Uses all 72 supplied images and your one-second WAV files. Browser audio is uncalibrated. Screening estimates are exploratory. PsychoPy and EEG hardware are disconnected.</section>
    <section class="panel" style="margin-bottom:24px"><h2>Your experiment assets are ready.</h2><p>72 images · 24 per category · two 1,000 Hz tones · one second each. The six-trial browser walkthrough samples one image for each category and level. Use the separate PsychoPy pilot for hardware markers and Windows EEG recording.</p>${button("Browse the 72 images", "gallery")}</section><section class="recent"><div class="section-heading"><h2>Recent local sessions</h2><span>${recent.length} saved</span></div>${recent.length ? `<div class="session-list">${recent.slice(0, 6).map(item => `<div class="session-row"><div><strong>${escape(item.participant)}</strong><small>${new Date(item.created_at).toLocaleString()}</small></div><span class="phase-pill">${escape(item.phase.replaceAll('_',' '))}</span><button class="button text-button" data-action="resume" data-id="${escape(item.id)}">${['complete','aborted'].includes(item.phase) ? 'View summary' : 'Resume session'} →</button></div>`).join('')}</div>` : '<div class="empty-state">Your first session will appear here. Incomplete sessions can be resumed.</div>'}</section>`, 'NEURONERDS / STUDY STATION');
}

function render() {
  if (!session) return home();
  const s = state();
  const count = s.discrimination.length;
  if (s.phase === 'setup') shell('Make yourself comfortable.', 'A quick station check before the walkthrough begins.', `
    <div class="content-grid"><section class="panel"><div class="panel-top"><span class="pill">STATION CHECK</span><span>01 / 05</span></div><h2>Test the sound first.</h2><p>Turn your computer volume down, then play a short tone. Adjust to a comfortable level.</p><div class="sound-preview"><span class="sound-icon" aria-hidden="true">◖))</span><div><strong>1,000 Hz · 1 second</strong><span>Quiet starting gain · 10 ms ramps</span></div>${button('Play test tone', 'test-tone', busy ? 'disabled' : '')}</div>
    <form id="station-check"><label class="check-row"><input name="comfortable" type="checkbox" required ${s.checks.comfortable?'checked':''}><span>The test tone is audible and comfortable.</span></label><label class="check-row"><input name="demo" type="checkbox" required ${s.checks.demo?'checked':''}><span>I understand this session uses demo stimuli and records no EEG.</span></label><button class="button primary" type="submit">Continue to screening <span>→</span></button></form></section>
    <aside class="panel muted-panel"><p class="eyebrow">CONNECTIONS</p><h2>Simulation station</h2><div class="connection"><span>Browser audio</span><b>Available</b></div><div class="connection"><span>PsychoPy</span><b class="disconnected">Disconnected</b></div><div class="connection"><span>DSI-7 / DSI Streamer</span><b class="disconnected">Disconnected</b></div><div class="connection"><span>Session storage</span><b>Local folder</b></div><p class="small">For the hardware test, run psychopy_pilot.py in PsychoPy Coder and record EEG separately on Windows. This browser does not monitor that recorder.</p></aside></div>`);
  if (s.phase === 'screening') shell('A little context before the sound.', 'Practice questionnaire · responses are saved for researcher review.', `
    <section class="panel narrow"><div class="panel-top"><span class="pill">SCREENING</span><span>02 / 05</span></div><form id="screening-form">${[
      ['difficulty','Do you currently experience difficulty hearing?'],
      ['symptoms','Do you currently have ringing, pain, or discomfort in your ears?'],
      ['assistance','Are you using a hearing aid or another hearing device today?']
    ].map(([key,label])=>`<fieldset><legend>${label}</legend><div class="choices">${['No','Yes','Prefer not to answer'].map(value=>`<label class="choice"><input type="radio" name="${key}" value="${value}" required ${s.screening[key]===value?'checked':''}><span>${value}</span></label>`).join('')}</div></fieldset>`).join('')}<div class="inline-note">These are prototype questions. Your team will supply the approved questionnaire and study inclusion criteria.</div><button class="button primary" type="submit">Continue to sound comparisons <span>→</span></button></form></section>`);
  if (s.phase === 'discrimination') {
    const fraction = count / 12 * 100;
    shell('Which tone sounded louder?', 'You’ll hear two tones. Choose the louder one after both have finished.', `
      <section class="panel task-panel"><div class="panel-top"><span class="pill">SOUND DISCRIMINATION</span><span>Comparison ${Math.min(count+1,12)} of 12</span></div><div class="progress-track" role="progressbar" aria-label="Comparisons completed" aria-valuenow="${count}" aria-valuemin="0" aria-valuemax="12"><span style="width:${fraction}%"></span></div>
      <div class="listening-stage"><div class="listening-symbol" aria-hidden="true">${busy ? '◖))' : '∿'}</div><h2 id="play-status" aria-live="polite">${readyForAnswer?'Choose the louder interval.':busy?'Listen to both tones…':'Ready when you are.'}</h2><p>${readyForAnswer?'There is no feedback between comparisons.':'The difference adjusts as you go. Keep the computer volume fixed.'}</p></div>
      <div class="center">${primary('Play tone pair', 'disc-play', busy || readyForAnswer ? 'disabled' : '')}</div><div class="answer-grid">${button('<small>INTERVAL 01</small>First tone', 'answer-1', !readyForAnswer?'disabled':'')}${button('<small>INTERVAL 02</small>Second tone', 'answer-2', !readyForAnswer?'disabled':'')}</div><p class="task-footnote">Short demo staircase · illustrative estimate · eligibility reviewed by a researcher</p></section>`, 'LISTEN / COMPARE / RESPOND');
  }
  if (s.phase === 'review') {
    const correct = s.discrimination.filter(item=>item.correct).length;
    const flags = Object.values(s.screening).filter(value=>value!=='No').length;
    shell('A checkpoint for the researcher.', 'Review the practice session before moving to image-and-tone trials.', `
    <div class="content-grid"><section class="panel"><div class="panel-top"><span class="pill amber">REVIEW REQUIRED</span><span>03 / 05</span></div><h2>Demo threshold estimate</h2><div class="threshold-value">${Number(s.threshold).toFixed(1)} <span>dB difference</span></div><p>Average of the final four tested differences in a short adaptive staircase. This estimate is unvalidated and does not determine study eligibility.</p><div class="metrics-row"><div><strong>${correct} / 12</strong><span>correct responses</span></div><div><strong>${flags}</strong><span>questionnaire items to review</span></div></div><form id="review-form"><label for="review-note">Researcher note</label><textarea id="review-note" name="note" rows="3" placeholder="Optional note about this demo run">${escape(s.review.note || '')}</textarea><label class="check-row"><input name="reviewed" type="checkbox" required><span>I’ve reviewed the results and approve continuing this demo.</span></label><button class="button primary" type="submit">Start image-and-tone task <span>→</span></button></form></section>
    <aside class="panel muted-panel"><p class="eyebrow">PROTOCOL DECISION</p><h2>Eligibility stays open.</h2><p>The study’s screening cutoff, full staircase length, and repeatability rule still need to be defined.</p><p class="small">Main-task gains are fixed across demo sessions. This threshold does not change them.</p>${button('Repeat comparisons', 'repeat-disc')}</aside></div>`);
  }
  if (s.phase === 'main') {
    const trial = s.schedule[s.ratings.length];
    shell('Look. Listen. Tell us how it felt.', 'View the image, hear the tone, then rate its perceived loudness.', `
      <section class="panel task-panel"><div class="panel-top"><span class="pill">IMAGE + TONE</span><span>Trial ${s.ratings.length+1} of ${s.schedule.length}</span></div><div class="progress-track"><span style="width:${s.ratings.length/s.schedule.length*100}%"></span></div>
      ${busy || readyForAnswer ? `<div class="stimulus-frame">${artwork(trial)}</div>` : '<div class="trial-ready"><div class="fixation">+</div><h2>A new visual context.</h2><p>The image appears first. A tone follows one second later.</p></div>'}
      ${readyForAnswer ? `<form id="rating-form"><fieldset class="rating-field"><legend>How loud did the tone feel?</legend><div class="rating-options">${[1,2,3,4,5,6,7].map(value=>`<label><input type="radio" name="rating" value="${value}" required><span>${value}</span></label>`).join('')}</div><div class="scale-labels"><span>Very quiet</span><span>Very loud</span></div></fieldset><div class="center"><button class="button primary" type="submit">${s.ratings.length===s.schedule.length-1?'Finish session':'Save rating & continue'}<span>→</span></button></div></form>` : `<div class="center">${primary(busy?'Playing trial…':'Begin trial', 'main-play', busy?'disabled':'')}</div>`}
      <p class="task-footnote">Supplied images × supplied 70/90-labeled WAVs · playback gain 0.1 · uncalibrated</p></section>`, 'VISUAL CONTEXT / LOUDNESS RATING');
  }
  if (['complete','aborted'].includes(s.phase)) shell(s.phase==='complete'?'Session saved. Thank you.':'Session stopped and saved.', 'Your responses and event log are stored on this computer.', `
    <section class="panel summary-panel"><span class="completion-mark">${s.phase==='complete'?'✓':'Ⅱ'}</span><p class="eyebrow">${s.phase==='complete'?'DEMO COMPLETE':'PARTIAL SESSION'}</p><h2>${escape(session.participant)}</h2><div class="summary-metrics"><div><strong>${s.discrimination.length}</strong><span>comparisons saved</span></div><div><strong>${s.ratings.length}</strong><span>ratings saved</span></div><div><strong>0</strong><span>EEG samples acquired</span></div></div><div class="inline-note">Simulation record. This session contains demo responses and browser event times. The image set and screening estimate require study validation.</div><div class="summary-actions"><a class="button primary" href="/api/sessions/${session.id}/export">Download session ZIP <span>↓</span></a>${button('New participant', 'home')}</div><p class="small storage-path">${escape(station.data_directory)} / sub-${escape(session.participant)} / ses-${escape(session.id)}</p></section>`);
}

async function enterSession(item) {
  session = item;
  readyForAnswer = false;
  busy = false;
  paused = false;
  saveFailure = false;
  saveQueue = Promise.resolve();
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
  render();
  try {
    await save();
    if (signal.signal.aborted) return;
    if (kind==='discrimination') {
      const low = 0.028;
      const high = low * Math.pow(10, task.delta_db / 20);
      const first = tone(task.louder_interval===1?high:low);
      const second = tone(task.louder_interval===2?high:low, 1.3);
      event('audio_pair_scheduled', {attempt_id: task.attempt_id, first_audio_context_s:first, second_audio_context_s:second, measured:false});
      await wait(2430, signal.signal);
    } else {
      const trial = s.schedule[index];
      event('image_requested', {attempt_id:task.attempt_id, image_id:trial.image_id, measured:false});
      await wait(1000, signal.signal);
      const start = fileTone(trial);
      event('tone_scheduled', {attempt_id:task.attempt_id, ...trial, audio_context_s:start, measured:false});
      await wait(1110, signal.signal);
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
  if (correct) {
    s.streak++;
    if (s.streak>=2) { s.delta=Math.max(1,s.delta-1); s.streak=0; }
  } else { s.delta=Math.min(10,s.delta+1); s.streak=0; }
  s.activeTask = null;
  if (s.discrimination.length===12) {
    s.threshold = s.discrimination.slice(-4).reduce((total,item)=>total+item.delta_db,0)/4;
    s.phase='review';
  }
  await save();
  busy = false;
  render();
}

function makeSchedule() {
  const schedule = ['pleasant','neutral','unpleasant'].flatMap(valence => {
    const pool = stimuli.images.filter(image => image.valence === valence);
    // Draw two different images per category without replacement.
    const first = crypto.getRandomValues(new Uint32Array(1))[0] % pool.length;
    const remaining = pool.filter((_, index) => index !== first);
    const second = crypto.getRandomValues(new Uint32Array(1))[0] % remaining.length;
    return ['70','90'].map((level,index) => {
      const image = index === 0 ? pool[first] : remaining[second];
      return {image_id:image.image_id, image_source:'supplied_finalized_image_set',
        image_path:image.path, image_sha256:image.sha256, valence, level,
        audio_path:stimuli.audio[level], gain:0.1};
    });
  });
  for (let i=schedule.length-1; i>0; i--) {
    const j=crypto.getRandomValues(new Uint32Array(1))[0]%(i+1);
    [schedule[i],schedule[j]]=[schedule[j],schedule[i]];
  }
  return schedule;
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
    if (form.id==='review-form') {
      s.review={note:values.get('note').trim(),demo_approved:true,at:new Date().toISOString()};
      s.schedule=makeSchedule(); s.phase='main'; event('demo_review_approved',s.review);
      event('schedule_created',{schedule:s.schedule});
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
    if (action==='disc-play') await playTask('discrimination');
    if (action==='main-play') await playTask('main');
    if (action==='answer-1') await answer(1);
    if (action==='answer-2') await answer(2);
    if (action==='repeat-disc') {
      event('discrimination_repeated',{previous_trials:state().discrimination,previous_estimate:state().threshold});
      Object.assign(state(),{discrimination:[],delta:6,streak:0,threshold:null,phase:'discrimination'});
      await save(); render();
    }
  } catch(error) {busy=false; notify(error.message + (session?' Reload to resume the saved checkpoint.':''));}
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
