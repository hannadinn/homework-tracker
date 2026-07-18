import { escapeHtml } from './utils.js';
import { getCurrentClass, getCurrentAssignment } from './state.js';

const NO_DATA_COLOR = 'rgb(142, 142, 147)'; // matches the #8e8e93 gray used elsewhere
const SUMMARY_THRESHOLD = 5; // 5 or fewer -> list each student; 6+ -> group by status with counts

// Fixed display order for the confirmation modal, regardless of the order
// assignableStatuses comes back from the API in.
const CONFIRM_STATUS_ORDER = ['Not Submitted', 'Late', 'Incomplete', 'On Time'];

let allStudents = [];        // [{name, status}]
let statusColors = {};       // {status: {red, green, blue}} (0-1 floats, from API)
let assignableStatuses = []; // e.g. ["On Time", "Late", "Not Submitted", "Incomplete"]
let pendingChanges = {};     // name -> newly chosen status
let activeStudent = null;

const studentSearch = document.getElementById('studentSearch');
const studentList = document.getElementById('studentList');
const statusSubmitBtn = document.getElementById('statusSubmitBtn');
const statusClearBtn = document.getElementById('statusClearBtn');
const pendingCountEl = document.getElementById('pendingCount');

const statusPickerModal = document.getElementById('statusPickerModal');
const statusPickerName = document.getElementById('statusPickerName');
const statusPickerButtons = document.getElementById('statusPickerButtons');

const clearConfirmModal = document.getElementById('clearConfirmModal');

const submitConfirmModal = document.getElementById('submitConfirmModal');
const submitConfirmTitle = document.getElementById('submitConfirmTitle');
const submitConfirmList = document.getElementById('submitConfirmList');

function rgbToCss(c) {
  if (!c) return NO_DATA_COLOR;
  const r = Math.round((c.red || 0) * 255);
  const g = Math.round((c.green || 0) * 255);
  const b = Math.round((c.blue || 0) * 255);
  return `rgb(${r}, ${g}, ${b})`;
}

function colorForStatus(status) {
  if (status === 'No Data') return NO_DATA_COLOR;
  return rgbToCss(statusColors[status]);
}

export async function loadStudentStatus(className, assignmentName) {
  studentList.innerHTML = '<div class="empty-state">Loading…</div>';
  pendingChanges = {};
  updateActionButtons();
  try {
    const res = await fetch(`/classes/${encodeURIComponent(className)}/assignments/${encodeURIComponent(assignmentName)}/status`);
    const data = await res.json();
    if (!res.ok) {
      studentList.innerHTML = `<div class="empty-state">${escapeHtml(data.detail || 'Failed to load students.')}</div>`;
      return;
    }
    allStudents = data.students;
    statusColors = data.status_colors;
    assignableStatuses = data.assignable_statuses;
    studentSearch.value = '';
    renderStudentList('');
  } catch (err) {
    studentList.innerHTML = '<div class="empty-state">Failed to load students.</div>';
  }
}

function buildStudentCard(s) {
  const effectiveStatus = pendingChanges[s.name] || s.status;
  const card = document.createElement('div');
  card.className = 'status-card';
  card.style.background = colorForStatus(effectiveStatus);
  card.innerHTML = `
    <span class="status-name">${escapeHtml(s.name)}</span>
    <span class="status-value">${escapeHtml(effectiveStatus)}</span>
  `;
  card.addEventListener('click', () => openStatusPicker(s));
  return card;
}

function renderStudentList(filterText) {
  const q = (filterText || '').trim().toLowerCase();
  const filtered = allStudents.filter(s => s.name.toLowerCase().includes(q));

  if (filtered.length === 0) {
    studentList.innerHTML = '<div class="empty-state">No matching students.</div>';
    return;
  }

  const modifiedNames = new Set(Object.keys(pendingChanges));
  const modified = filtered.filter(s => modifiedNames.has(s.name));
  const unmodified = filtered.filter(s => !modifiedNames.has(s.name));

  studentList.innerHTML = '';

  // "Modified" section only appears at all if there's at least one
  // pending change -- otherwise the view is just the normal flat list.
  if (modified.length > 0) {
    const modifiedWrapper = document.createElement('div');
    modifiedWrapper.className = 'modified-section';

    const topHeader = document.createElement('div');
    topHeader.className = 'group-header top-level';
    topHeader.textContent = 'Modified';
    modifiedWrapper.appendChild(topHeader);

    // Group modified students by the status they were changed to,
    // in the same order the backend lists assignable statuses.
    const byStatus = {};
    modified.forEach(s => {
      const newStatus = pendingChanges[s.name];
      (byStatus[newStatus] = byStatus[newStatus] || []).push(s);
    });

    sortedStatusesForConfirm(new Set(Object.keys(byStatus)))
      .forEach(status => {
        const group = byStatus[status];
        const subHeader = document.createElement('div');
        subHeader.className = 'group-header';
        subHeader.innerHTML = `${escapeHtml(status)} <span class="group-count">(${group.length})</span>`;
        modifiedWrapper.appendChild(subHeader);
        group.forEach(s => modifiedWrapper.appendChild(buildStudentCard(s)));
      });

    studentList.appendChild(modifiedWrapper);
  }

  unmodified.forEach(s => studentList.appendChild(buildStudentCard(s)));
}

studentSearch.addEventListener('input', () => renderStudentList(studentSearch.value));

function openStatusPicker(student) {
  activeStudent = student;
  statusPickerName.textContent = student.name;
  statusPickerButtons.innerHTML = '';
  sortedStatusesForConfirm(new Set(assignableStatuses)).forEach(status => {
    const btn = document.createElement('button');
    btn.textContent = status;
    btn.style.background = colorForStatus(status);
    btn.addEventListener('click', () => {
      pendingChanges[activeStudent.name] = status;
      closeStatusPicker();
      renderStudentList(studentSearch.value);
      updateActionButtons();
    });
    statusPickerButtons.appendChild(btn);
  });
  statusPickerModal.classList.add('active');
}

function closeStatusPicker() {
  statusPickerModal.classList.remove('active');
  activeStudent = null;
}
document.getElementById('statusPickerCancelBtn').addEventListener('click', closeStatusPicker);

function updateActionButtons() {
  const count = Object.keys(pendingChanges).length;
  pendingCountEl.textContent = count;
  statusSubmitBtn.disabled = count === 0;
  statusClearBtn.disabled = count === 0;
}

// ---- Clear (with confirmation) ----
statusClearBtn.addEventListener('click', () => {
  if (Object.keys(pendingChanges).length === 0) return;
  clearConfirmModal.classList.add('active');
});
document.getElementById('clearCancelBtn').addEventListener('click', () => {
  clearConfirmModal.classList.remove('active');
});
document.getElementById('clearConfirmBtn').addEventListener('click', () => {
  pendingChanges = {};
  clearConfirmModal.classList.remove('active');
  renderStudentList(studentSearch.value);
  updateActionButtons();
});

// ---- Submit (opens a confirmation modal first) ----
function sortedStatusesForConfirm(statusesPresent) {
  // Anything in CONFIRM_STATUS_ORDER comes first, in that order; any other
  // status that might exist (future-proofing) is appended after.
  const known = CONFIRM_STATUS_ORDER.filter(s => statusesPresent.has(s));
  const extra = [...statusesPresent].filter(s => !CONFIRM_STATUS_ORDER.includes(s));
  return [...known, ...extra];
}

statusSubmitBtn.addEventListener('click', () => {
  const entries = Object.entries(pendingChanges); // [name, status][]
  if (entries.length === 0) return;

  submitConfirmTitle.textContent = `Confirm ${entries.length} change${entries.length === 1 ? '' : 's'}`;
  submitConfirmList.innerHTML = '';

  const byStatus = {};
  entries.forEach(([name, status]) => {
    (byStatus[status] = byStatus[status] || []).push(name);
  });
  const orderedStatuses = sortedStatusesForConfirm(new Set(Object.keys(byStatus)));

  if (entries.length <= SUMMARY_THRESHOLD) {
    // 5 or fewer: one card per student, e.g. "Klaus | Incomplete" --
    // same look as the cards in the main list, just not clickable.
    orderedStatuses.forEach(status => {
      byStatus[status].forEach(name => {
        const card = document.createElement('div');
        card.className = 'status-card';
        card.style.background = colorForStatus(status);
        card.innerHTML = `
          <span class="status-name">${escapeHtml(name)}</span>
          <span class="status-value">${escapeHtml(status)}</span>
        `;
        submitConfirmList.appendChild(card);
      });
    });
  } else {
    // 6+: grouped counts, e.g. "2 students | Incomplete" -- same card
    // styling, just with a count instead of a single name.
    orderedStatuses.forEach(status => {
      const count = byStatus[status].length;
      const card = document.createElement('div');
      card.className = 'status-card';
      card.style.background = colorForStatus(status);
      card.innerHTML = `
        <span class="status-name">${count} student${count === 1 ? '' : 's'}</span>
        <span class="status-value">${escapeHtml(status)}</span>
      `;
      submitConfirmList.appendChild(card);
    });
  }

  submitConfirmModal.classList.add('active');
});

document.getElementById('submitConfirmCancelBtn').addEventListener('click', () => {
  submitConfirmModal.classList.remove('active');
});

document.getElementById('submitConfirmOkBtn').addEventListener('click', async () => {
  const updates = Object.entries(pendingChanges).map(([name, status]) => ({ name, status }));
  if (updates.length === 0) {
    submitConfirmModal.classList.remove('active');
    return;
  }

  const currentClass = getCurrentClass();
  const currentAssignment = getCurrentAssignment();

  const okBtn = document.getElementById('submitConfirmOkBtn');
  okBtn.disabled = true;
  okBtn.textContent = 'Submitting…';
  try {
    const res = await fetch(`/classes/${encodeURIComponent(currentClass)}/assignments/${encodeURIComponent(currentAssignment)}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ updates }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert(data.detail || 'Failed to submit changes.');
      return;
    }
    submitConfirmModal.classList.remove('active');
    await loadStudentStatus(currentClass, currentAssignment);
  } catch (err) {
    alert('Network error while submitting changes.');
  } finally {
    okBtn.disabled = false;
    okBtn.textContent = 'Confirm';
  }
});