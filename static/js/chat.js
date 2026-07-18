// Old chatbot-driven view. Currently unreachable from the UI -- the
// assignment click handler in assignments.js opens view-status instead.
// Kept here in case you want to switch back or offer both. Safe to delete
// (along with view-chat's markup in index.html) once you're confident in
// the touch UI.
import { getCurrentClass, getCurrentAssignment } from './state.js';

const chatEl = document.getElementById('chat');
const chatInput = document.getElementById('chatInput');
const sendBtn = document.getElementById('sendBtn');
// Bounded recent-conversation memory sent with each request so
// follow-ups like "Bob too" resolve correctly. Resets whenever a fresh
// assignment chat starts (see resetChat) -- session-only, same as the
// "New" badge tracking.
const MAX_HISTORY_TURNS = 12;
let chatHistory = [];
const historyBanner = document.getElementById('historyBanner');
let historyCapNoticeShown = false;

document.getElementById('dismissBannerBtn').addEventListener('click', () => {
  historyBanner.classList.remove('visible');
});

export function resetChat() {
  const currentClass = getCurrentClass();
  const currentAssignment = getCurrentAssignment();
  chatEl.innerHTML = '';
  chatHistory = [];
  historyCapNoticeShown = false;
  historyBanner.classList.remove('visible');
  addMessage(
    `You're tracking submissions for "${currentAssignment}" in ${currentClass}. ` +
    `Only mention students who were late or didn't submit -- e.g. "Alice was late, Bob didn't submit". ` +
    `Everyone else will be marked On Time automatically the first time you do this.`,
    'agent'
  );
}

function addMessage(text, sender, opts = {}) {
  const div = document.createElement('div');
  div.className = `msg ${sender}` + (opts.loading ? ' loading' : '');
  div.textContent = text;
  chatEl.appendChild(div);
  div.scrollIntoView({ behavior: 'smooth' });
  return div;
}

async function sendChat() {
  const text = chatInput.value.trim();
  if (!text) return;
  const currentClass = getCurrentClass();
  const currentAssignment = getCurrentAssignment();
  addMessage(text, 'user');
  chatInput.value = '';
  sendBtn.disabled = true;
  const loadingEl = addMessage('Thinking…', 'agent', { loading: true });
  try {
    const res = await fetch(`/classes/${encodeURIComponent(currentClass)}/assignments/${encodeURIComponent(currentAssignment)}/instruct`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, history: chatHistory }),
    });
    const data = await res.json();
    const replyText = data.result || data.detail || 'Something went wrong.';
    loadingEl.textContent = replyText;
    loadingEl.classList.remove('loading');

    if (res.ok) {
      chatHistory.push({ role: 'user', text });
      chatHistory.push({ role: 'agent', text: replyText });
      if (chatHistory.length > MAX_HISTORY_TURNS) {
        chatHistory = chatHistory.slice(-MAX_HISTORY_TURNS);
        if (!historyCapNoticeShown) {
          historyCapNoticeShown = true;
          historyBanner.classList.add('visible');
        }
      }
    }
  } catch (err) {
    loadingEl.textContent = 'Something went wrong. Please try again.';
    loadingEl.classList.remove('loading');
  } finally {
    sendBtn.disabled = false;
    chatInput.focus();
  }
}
sendBtn.addEventListener('click', sendChat);
chatInput.addEventListener('keydown', e => { if (e.key === 'Enter') sendChat(); });