import { getCurrentClass, getCurrentAssignment } from './state.js';

export const views = {
  home: document.getElementById('view-home'),
  assignments: document.getElementById('view-assignments'),
  chat: document.getElementById('view-chat'),
  status: document.getElementById('view-status'),
};

const pageTitle = document.getElementById('pageTitle');
const backBtn = document.getElementById('backBtn');

export function showView(name) {
  Object.values(views).forEach(v => v.classList.remove('active'));
  views[name].classList.add('active');
  backBtn.classList.toggle('visible', name !== 'home');
  if (name === 'home') pageTitle.textContent = '📊 Homework Tracker';
  if (name === 'assignments') pageTitle.textContent = getCurrentClass();
  if (name === 'chat') pageTitle.textContent = getCurrentAssignment();
  if (name === 'status') pageTitle.textContent = getCurrentAssignment();
}

export function isViewActive(name) {
  return views[name].classList.contains('active');
}

export { backBtn };