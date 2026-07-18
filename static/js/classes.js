import { escapeHtml } from './utils.js';
import { showView } from './views.js';
import { setCurrentClass } from './state.js';
import { loadAssignments } from './assignments.js';

export async function loadClasses() {
  const container = document.getElementById('classList');
  container.innerHTML = '<div class="empty-state">Loading…</div>';
  try {
    const res = await fetch('/classes');
    const data = await res.json();
    if (!data.classes || data.classes.length === 0) {
      container.innerHTML = '<div class="empty-state">No classes yet. Create one below.</div>';
      return;
    }
    container.innerHTML = '';
    data.classes.forEach(name => {
      const el = document.createElement('div');
      el.className = 'list-item';
      el.innerHTML = `<span>${escapeHtml(name)}</span><span class="chevron">›</span>`;
      el.addEventListener('click', () => {
        setCurrentClass(name);
        showView('assignments');
        loadAssignments(name);
      });
      container.appendChild(el);
    });
  } catch (err) {
    container.innerHTML = '<div class="empty-state">Failed to load classes.</div>';
  }
}

// ---- Create class modal ----
let selectedFile = null;

const createClassModal = document.getElementById('createClassModal');
const fileLabel = document.getElementById('fileLabel');
const csvFile = document.getElementById('csvFile');
const confirmClassBtn = document.getElementById('confirmClassBtn');
const createClassError = document.getElementById('createClassError');

document.getElementById('createClassBtn').addEventListener('click', () => {
  selectedFile = null;
  csvFile.value = '';
  fileLabel.textContent = 'Choose CSV file';
  fileLabel.classList.remove('has-file');
  confirmClassBtn.disabled = true;
  createClassError.textContent = '';
  createClassModal.classList.add('active');
});
document.getElementById('cancelClassBtn').addEventListener('click', () => {
  createClassModal.classList.remove('active');
});
csvFile.addEventListener('change', () => {
  if (csvFile.files.length > 0) {
    selectedFile = csvFile.files[0];
    fileLabel.textContent = selectedFile.name;
    fileLabel.classList.add('has-file');
    confirmClassBtn.disabled = false;
  }
});
confirmClassBtn.addEventListener('click', async () => {
  if (!selectedFile) return;
  confirmClassBtn.disabled = true;
  createClassError.textContent = '';
  const formData = new FormData();
  formData.append('file', selectedFile);
  try {
    const res = await fetch('/classes', { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok) {
      createClassError.textContent = data.detail || 'Failed to create class.';
      confirmClassBtn.disabled = false;
      return;
    }
    createClassModal.classList.remove('active');
    loadClasses();
  } catch (err) {
    createClassError.textContent = 'Something went wrong.';
    confirmClassBtn.disabled = false;
  }
});