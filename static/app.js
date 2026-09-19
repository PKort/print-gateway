const form = document.querySelector('#print-form');
const fileInput = document.querySelector('#file');
const fileLabel = document.querySelector('#file-label');
const drop = document.querySelector('#drop-zone');
const submit = document.querySelector('#submit');
const message = document.querySelector('#message');
const printerSelect = document.querySelector('#printer-select');

function selectedPrinter() { return printerSelect.value; }
function updatePrinterControls(applyDefaults = false) {
  const option = printerSelect.selectedOptions[0];
  document.querySelector('#printer-subtitle').textContent = `${option.dataset.label} · A4`;
  document.querySelector('#color-option').hidden = option.dataset.color !== 'true';
  if (applyDefaults) document.querySelector('#duplex-select').value = option.dataset.defaultDuplex;
}

function setFile(file) {
  if (!file) return;
  const dt = new DataTransfer(); dt.items.add(file); fileInput.files = dt.files;
  fileLabel.textContent = file.name; submit.disabled = false;
}
fileInput.addEventListener('change', () => setFile(fileInput.files[0]));
['dragenter','dragover'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.add('drag'); }));
['dragleave','drop'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.remove('drag'); }));
drop.addEventListener('drop', e => setFile(e.dataTransfer.files[0]));

function escapeHtml(value) { const d=document.createElement('div'); d.textContent=value; return d.innerHTML; }
function jobHtml(job, active) {
  return `<div class="job"><strong>${escapeHtml(job.name)}</strong><small>${escapeHtml(job.submitted)}</small>${active ? `<button class="cancel" data-job="${job.id}">Anuluj</button>` : ''}</div>`;
}
async function refresh() {
  try {
    const response = await fetch(`/api/status?printer=${encodeURIComponent(selectedPrinter())}`, { cache:'no-store' });
    const data = await response.json();
    const status = document.querySelector('#status'); status.className = `status ${data.status}`; status.lastElementChild.textContent = data.label;
    const powerState = data.power.state;
    const powerLabels = {on:'Włączona',off:'Wyłączona',unavailable:'Niedostępna',unknown:'Nieznany'};
    document.querySelector('#power-state').textContent = data.power.managed ? (data.power.connected ? (powerLabels[powerState] || powerState) : 'Brak połączenia z HA') : 'Gniazdko jeszcze nieprzypisane';
    document.querySelector('#power-on').disabled = !data.power.managed || !data.power.connected || powerState === 'on';
    document.querySelector('#power-off').disabled = !data.power.managed || !data.power.connected || powerState === 'off';
    document.querySelector('#active-jobs').innerHTML = data.active.length ? data.active.map(j => jobHtml(j,true)).join('') : '<div class="empty">Brak aktywnych zadań</div>';
    document.querySelector('#completed-jobs').innerHTML = data.completed.length ? data.completed.map(j => jobHtml(j,false)).join('') : '<div class="empty">Brak historii</div>';
  } catch { document.querySelector('#status').lastElementChild.textContent = 'Brak połączenia'; }
}

form.addEventListener('submit', async e => {
  e.preventDefault(); submit.disabled = true; submit.textContent = 'Wysyłanie…'; message.className='message'; message.textContent='';
  try {
    const response = await fetch('/api/print', { method:'POST', headers:{'X-CSRF-Token':window.GATEWAY.csrf}, body:new FormData(form) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Nie udało się wysłać dokumentu.');
    const printer = selectedPrinter(); message.className='message success'; message.textContent=`${data.message}${data.job ? ` Numer: ${data.job}.` : ''}`; form.reset(); printerSelect.value=printer; updatePrinterControls(true); fileLabel.textContent='Wybierz PDF lub przeciągnij go tutaj'; await refresh();
  } catch (error) { message.className='message error'; message.textContent=error.message; }
  finally { submit.textContent='Drukuj dokument'; submit.disabled=!fileInput.files.length; }
});
document.addEventListener('click', async e => {
  if (!e.target.matches('[data-job]')) return;
  await fetch(`/api/jobs/${encodeURIComponent(selectedPrinter())}/${e.target.dataset.job}/cancel`, {method:'POST', headers:{'X-CSRF-Token':window.GATEWAY.csrf}}); refresh();
});
document.querySelector('#refresh').addEventListener('click', refresh);
printerSelect.addEventListener('change', () => { updatePrinterControls(true); refresh(); });
async function setPower(action) {
  const on = document.querySelector('#power-on'), off = document.querySelector('#power-off'); on.disabled=true; off.disabled=true;
  message.className='message'; message.textContent=action === 'on' ? 'Włączanie drukarki…' : 'Wyłączanie drukarki…';
  try {
    const response = await fetch('/api/power', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':window.GATEWAY.csrf}, body:JSON.stringify({action,printer:selectedPrinter()})});
    const data = await response.json(); if (!response.ok) throw new Error(data.error);
    message.className='message success'; message.textContent=data.message; setTimeout(refresh, 800);
  } catch(error) { message.className='message error'; message.textContent=error.message; refresh(); }
}
document.querySelector('#power-on').addEventListener('click', () => setPower('on'));
document.querySelector('#power-off').addEventListener('click', () => setPower('off'));
updatePrinterControls(); refresh(); setInterval(refresh, 5000);
