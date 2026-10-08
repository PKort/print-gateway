const form = document.querySelector('#print-form');
const fileInput = document.querySelector('#file');
const fileLabel = document.querySelector('#file-label');
const drop = document.querySelector('#drop-zone');
const submit = document.querySelector('#submit');
const message = document.querySelector('#message');
const printerSelect = document.querySelector('#printer-select');
const preview = document.querySelector('#preview');
const previewFrame = document.querySelector('#preview-frame');
const imagePreview = document.querySelector('#image-preview');
const documentToken = document.querySelector('#document-token');
const documentName = document.querySelector('#document-name');
const imageLayout = document.querySelector('#image-layout');
const imageLayoutMode = document.querySelector('#image-layout-mode');
const imageHorizontal = document.querySelector('#image-horizontal');
const imageVertical = document.querySelector('#image-vertical');

function selectedPrinter() { return printerSelect.value; }
function updatePrinterControls(applyDefaults = false) {
  const option = printerSelect.selectedOptions[0];
  document.querySelector('#printer-subtitle').textContent = `${option.dataset.label} · ${option.dataset.location} · A4`;
  document.querySelector('#color-option').hidden = option.dataset.color !== 'true';
  document.querySelector('#power-card').hidden = option.dataset.remotePower !== 'true';
  if (applyDefaults) document.querySelector('#duplex-select').value = option.dataset.defaultDuplex;
}

function resetDocument() {
  fileInput.value=''; documentToken.value=''; documentName.value=''; previewFrame.src='about:blank'; imagePreview.removeAttribute('src'); imagePreview.hidden=true; previewFrame.hidden=false; preview.hidden=true; imageLayout.hidden=true; drop.hidden=false;
  fileLabel.textContent='Wybierz dokument lub przeciągnij go tutaj'; submit.disabled=true;
}
async function setFile(file) {
  if (!file) return;
  submit.disabled=true; fileLabel.textContent=file.name; message.className='message'; message.textContent='Przygotowywanie podglądu…';
  const body=new FormData(); body.append('file',file);
  try {
    const response=await fetch('/api/documents',{method:'POST',headers:{'X-CSRF-Token':window.GATEWAY.csrf},body});
    const data=await response.json(); if(!response.ok) throw new Error(data.error || 'Nie udało się przygotować dokumentu.');
    documentToken.value=data.token; documentName.value=data.name; document.querySelector('#preview-name').textContent=data.name;
    document.querySelector('#preview-meta').textContent=`${data.pages} ${data.pages===1?'strona':'stron'} · ${(data.size/1024/1024).toFixed(1)} MB${data.converted?' · przekonwertowano do PDF':''}`;
    imageLayout.hidden=!data.image;
    if(data.image) { imageLayoutMode.value='contain'; imageHorizontal.value='center'; imageVertical.value='center'; updateImageInfo(data.imageInfo); imagePreview.src=data.imagePreviewUrl; imagePreview.hidden=false; previewFrame.hidden=true; }
    else { previewFrame.src=data.previewUrl; imagePreview.hidden=true; previewFrame.hidden=false; }
    document.querySelector('#preview-open').href=data.previewUrl; drop.hidden=true; preview.hidden=false; submit.disabled=false;
    message.textContent='Sprawdź podgląd, a następnie zatwierdź drukowanie.';
  } catch(error) { resetDocument(); message.className='message error'; message.textContent=error.message; }
}
fileInput.addEventListener('change', () => setFile(fileInput.files[0]));
['dragenter','dragover'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.add('drag'); }));
['dragleave','drop'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.remove('drag'); }));
drop.addEventListener('drop', e => setFile(e.dataTransfer.files[0]));

function updateImageInfo(info) {
  if(!info) return;
  document.querySelector('#image-info').textContent=`${info.width} × ${info.height} px · ${Math.round(info.dpiX)} DPI · wydruk ${info.printWidthCm} × ${info.printHeightCm} cm · A4 ${info.pageOrientation}`;
}
let layoutRequest=0;
async function updateImageLayout() {
  if(!documentToken.value || imageLayout.hidden) return;
  const requestId=++layoutRequest; submit.disabled=true; message.className='message'; message.textContent='Aktualizowanie podglądu…';
  try {
    const response=await fetch(`/api/documents/${documentToken.value}/layout`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':window.GATEWAY.csrf},body:JSON.stringify({layout:imageLayoutMode.value,horizontal:imageHorizontal.value,vertical:imageVertical.value})});
    const data=await response.json(); if(!response.ok) throw new Error(data.error || 'Nie udało się zmienić układu zdjęcia.');
    if(requestId!==layoutRequest) return; updateImageInfo(data.imageInfo); imagePreview.src=data.imagePreviewUrl; document.querySelector('#preview-open').href=data.previewUrl;
    message.textContent='Sprawdź zaktualizowany podgląd, a następnie zatwierdź drukowanie.';
  } catch(error) { if(requestId===layoutRequest) { message.className='message error'; message.textContent=error.message; } }
  finally { if(requestId===layoutRequest) submit.disabled=!documentToken.value; }
}
[imageLayoutMode,imageHorizontal,imageVertical].forEach(control=>control.addEventListener('change',updateImageLayout));

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
    document.querySelector('#power-card').hidden = !data.power.managed;
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
    const printer = selectedPrinter(); message.className='message success'; message.textContent=`${data.message}${data.job ? ` Numer: ${data.job}.` : ''}`; form.reset(); printerSelect.value=printer; updatePrinterControls(true); resetDocument(); await refresh();
  } catch (error) { message.className='message error'; message.textContent=error.message; }
  finally { submit.textContent='Drukuj dokument'; submit.disabled=!documentToken.value; }
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
document.querySelector('#remove-document').addEventListener('click', resetDocument);
updatePrinterControls(); refresh(); setInterval(refresh, 5000);
