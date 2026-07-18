import { views, backBtn, isViewActive, showView } from './views.js';
import { loadClasses } from './classes.js';
import { loadAssignments } from './assignments.js';
import { getCurrentClass, setCurrentClass } from './state.js';
// status.js and chat.js self-register their own event listeners on import
// (they attach handlers to elements that already exist in the page), so
// importing them for their side effects is enough -- no functions from
// them are needed directly in this file.
import './status.js';
import './chat.js';

backBtn.addEventListener('click', () => {
  if (isViewActive('chat') || isViewActive('status')) {
    showView('assignments');
    loadAssignments(getCurrentClass());
  } else if (isViewActive('assignments')) {
    setCurrentClass(null);
    showView('home');
    loadClasses();
  }
});

// ---- Init ----
loadClasses();