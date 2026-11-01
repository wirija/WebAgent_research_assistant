const $ = id => document.getElementById(id);
let config, memoryKey = '', current = null, currentId = null, pollTimer, active = false, selectedTab = 'profile';
let elapsed = 0, elapsedAnchor = performance.now();
// Optional presentation-layer handoff. Standalone use has no parent dependency.
window.addEventListener('message', event => {
  if (window.parent === window || event.source !== window.parent || event.origin !== location.origin) return;
  if (event.data?.type !== 'workspace:source-folder' || typeof event.data.folder !== 'string') return;
  if (!active) {
    $('source-folder').value = event.data.folder;
    $('source-folder').scrollIntoView({behavior:'smooth', block:'center'});
    $('year-end').focus({preventScroll:true});
  }
  window.parent.postMessage({type:'module:source-selected', accepted:!active}, location.origin);
});
const text = (tag, value, className) => { const node = document.createElement(tag); node.textContent = value; if(className) node.className = className; return node; };
function error(message) { $('error').textContent = message; $('error').hidden = !message; }
async function api(path, body) {
  const result = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':config.session_token},body:JSON.stringify(body)});
  const data = await result.json();
  if(!result.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Check the input folder and dates.');
  return data;
}
function settings() { $('key').value = memoryKey; $('key-state').textContent = config.key_configured ? 'A key is configured for this module. Leave blank to use it.' : 'You can also set OPENAI_API_KEY in this module’s .env file.'; $('settings').showModal(); }
$('settings-button').onclick = settings;
$('settings').addEventListener('close', () => { if($('settings').returnValue === 'save') memoryKey = $('key').value.trim(); $('key').value = ''; });
$('start-form').onsubmit = async event => { event.preventDefault(); await start(false); };
$('demo-button').onclick = () => start(true);
async function start(demo) {
  error('');
  if(!demo && !config.key_configured && !memoryKey) { settings(); return; }
  $('start-button').disabled = true; $('demo-button').disabled = true;
  try {
    const run = await api(demo ? 'api/demo' : 'api/jobs', demo ? {} : {source_folder:$('source-folder').value.trim(),financial_year_end:$('year-end').value,information_cutoff:$('cutoff').value,api_key:memoryKey || null});
    currentId = run.id; localStorage.setItem('understanding-last-job', currentId); selectedTab = 'profile'; render(run); schedule();
  } catch(e) { error(e.message); $('start-button').disabled = active; $('demo-button').disabled = active; }
}
function schedule() { clearTimeout(pollTimer); pollTimer = setTimeout(poll, 1200); }
async function poll() {
  const id = currentId;
  try { const run = await api(`api/jobs/${id}`); if(currentId !== id) return; render(run); if(active) schedule(); else await history(); }
  catch(e) { error(e.message); if(active) schedule(); }
}
function timer() {
  const seconds = Math.max(0, Math.floor(elapsed + (active ? (performance.now()-elapsedAnchor)/1000 : 0)));
  $('elapsed').textContent = [Math.floor(seconds/3600),Math.floor(seconds%3600/60),seconds%60].map(n=>String(n).padStart(2,'0')).join(':');
}
setInterval(timer,1000);
function render(run) {
  current = run; active = run.status === 'running'; elapsed = run.elapsed_seconds || 0; elapsedAnchor = performance.now(); timer();
  $('job').hidden = false; $('how').hidden = true; $('company').textContent = run.company;
  $('dates').textContent = `Financial year-end: ${run.financial_year_end} · Information cutoff: ${run.information_cutoff}`;
  $('status').textContent = run.status; $('stage').textContent = run.stage; $('stop').hidden = !active;
  $('start-button').disabled = active; $('demo-button').disabled = active; $('demo-notice').hidden = !run.demo;
  $('documents-count').textContent = run.documents.filter(x=>x.status === 'processed').length;
  $('chunks-count').textContent = `${run.progress.chunks_processed} / ${run.progress.chunks_total}`;
  $('facts-count').textContent = run.facts.length;
  const nearBottom = $('events').scrollHeight - $('events').scrollTop - $('events').clientHeight < 60;
  $('events').replaceChildren(...run.events.map(e=>{const li=text('li',''); li.append(text('time',new Date(e.time).toLocaleTimeString()),text('span',e.message)); return li;}));
  if(nearBottom) $('events').scrollTop = $('events').scrollHeight;
  $('outputs').hidden = active; $('output-folder').textContent = run.folder;
  $('report').href = `api/jobs/${run.id}/files/report.html`; $('zip').href = `api/jobs/${run.id}/zip`; $('json').href = `api/jobs/${run.id}/files/job.json`;
  $('issues-panel').hidden = !run.issues.length; $('issues-title').textContent = `${run.issues.length} items to review`;
  $('issues').replaceChildren(...run.issues.map(issue=>text('li',issue)));
  if(!active) renderItems();
}
function evidence(item, box) {
  const cites = item.citations || [];
  if(!cites.length) { box.append(text('p','No direct supporting quotation is attached. Treat this as an open question or unconfirmed exposure.','review')); return; }
  const details = document.createElement('details'); details.append(text('summary',`${cites.length} source references — inspect evidence`));
  for(const cite of cites) {
    const block = text('blockquote',cite.quote,'quote');
    block.append(document.createElement('br'));
    const a = text('a',`${cite.source_file} · ${cite.location}`);
    a.href = `api/jobs/${current.id}/sources/${encodeURIComponent(cite.document_id)}`; a.setAttribute('download','');
    block.append(a,text('p',`${cite.chunk_id} · Published: ${cite.published_date || 'unknown'} · Availability: ${cite.availability}`,'hint')); details.append(block);
  }
  box.append(details);
}
function renderItems() {
  for(const button of document.querySelectorAll('[data-tab]')) button.classList.toggle('selected',button.dataset.tab === selectedTab);
  const items = selectedTab === 'coverage' ? current.documents : current[selectedTab];
  $('items').replaceChildren();
  if(!items?.length) { $('items').append(text('p','No supported items were established in this section. Review the open questions and document coverage.','empty')); return; }
  for(const item of items) {
    const box = text('article','','card finding');
    if(selectedTab === 'coverage') {
      box.append(text('h3',item.title),text('p',`${item.status} · ${item.pages} pages/sections · ${item.chunks} text chunks`),text('p',`Publication: ${item.published_date || 'unknown'} · ${item.file || 'No file saved'}`,'hint'));
      if(item.reason) box.append(text('p',item.reason,'review'));
      for(const note of item.issues) box.append(text('p',note,'review'));
      const notes=(current.document_notes || []).filter(n=>n.document_id === item.id);
      if(notes.length) box.append(text('p',notes.map(n=>`${n.document_type}: ${n.reporting_period} (${n.entity_scope})`).join(' · '),'hint'));
    } else if(['question_coverage','evidence_comparisons','claim_checks'].includes(selectedTab)) {
      box.append(text('h3',item.question || item.statement || item.kind?.replaceAll('_',' ')),text('p',(item.status || item.kind || '').replaceAll('_',' ')),text('p',item.answer || item.explanation || item.reason));
      for(const gap of item.missing_information || []) box.append(text('p',gap,'review'));
      if(item.citations) evidence(item,box);
      for(const check of item.independent_answers || []) {box.append(text('p',check.answer)); evidence(check,box);}
    } else if(selectedTab === 'open_questions') {
      box.append(text('h3',item.question),text('p',item.reason,'hint'));
    } else {
      const meta = text('div','','meta'); meta.append(text('span',(item.area || 'potential exposure').replaceAll('_',' '),'tag'),text('span',item.kind.replaceAll('_',' '),'tag'));
      if(item.period) meta.append(text('span',`${item.period} · ${item.entity_scope}`));
      box.append(meta,text('h3',item.title),text('p',item.statement || item.explanation));
      if(item.evidence_status) box.append(text('p',item.evidence_status.replaceAll('_',' '),'hint'));
      for(const note of item.uncertainties || []) box.append(text('p','Unknown: '+note,'review'));
      const review = item.review || {status:'unreviewed',reason:'This item has not completed the review pass.'};
      box.append(text('p',`${review.status === 'supported' ? 'Automatic evidence review passed' : 'Needs review'}: ${review.reason}`,'review'));
      evidence(item,box);
    }
    $('items').append(box);
  }
}
for(const button of document.querySelectorAll('[data-tab]')) button.onclick = () => {selectedTab=button.dataset.tab; if(current) renderItems();};
$('stop').onclick = async () => {try {await api(`api/jobs/${currentId}/cancel`,{}); schedule();} catch(e) {error(e.message);}};
$('open-folder').onclick = async () => {try {await api(`api/jobs/${currentId}/open-folder`,{});} catch(e) {error(e.message);}};
async function history() {
  const rows = await api('api/jobs'); $('history').replaceChildren();
  for(const row of rows) {const button=text('button',row.company); button.append(text('small',`${row.financial_year_end} · ${row.status}${row.demo ? ' · sample' : ''}`)); button.onclick=async()=>{if(active) return; currentId=row.id; localStorage.setItem('understanding-last-job',currentId); await poll();}; $('history').append(button);}
  if(!rows.length) $('history').append(text('p','No jobs yet. Start with a collection folder or try the sample.','hint'));
}
async function init() {
  try {config=await api('api/config'); $('model').textContent=config.model; $('cutoff').value=config.today; await history();
    const id=localStorage.getItem('understanding-last-job'); if(id) {currentId=id; try {render(await api(`api/jobs/${id}`)); if(active) schedule();} catch {localStorage.removeItem('understanding-last-job');}}
    if(window.parent !== window) window.parent.postMessage({type:'module:ready'}, location.origin);
  } catch(e) {error('Could not connect to Module 2. '+e.message);}
}
init();
