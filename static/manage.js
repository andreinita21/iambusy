/**
 * IamBusy — Schedule management UI.
 *
 *  • Weekly grid (07:00–22:00) for odd / even weeks
 *  • Click an empty cell to add, click a card to edit, drag to move
 *  • Conflict detection with a resolution dialog
 *  • Pre-fill from the URL: /manage?day=Marti&start=12:00&end=14:00&week=odd
 */

(function () {
    'use strict';

    const DAYS = ['Luni', 'Marti', 'Miercuri', 'Joi', 'Vineri', 'Sambata', 'Duminica'];
    const DAY_LABELS = { Luni: 'Luni', Marti: 'Marți', Miercuri: 'Miercuri', Joi: 'Joi', Vineri: 'Vineri', Sambata: 'Sâmbătă', Duminica: 'Duminică' };
    const WEEK_LABELS = { both: 'ambele săptămâni', odd: 'săpt. impară', even: 'săpt. pară' };
    const HOUR_START = 7;
    const HOUR_END = 22;

    const pad = (n) => String(n).padStart(2, '0');

    // ── State ───────────────────────────────────────────────────
    let activities = [];
    let currentWeekFilter = document.getElementById('week-tabs').dataset.current || 'odd';
    let pendingSave = null;
    let pendingConflicts = [];
    let pendingDeleteId = null;
    let dragState = null;

    // ── DOM ─────────────────────────────────────────────────────
    const grid = document.getElementById('wgrid');
    const gridWrap = document.getElementById('grid-wrap');
    const weekTabs = document.querySelectorAll('.seg-btn');
    const btnAdd = document.getElementById('btn-add');

    const modalOverlay = document.getElementById('modal-overlay');
    const modalTitle = document.getElementById('modal-title');
    const form = document.getElementById('activity-form');
    const formId = document.getElementById('form-id');
    const formTitleInput = document.getElementById('form-title');
    const formDay = document.getElementById('form-day');
    const formStart = document.getElementById('form-start');
    const formEnd = document.getElementById('form-end');
    const btnCancel = document.getElementById('btn-cancel');
    const modalClose = document.getElementById('modal-close');

    const conflictOverlay = document.getElementById('conflict-overlay');
    const conflictList = document.getElementById('conflict-list');
    const conflictClose = document.getElementById('conflict-close');
    const conflictDelete = document.getElementById('conflict-delete');
    const conflictChoose = document.getElementById('conflict-choose-another');

    const deleteOverlay = document.getElementById('delete-overlay');
    const deleteMessage = document.getElementById('delete-message');
    const deleteCancel = document.getElementById('delete-cancel');
    const deleteClose = document.getElementById('delete-close');
    const deleteConfirm = document.getElementById('delete-confirm');

    const getWeekType = () => (form.querySelector('input[name="week_type"]:checked') || {}).value || 'both';
    const setWeekType = (v) => {
        const r = form.querySelector(`input[name="week_type"][value="${v}"]`);
        if (r) r.checked = true;
    };

    // ── Theme toggle ────────────────────────────────────────────
    const themeToggle = document.getElementById('theme-toggle');
    if (themeToggle) {
        themeToggle.addEventListener('click', () => {
            const root = document.documentElement;
            const isLight = root.getAttribute('data-theme') === 'light';
            if (isLight) root.removeAttribute('data-theme');
            else root.setAttribute('data-theme', 'light');
            try { localStorage.setItem('iambusy-theme', isLight ? 'dark' : 'light'); } catch (_) { }
        });
    }

    // ── API ─────────────────────────────────────────────────────
    async function fetchActivities() {
        const res = await fetch('/api/activities');
        activities = await res.json();
        renderGrid();
    }

    async function saveActivity(data) {
        const isEdit = !!data.id;
        const url = isEdit ? `/api/activities/${data.id}` : '/api/activities';
        const res = await fetch(url, {
            method: isEdit ? 'PUT' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        const body = await res.json();

        if (res.status === 409 && body.needs_resolution) {
            pendingSave = data;
            pendingConflicts = body.conflicts;
            hideModal(modalOverlay);
            showConflictModal(body.conflicts);
            return false;
        }
        if (!res.ok) {
            showToast(body.error || 'Ceva nu a mers.', 'error');
            return false;
        }
        return true;
    }

    async function removeActivity(id) {
        const res = await fetch(`/api/activities/${id}`, { method: 'DELETE' });
        return res.ok;
    }

    // ── Grid ────────────────────────────────────────────────────
    function renderGrid() {
        grid.innerHTML = '';
        const filtered = activities.filter(a => a.week_type === currentWeekFilter || a.week_type === 'both');

        grid.appendChild(el('div', 'g-corner', ''));
        DAYS.forEach((day, i) => {
            const h = el('div', 'g-day' + (i >= 5 ? ' weekend' : ''), '');
            const n = filtered.filter(a => a.day === day).length;
            h.innerHTML = `<span>${DAY_LABELS[day].substring(0, 3)}</span><small>${n ? n + ' act.' : 'liber'}</small>`;
            h.title = DAY_LABELS[day];
            grid.appendChild(h);
        });

        for (let hour = HOUR_START; hour < HOUR_END; hour++) {
            grid.appendChild(el('div', 'g-time', `${pad(hour)}:00`));
            DAYS.forEach((day, i) => {
                const cell = el('div', 'g-cell' + (i >= 5 ? ' weekend' : ''), '');
                cell.dataset.day = day;
                cell.dataset.hour = hour;
                cell.title = `${DAY_LABELS[day]} ${pad(hour)}:00 — click pentru a adăuga`;
                cell.addEventListener('click', (e) => {
                    if (dragState || e.target !== cell) return;
                    openAddModal(day, `${pad(hour)}:00`, `${pad(Math.min(hour + 2, HOUR_END))}:00`);
                });
                grid.appendChild(cell);
            });
        }

        filtered.forEach(placeActivity);
    }

    function placeActivity(act) {
        const [sh, sm] = act.start_time.split(':').map(Number);
        const [eh, em] = act.end_time.split(':').map(Number);
        if (sh < HOUR_START || sh >= HOUR_END) return;
        const dayIndex = DAYS.indexOf(act.day);
        if (dayIndex === -1) return;

        const cells = grid.querySelectorAll('.g-cell');
        const targetCell = cells[(sh - HOUR_START) * DAYS.length + dayIndex];
        if (!targetCell) return;

        const durationMinutes = (eh * 60 + em) - (sh * 60 + sm);
        const parts = act.title.split(' | ');
        const subject = parts[0] || act.title;
        const room = parts[1] || '';

        const card = document.createElement('div');
        card.className = 'act';
        card.style.top = `${(sm / 60) * 100}%`;
        card.style.height = `calc(${(durationMinutes / 60) * 100}% - 4px)`;
        card.dataset.id = act.id;
        card.tabIndex = 0;
        card.setAttribute('role', 'button');
        card.setAttribute('aria-label', `${subject}, ${act.start_time}–${act.end_time}, ${WEEK_LABELS[act.week_type]}`);

        const kindMatch = subject.match(/\(([CSL])\)\s*$/);
        if (kindMatch) card.classList.add(`kind-${kindMatch[1]}`);
        if (act.week_type !== 'both') card.classList.add('only-week');

        card.innerHTML = `
            <div class="act-subject">${escapeHtml(subject)}</div>
            ${room ? `<div class="act-room">${escapeHtml(room)}</div>` : ''}
            <div class="act-time">${act.start_time}–${act.end_time}</div>
            ${act.week_type !== 'both' ? `<span class="act-week">${act.week_type === 'odd' ? 'IMP' : 'PAR'}</span>` : ''}
            <button type="button" class="act-del" aria-label="Șterge ${escapeHtml(subject)}">&times;</button>
        `;

        // ── Drag & drop (pointer events) ────────────────────
        card.style.touchAction = 'none';
        card.addEventListener('pointerdown', (e) => {
            if (e.target.classList.contains('act-del')) return;
            e.preventDefault();
            const rect = card.getBoundingClientRect();
            dragState = {
                act, card, ghost: null,
                startX: e.clientX, startY: e.clientY,
                offsetX: e.clientX - rect.left, offsetY: e.clientY - rect.top,
                hasMoved: false, cardWidth: rect.width, cardHeight: rect.height,
            };
            card.setPointerCapture(e.pointerId);
        });

        card.addEventListener('pointermove', (e) => {
            if (!dragState || dragState.card !== card) return;
            const dx = e.clientX - dragState.startX;
            const dy = e.clientY - dragState.startY;
            if (!dragState.hasMoved && Math.abs(dx) < 6 && Math.abs(dy) < 6) return;
            if (!dragState.hasMoved) {
                dragState.hasMoved = true;
                const ghost = card.cloneNode(true);
                ghost.classList.add('drag-ghost');
                ghost.style.width = dragState.cardWidth + 'px';
                ghost.style.height = dragState.cardHeight + 'px';
                ghost.style.top = '';
                document.body.appendChild(ghost);
                dragState.ghost = ghost;
                card.classList.add('dragging');
            }
            dragState.ghost.style.left = (e.clientX - dragState.offsetX) + 'px';
            dragState.ghost.style.top = (e.clientY - dragState.offsetY) + 'px';
            highlightDropTarget(e.clientX, e.clientY);
        });

        card.addEventListener('pointerup', async (e) => {
            if (!dragState || dragState.card !== card) return;
            const wasDrag = dragState.hasMoved;
            if (wasDrag) {
                if (dragState.ghost) dragState.ghost.remove();
                card.classList.remove('dragging');
                clearDropHighlights();
                const target = getDropTarget(e.clientX, e.clientY);
                if (target) await handleDrop(dragState.act, target.day, target.hour);
            }
            dragState = null;
            if (!wasDrag) openEditModal(act);
        });

        card.addEventListener('pointercancel', () => {
            if (dragState && dragState.card === card) {
                if (dragState.ghost) dragState.ghost.remove();
                card.classList.remove('dragging');
                clearDropHighlights();
                dragState = null;
            }
        });

        card.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openEditModal(act); }
            if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); openDeleteModal(act); }
        });

        card.querySelector('.act-del').addEventListener('click', (e) => {
            e.stopPropagation();
            openDeleteModal(act);
        });

        targetCell.appendChild(card);
    }

    function highlightDropTarget(x, y) {
        clearDropHighlights();
        const cell = getCellAt(x, y);
        if (cell) cell.classList.add('drop-target');
    }

    function clearDropHighlights() {
        grid.querySelectorAll('.drop-target').forEach(c => c.classList.remove('drop-target'));
    }

    function getCellAt(x, y) {
        if (dragState && dragState.ghost) dragState.ghost.style.pointerEvents = 'none';
        const hit = document.elementFromPoint(x, y);
        if (dragState && dragState.ghost) dragState.ghost.style.pointerEvents = '';
        return hit ? hit.closest('.g-cell') : null;
    }

    function getDropTarget(x, y) {
        const cell = getCellAt(x, y);
        return cell ? { day: cell.dataset.day, hour: parseInt(cell.dataset.hour, 10) } : null;
    }

    async function handleDrop(act, newDay, newHour) {
        const [sh, sm] = act.start_time.split(':').map(Number);
        const [eh, em] = act.end_time.split(':').map(Number);
        const durationMin = (eh * 60 + em) - (sh * 60 + sm);
        const newStart = `${pad(newHour)}:00`;
        const endTotal = newHour * 60 + durationMin;
        const newEnd = `${pad(Math.floor(endTotal / 60))}:${pad(endTotal % 60)}`;

        if (act.day === newDay && act.start_time === newStart) return;
        if (endTotal > HOUR_END * 60) {
            showToast('Activitatea ar ieși din grilă. Alege un interval mai devreme.', 'error');
            return;
        }
        const ok = await saveActivity({ id: act.id, day: newDay, start_time: newStart, end_time: newEnd });
        if (ok) {
            showToast(`Mutat: ${DAY_LABELS[newDay]} ${newStart}`);
            await fetchActivities();
        }
    }

    // ── Modals ──────────────────────────────────────────────────
    function openAddModal(day, start, end, weekType) {
        modalTitle.textContent = 'Adaugă activitate';
        formId.value = '';
        formTitleInput.value = '';
        formDay.value = day || 'Luni';
        formStart.value = start || '08:00';
        formEnd.value = end || '10:00';
        setWeekType(weekType || currentWeekFilter);
        showModal(modalOverlay);
        formTitleInput.focus();
    }

    function openEditModal(act) {
        modalTitle.textContent = 'Editează activitatea';
        formId.value = act.id;
        formTitleInput.value = act.title;
        formDay.value = act.day;
        formStart.value = act.start_time;
        formEnd.value = act.end_time;
        setWeekType(act.week_type);
        showModal(modalOverlay);
        formTitleInput.focus();
    }

    function openDeleteModal(act) {
        pendingDeleteId = act.id;
        deleteMessage.textContent = `Sigur vrei să ștergi „${act.title.split(' | ')[0]}” (${DAY_LABELS[act.day]}, ${act.start_time}–${act.end_time})?`;
        showModal(deleteOverlay);
    }

    function showConflictModal(conflicts) {
        conflictList.innerHTML = '';
        conflicts.forEach(c => {
            const item = el('div', 'conflict-item', '');
            item.innerHTML = `<b>${escapeHtml(c.title.split(' | ')[0])}</b>
                <span>${DAY_LABELS[c.day] || c.day} · ${c.start_time}–${c.end_time} · ${WEEK_LABELS[c.week_type] || c.week_type}</span>`;
            conflictList.appendChild(item);
        });
        showModal(conflictOverlay);
    }

    function showModal(overlay) {
        overlay.setAttribute('aria-hidden', 'false');
        overlay.classList.add('visible');
        document.body.style.overflow = 'hidden';
    }

    function hideModal(overlay) {
        overlay.setAttribute('aria-hidden', 'true');
        overlay.classList.remove('visible');
        if (!document.querySelector('.overlay.visible')) document.body.style.overflow = '';
    }

    function hideAllModals() {
        [modalOverlay, conflictOverlay, deleteOverlay].forEach(hideModal);
    }

    // ── Toast ───────────────────────────────────────────────────
    function showToast(message, type = 'success') {
        const existing = document.querySelector('.toast');
        if (existing) existing.remove();
        const toast = el('div', `toast toast-${type}`, message);
        toast.setAttribute('role', 'status');
        document.body.appendChild(toast);
        requestAnimationFrame(() => toast.classList.add('visible'));
        setTimeout(() => {
            toast.classList.remove('visible');
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }

    // ── Events ──────────────────────────────────────────────────
    function activateTab(week) {
        currentWeekFilter = week;
        weekTabs.forEach(t => t.setAttribute('aria-selected', String(t.dataset.week === week)));
        renderGrid();
    }

    weekTabs.forEach(tab => tab.addEventListener('click', () => activateTab(tab.dataset.week)));
    weekTabs.forEach(t => t.setAttribute('aria-selected', String(t.dataset.week === currentWeekFilter)));

    btnAdd.addEventListener('click', () => openAddModal());

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const data = {
            title: formTitleInput.value.trim(),
            day: formDay.value,
            start_time: formStart.value,
            end_time: formEnd.value,
            week_type: getWeekType(),
        };
        if (formId.value) data.id = parseInt(formId.value, 10);
        if (data.end_time <= data.start_time) {
            showToast('Ora de sfârșit trebuie să fie după cea de început.', 'error');
            return;
        }
        const ok = await saveActivity(data);
        if (ok) {
            hideModal(modalOverlay);
            showToast(data.id ? 'Activitate actualizată.' : 'Activitate adăugată.');
            await fetchActivities();
        }
    });

    btnCancel.addEventListener('click', () => hideModal(modalOverlay));
    modalClose.addEventListener('click', () => hideModal(modalOverlay));

    conflictClose.addEventListener('click', () => {
        hideModal(conflictOverlay);
        pendingSave = null;
        pendingConflicts = [];
    });

    conflictChoose.addEventListener('click', () => {
        hideModal(conflictOverlay);
        if (pendingSave && pendingSave.title !== undefined) {
            // Came from the form → reopen it with the same values.
            showModal(modalOverlay);
            formStart.focus();
        }
        pendingSave = null;
        pendingConflicts = [];
    });

    conflictDelete.addEventListener('click', async () => {
        for (const c of pendingConflicts) await removeActivity(c.id);
        if (pendingSave) {
            pendingSave.force = true;
            await saveActivity(pendingSave);
        }
        hideAllModals();
        showToast('Suprapunere rezolvată, activitate salvată.');
        pendingSave = null;
        pendingConflicts = [];
        await fetchActivities();
    });

    deleteCancel.addEventListener('click', () => { hideModal(deleteOverlay); pendingDeleteId = null; });
    deleteClose.addEventListener('click', () => { hideModal(deleteOverlay); pendingDeleteId = null; });
    deleteConfirm.addEventListener('click', async () => {
        if (pendingDeleteId === null) return;
        await removeActivity(pendingDeleteId);
        hideModal(deleteOverlay);
        showToast('Activitate ștearsă.');
        pendingDeleteId = null;
        await fetchActivities();
    });

    [modalOverlay, conflictOverlay, deleteOverlay].forEach(overlay => {
        overlay.addEventListener('click', (e) => { if (e.target === overlay) hideModal(overlay); });
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') hideAllModals();
    });

    // Auto-set end time (+2h) when start changes
    formStart.addEventListener('change', () => {
        const [h, m] = formStart.value.split(':').map(Number);
        if (!Number.isNaN(h)) formEnd.value = `${pad(Math.min(h + 2, 23))}:${pad(m || 0)}`;
    });

    // ── Utils ───────────────────────────────────────────────────
    function el(tag, className, text) {
        const e = document.createElement(tag);
        if (className) e.className = className;
        if (text) e.textContent = text;
        return e;
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // ── Init ────────────────────────────────────────────────────
    fetchActivities().then(() => {
        // Scroll the grid so 08:00 is near the top on small screens.
        const firstRow = grid.querySelector('.g-time');
        if (firstRow && gridWrap) gridWrap.scrollTop = 0;

        // Pre-fill from the daily view's "Adaugă" links.
        const q = new URLSearchParams(location.search);
        if (q.has('day') || q.has('start')) {
            const week = q.get('week');
            if (week === 'odd' || week === 'even') activateTab(week);
            const day = DAYS.includes(q.get('day')) ? q.get('day') : 'Luni';
            openAddModal(day, q.get('start') || '10:00', q.get('end') || '12:00', week === 'odd' || week === 'even' ? week : 'both');
            history.replaceState(null, '', '/manage');
        }
    });
})();
