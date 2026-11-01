const $ = id => document.getElementById(id);
let config, currentId = null, timer = null, seenEvents = 0, memoryKey = '', active = false;
let elapsedBase = 0, elapsedUpdatedAt = 0;
const terminal = new Set(['finished','partial','failed','cancelled','demo','interrupted']);
function updateElapsed() {
  const seconds = Math.max(0, Math.floor(elapsedBase + (active ? (performance.now() - elapsedUpdatedAt) / 1000 : 0)));
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60);
  $('metric-elapsed').textContent = [hours, minutes, seconds % 60].map(x=>String(x).padStart(2,'0')).join(':');
}
setInterval(updateElapsed, 1000);
function error(message) { $('error').textContent = message; $('error').hidden = !message; }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':config.session_token},body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Please check the form values and try again.');
  return data;
}
function settings() { $('api-key').value = memoryKey; $('settings').showModal(); }
$('settings-button').onclick = settings; $('configure-key').onclick = settings;
$('settings').addEventListener('close', () => {
  if ($('settings').returnValue === 'save') memoryKey = $('api-key').value.trim();
  $('api-key').value = '';
  updateKey();
});
function updateKey() {
  $('key-notice').hidden = config.key_configured || Boolean(memoryKey);
  $('key-state').textContent = config.key_configured ? 'A key is already configured on the server. Leave blank to use it.' : memoryKey ? 'A key is available for this browser session.' : 'You can also configure OPENAI_API_KEY in the project’s .env file.';
}
$('options-button').onclick = () => { $('options').hidden = !$('options').hidden; };
$('research-form').onsubmit = event => { event.preventDefault(); start(false); };
$('demo-button').onclick = () => start(true);
async function start(demo) {
  error('');
  if (!demo && !config.key_configured && !memoryKey) { settings(); return; }
  $('start-button').disabled = true; $('demo-button').disabled = true;
  try {
    const data = await api('api/runs', {company:$('company').value.trim() || (demo ? 'Example Company' : ''),demo,
      api_key:memoryKey || null, months:Number($('months').value)});
    selectRun(data.id); render(data); schedule();
  } catch (e) { error(e.message); $('start-button').disabled = active; $('demo-button').disabled = active; }
}
function selectRun(id) {
  clearTimeout(timer); currentId = id; seenEvents = 0; $('events').replaceChildren();
  $('workspace').hidden = false; $('empty-state').hidden = true; $('results').hidden = true;
  localStorage.setItem('webagent-last-run', id);
}
function schedule() { clearTimeout(timer); timer = setTimeout(poll, 1200); }
async function poll() {
  if (!currentId) return;
  const id = currentId;
  try { const data = await api(`api/runs/${id}`); if (id !== currentId) return; render(data); if (!terminal.has(data.status)) schedule(); else loadHistory(); }
  catch (e) { error('Connection interrupted. Your files remain on disk. ' + e.message); schedule(); }
}
function safeLink(url) { try { const u = new URL(url); return ['http:','https:'].includes(u.protocol) ? u.href : null; } catch { return null; } }
function render(run) {
  active = !terminal.has(run.status);
  $('start-button').disabled = active; $('demo-button').disabled = active;
  $('stop-button').hidden = !active; $('working').hidden = !active || run.status === 'needs_input';
  $('run-title').textContent = run.identity.name || run.company;
  $('run-window').textContent = `${run.start_date} — ${run.end_date} · publication dates`;
  $('run-eyebrow').textContent = active ? 'COLLECTION IN PROGRESS' : 'COLLECTION RESULTS';
  $('status').textContent = ({needs_input:'Needs clarification',finished:'Collected',partial:'Partial',demo:'Demo complete',interrupted:'Interrupted'})[run.status] || run.status;
  $('status').className = `badge ${run.status}`;
  $('stage').textContent = run.stage;
  $('demo-banner').hidden = !run.demo;
  $('metric-docs').textContent = run.documents.filter(d=>d.status === 'saved').length;
  const savedFiles = run.documents.filter(d=>d.status === 'saved');
  const webpages = savedFiles.filter(d=>/\.html?$/i.test(d.file || '')).length;
  $('file-breakdown').textContent = `${webpages} webpage copies · ${savedFiles.length - webpages} document files`;
  $('metric-pages').textContent = run.pages.length;
  $('metric-searches').textContent = run.events.filter(e=>e.kind === 'search').length;
  const finished = run.finished_at || (!active ? run.events.at(-1)?.time || run.started_at : null);
  elapsedBase = Number.isFinite(run.elapsed_seconds) ? run.elapsed_seconds : Math.max(0, ((finished ? Date.parse(finished) : Date.now()) - Date.parse(run.started_at)) / 1000);
  elapsedUpdatedAt = performance.now();
  $('metric-elapsed').title = run.elapsed_estimated || (!active && !run.finished_at) ? 'Estimated duration from the last recorded activity in this older run.' : 'Elapsed time since collection started, including time awaiting clarification.';
  updateElapsed();
  $('count-actions').textContent = run.step;
  const queue = run.queue_counts;
  $('queue-progress').hidden = !queue?.total;
  if (queue?.total) $('queue-progress').textContent = `Collection queue: ${queue.announcements} announcement/document entries · ${queue.attachments} attachments · ${queue.archive_pages} archive pages — ${queue.completed} processed, ${queue.pending + queue.processing} remaining, ${queue.failed} failed, ${queue.excluded_date} outside date window${queue.interrupted ? `, ${queue.interrupted} interrupted` : ''}.`;
  const counts = run.counts || {identified:run.documents.length, attempted:run.documents.filter(d=>['saved','duplicate','failed'].includes(d.status)).length,
    duplicates:run.documents.filter(d=>d.status === 'duplicate').length, failed:run.documents.filter(d=>d.status === 'failed').length,
    date_review:run.documents.filter(d=>d.date_status === 'needs_review').length, excluded:run.documents.filter(d=>d.status === 'excluded_date').length};
  for (const [id,key] of Object.entries({identified:'identified',attempted:'attempted',duplicates:'duplicates',failed:'failed',review:'date_review',excluded:'excluded'})) $('count-'+id).textContent = counts[key];
  $('clarification').hidden = run.status !== 'needs_input'; $('question').textContent = run.question || '';
  const list = $('events'), nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 65;
  for (const item of run.events.slice(seenEvents)) {
    const li = document.createElement('li'); li.className = item.kind;
    const time = document.createElement('time'); time.textContent = new Date(item.time).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});
    li.append(time, document.createTextNode(item.message));
    if (safeLink(item.url)) { const a = document.createElement('a'); a.href = safeLink(item.url); a.target = '_blank'; a.rel = 'noopener noreferrer'; a.textContent = item.url; li.append(a); }
    list.append(li);
  }
  seenEvents = run.events.length; if (nearBottom) list.scrollTop = list.scrollHeight;
  $('documents').replaceChildren(); $('documents-empty').hidden = run.documents.length > 0;
  $('document-count').textContent = `${run.documents.length} records`;
  for (const doc of run.documents) {
    const li = document.createElement('li'), icon = document.createElement('span'), box = document.createElement('div');
    icon.className = 'file-icon'; icon.textContent = doc.file ? doc.file.split('.').pop().toUpperCase().slice(0,5) : 'LINK';
    const title = document.createElement(doc.file ? 'a' : 'span'); title.className = 'doc-title'; title.textContent = doc.title;
    if (doc.file) { title.href = `api/runs/${run.id}/files/${doc.file.split('/').map(encodeURIComponent).join('/')}`; title.setAttribute('download',''); }
    const meta = document.createElement('div'); meta.className = 'doc-meta';
    meta.textContent = `${/\.html?$/i.test(doc.file || '') ? 'Webpage copy' : 'Document'} · ${doc.category.replaceAll('_',' ')} · ${doc.published_date || (run.demo ? 'Synthetic sample' : 'Date needs review')} · ${doc.status.replaceAll('_',' ')}`;
    if (doc.date_status === 'needs_review' || doc.status === 'failed') meta.classList.add('doc-review');
    box.append(title,meta);
    if (doc.error) { const reason = document.createElement('div'); reason.className = 'doc-meta doc-review'; reason.textContent = doc.error; box.append(reason); }
    if (doc.date_note) { const note = document.createElement('div'); note.className='doc-meta doc-review'; note.textContent=doc.date_note; box.append(note); }
    if (doc.attempts?.length) { const attempts = document.createElement('div'); attempts.className='doc-meta'; attempts.textContent=`${doc.attempts.length} download attempt${doc.attempts.length === 1 ? '' : 's'}`; box.append(attempts); }
    if (safeLink(doc.source_url)) { const source = document.createElement('a'); source.href=safeLink(doc.source_url); source.target='_blank'; source.rel='noopener noreferrer'; source.className='doc-meta'; source.textContent='View source page ↗'; box.append(source); }
    li.append(icon,box); $('documents').append(li);
  }
  $('results').hidden = active;
  if (!active) {
    $('result-heading').textContent = run.status === 'failed' ? 'Collection stopped with an error' : run.demo ? 'Your sample folder is ready' : 'Your research folder is ready';
    $('summary').textContent = run.summary;
    $('folder-path').textContent = run.folder;
    $('download-zip').href = `api/runs/${run.id}/zip`;
    $('download-catalogue').href = `api/runs/${run.id}/files/catalogue.json`;
    $('issues-box').hidden = !run.issues.length; $('issues-label').textContent = `${run.issues.length} items to review`;
    $('issues').replaceChildren(...run.issues.map(x => { const li = document.createElement('li'); li.textContent = x; return li; }));
    $('archives').replaceChildren(...Object.entries(run.archives).map(([url,a]) => {
      const li = document.createElement('li'), pages = run.pages.filter(p=>p.url === url);
      const scans = pages.flatMap(p=>p.link_scans || []);
      const enumerated = new Set((a.scans || []).map(s=>s.observation_id)).size;
      li.textContent = `${a.category}: ${a.status} (coverage reviewed by agent) — ${a.evidence} · ${enumerated} full-page enumerations; ${pages.length} direct snapshots; ${scans.length} link searches · ${url}`;
      if (scans.length) { const detail = document.createElement('div'); detail.textContent = scans.map(s=>`${s.query || 'All links'}: ${s.offset}–${s.offset+s.returned} of ${s.total}`).join('; '); li.append(detail); }
      return li;
    }));
    if (!Object.keys(run.archives).length) { const li = document.createElement('li'); li.textContent = 'No archive coverage recorded.'; $('archives').append(li); }
  }
}
$('stop-button').onclick = async () => { try { await api(`api/runs/${currentId}/cancel`, {}); schedule(); } catch(e) { error(e.message); } };
$('answer-form').onsubmit = async event => { event.preventDefault(); try { await api(`api/runs/${currentId}/answer`, {answer:$('answer').value}); $('answer').value=''; $('clarification').hidden=true; schedule(); } catch(e) { error(e.message); } };
$('open-folder').onclick = async () => { try { await api(`api/runs/${currentId}/open-folder`, {}); } catch(e) { error(e.message); } };
async function loadHistory() {
  const history = await api('api/runs'); $('history-section').hidden = !history.length; $('history').replaceChildren();
  for (const run of history.slice(0,6)) { const button = document.createElement('button'); button.textContent = run.company + (run.demo ? ' · sample' : ''); const date = document.createElement('small'); date.textContent = run.started_at.slice(0,10); button.append(date); button.onclick = async () => { if (active) return; selectRun(run.id); await poll(); }; $('history').append(button); }
}
async function init() {
  try {
    config = await api('api/config'); $('model-label').textContent = config.model; $('settings-model').textContent = config.model;
    $('output-root').textContent = config.output_root; updateKey(); await loadHistory();
    const saved = localStorage.getItem('webagent-last-run');
    if (saved) { try { const run = await api(`api/runs/${saved}`); selectRun(saved); render(run); if (!terminal.has(run.status)) schedule(); } catch { localStorage.removeItem('webagent-last-run'); } }
  } catch(e) { error('Unable to connect to the local server. ' + e.message); }
}
init();
