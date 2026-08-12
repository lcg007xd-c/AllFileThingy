const CHUNK_SIZE = 8 * 1024 * 1024;
const MAX_RETRIES = 5;
const picker = document.querySelector('#files');
const clipList = document.querySelector('#clip-list');
const uploadList = document.querySelector('#upload-list');
const uploadButton = document.querySelector('#upload');
const errorBox = document.querySelector('#error');
const totals = document.querySelector('#totals');
const pickerView = document.querySelector('#picker-view');
const statusView = document.querySelector('#status-view');
const progressBar = document.querySelector('#progress-bar');
const percent = document.querySelector('#percent');
const phase = document.querySelector('#phase');
const statusMessage = document.querySelector('#status-message');
let files = [];
let job = null;
let active = false;

const sizeLabel = (bytes) => bytes < 1024 ** 2
  ? `${Math.ceil(bytes / 1024)} KB`
  : `${(bytes / 1024 ** 2).toFixed(1)} MB`;

function setProgress(value) {
  const bounded = Math.max(0, Math.min(100, value));
  progressBar.style.width = `${bounded}%`;
  percent.textContent = `${Math.round(bounded)}%`;
  progressBar.parentElement.setAttribute('aria-valuenow', String(Math.round(bounded)));
}

function renderSelection() {
  clipList.replaceChildren();
  files.forEach((file, index) => {
    const item = document.createElement('li');
    item.className = 'clip';
    const details = document.createElement('div');
    const name = document.createElement('strong');
    name.textContent = file.name;
    const size = document.createElement('span');
    size.className = 'muted';
    size.textContent = sizeLabel(file.size);
    details.append(name, size);
    const controls = document.createElement('div');
    controls.className = 'order-controls';
    [['↑', -1, 'Move up'], ['↓', 1, 'Move down']].forEach(([symbol, delta, label]) => {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'icon-button'; button.textContent = symbol;
      button.setAttribute('aria-label', `${label}: ${file.name}`);
      button.disabled = index + delta < 0 || index + delta >= files.length || active;
      button.onclick = () => { [files[index], files[index + delta]] = [files[index + delta], files[index]]; renderSelection(); };
      controls.append(button);
    });
    item.append(details, controls); clipList.append(item);
  });
  const total = files.reduce((sum, file) => sum + file.size, 0);
  totals.textContent = files.length ? `${files.length} clip${files.length === 1 ? '' : 's'} · ${sizeLabel(total)}` : '';
  uploadButton.disabled = files.length === 0 || active;
}

picker.addEventListener('change', () => {
  files = Array.from(picker.files || []);
  errorBox.textContent = '';
  renderSelection();
});

async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 401) { location.href = '/login'; throw new Error('Login required'); }
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    const reason = new Error(message); reason.status = response.status; throw reason;
  }
  return response.status === 204 ? null : response.json();
}

function renderUploads(offsets) {
  uploadList.replaceChildren();
  job.files.forEach((record, index) => {
    const item = document.createElement('li');
    const row = document.createElement('div'); row.className = 'status-head';
    const name = document.createElement('span'); name.textContent = record.display_name;
    const value = document.createElement('span'); value.textContent = `${Math.round((offsets[index] / record.expected_size) * 100)}%`;
    row.append(name, value);
    const bar = document.createElement('div'); bar.className = 'progress small';
    const fill = document.createElement('span'); fill.style.width = `${(offsets[index] / record.expected_size) * 100}%`;
    bar.append(fill); item.append(row, bar); uploadList.append(item);
  });
}

const wait = (milliseconds) => new Promise(resolve => setTimeout(resolve, milliseconds));

async function uploadFile(file, record, index, offsets) {
  let offset = (await api(`/api/jobs/${job.id}/files/${record.id}/offset`)).offset;
  offsets[index] = offset;
  while (offset < file.size) {
    const end = Math.min(file.size, offset + CHUNK_SIZE);
    let failure;
    for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
      try {
        const result = await api(`/api/jobs/${job.id}/files/${record.id}/chunks`, {
          method: 'PUT',
          headers: {'Content-Type': 'application/octet-stream', 'Upload-Offset': String(offset)},
          body: file.slice(offset, end)
        });
        offset = result.offset; failure = null; break;
      } catch (reason) {
        failure = reason;
        if (![408, 409, 425, 429, 500, 502, 503, 504].includes(reason.status) || attempt === MAX_RETRIES) throw reason;
        await wait(Math.min(8000, 400 * (2 ** attempt)) + Math.random() * 250);
        offset = (await api(`/api/jobs/${job.id}/files/${record.id}/offset`)).offset;
        if (offset >= end) { failure = null; break; }
      }
    }
    if (failure) throw failure;
    offsets[index] = offset;
    const sent = offsets.reduce((sum, value) => sum + value, 0);
    const total = job.files.reduce((sum, value) => sum + value.expected_size, 0);
    setProgress(sent / total * 100); renderUploads(offsets);
  }
}

uploadButton.addEventListener('click', async () => {
  active = true; errorBox.textContent = ''; renderSelection();
  try {
    job = await api('/api/jobs', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({files: files.map(file => ({name: file.name, size: file.size, type: file.type}))})
    });
    localStorage.setItem('allfilethingy_job', job.id);
    pickerView.hidden = true; statusView.hidden = false;
    const offsets = files.map(() => 0); renderUploads(offsets);
    for (let index = 0; index < files.length; index++) {
      statusMessage.textContent = `Uploading ${index + 1} of ${files.length}`;
      await uploadFile(files[index], job.files[index], index, offsets);
    }
    job = await api(`/api/jobs/${job.id}`);
    phase.textContent = 'Ready'; statusMessage.textContent = 'Every clip is uploaded and ready to stitch.'; setProgress(100);
  } catch (reason) {
    errorBox.textContent = reason.message;
    if (!pickerView.hidden) active = false;
    statusMessage.textContent = `Upload paused: ${reason.message}. Choose start over or retry this page with the same files.`;
    renderSelection();
  }
});

document.querySelector('#start-over').addEventListener('click', async () => {
  if (job && !confirm('Delete this job and all uploaded clips?')) return;
  if (job) await api(`/api/jobs/${job.id}`, {method: 'DELETE'});
  localStorage.removeItem('allfilethingy_job'); location.reload();
});

document.querySelector('#logout').onclick = async () => { await api('/api/logout', {method:'POST'}); location.href='/login'; };
window.addEventListener('beforeunload', (event) => { if (active && job?.state !== 'ready') { event.preventDefault(); event.returnValue = ''; } });

