import { escapeHtml } from './utils.js';
import { showView } from './views.js';
import { getCurrentClass, setCurrentAssignment, newlyCreatedAssignments } from './state.js';
import { loadStudentStatus } from './status.js';

export async function loadAssignments(className) {
  const container = document.getElementById('assignmentList');
  container.innerHTML = '<div class="empty-state">Loading…</div>';
  try {
    const res = await fetch(`/classes/${encodeURIComponent(className)}/assignments`);
    const data = await res.json();
    if (!data.assignments || data.assignments.length === 0) {
      container.innerHTML = '<div class="empty-state">No assignments yet. Create one above.</div>';
      return;
    }

    // Backend already sorts by date_created descending. Additionally
    // pin anything created this session to the very top, so a brand
    // new assignment is guaranteed to be first regardless of same-day
    // ties with other assignments.
    const isNew = a => newlyCreatedAssignments.has(`${className}::${a.name}`);
    const sorted = [
      ...data.assignments.filter(isNew),
      ...data.assignments.filter(a => !isNew(a)),
    ];

    container.innerHTML = '';
    sorted.forEach(a => {
      const el = document.createElement('div');
      el.className = 'list-item';
      const dateText = a.due_date ? `Due: ${escapeHtml(a.due_date)}` : '';
      const badge = isNew(a) ? '<span class="new-badge">NEW</span>' : '';
      el.innerHTML = `
        <div class="item-main">
          <span>${escapeHtml(a.name)}</span>
          ${dateText ? `<span class="item-subtext">${dateText}</span>` : ''}
        </div>
        <div class="item-right">${badge}<span class="chevron">›</span></div>
      `;
      el.addEventListener('click', () => {
        newlyCreatedAssignments.delete(`${className}::${a.name}`);
        setCurrentAssignment(a.name);
        showView('status');
        loadStudentStatus(className, a.name);
      });
      container.appendChild(el);
    });
  } catch (err) {
    container.innerHTML = '<div class="empty-state">Failed to load assignments.</div>';
  }
}

// ---- Create assignment modal ----
const createAssignmentModal = document.getElementById('createAssignmentModal');
const assignmentNameInput = document.getElementById('assignmentNameInput');
const assignmentDueDateInput = document.getElementById('assignmentDueDateInput');
const confirmAssignmentBtn = document.getElementById('confirmAssignmentBtn');
const createAssignmentError = document.getElementById('createAssignmentError');

const MONTH_ABBR = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

function todayIso() {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

// Convert an <input type="date"> value ("YYYY-MM-DD") to the backend's
// expected "D Mon YYYY" format (e.g. "2 Jun 2026").
function isoToDisplayDate(isoStr) {
  const [yyyy, mm, dd] = isoStr.split('-').map(Number);
  return `${dd} ${MONTH_ABBR[mm - 1]} ${yyyy}`;
}

document.getElementById('createAssignmentRow').addEventListener('click', () => {
  assignmentNameInput.value = '';
  assignmentDueDateInput.value = todayIso();
  createAssignmentError.textContent = '';
  createAssignmentModal.classList.add('active');
  assignmentNameInput.focus();
});
document.getElementById('cancelAssignmentBtn').addEventListener('click', () => {
  createAssignmentModal.classList.remove('active');
});
confirmAssignmentBtn.addEventListener('click', async () => {
  const name = assignmentNameInput.value.trim();
  const dueDateIso = assignmentDueDateInput.value;
  const currentClass = getCurrentClass();

  if (!name) {
    createAssignmentError.textContent = 'Please enter an assignment name.';
    return;
  }
  if (!dueDateIso) {
    createAssignmentError.textContent = 'Please select a due date.';
    return;
  }

  const due_date = isoToDisplayDate(dueDateIso);

  confirmAssignmentBtn.disabled = true;
  createAssignmentError.textContent = '';
  try {
    const res = await fetch(`/classes/${encodeURIComponent(currentClass)}/assignments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, due_date }),
    });
    const data = await res.json();
    if (!res.ok) {
      createAssignmentError.textContent = data.detail || 'Failed to create assignment.';
      confirmAssignmentBtn.disabled = false;
      return;
    }
    createAssignmentModal.classList.remove('active');
    confirmAssignmentBtn.disabled = false;
    newlyCreatedAssignments.add(`${currentClass}::${name}`);
    loadAssignments(currentClass);
  } catch (err) {
    createAssignmentError.textContent = 'Something went wrong.';
    confirmAssignmentBtn.disabled = false;
  }
});