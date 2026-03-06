/**
 * IamBusy — Schedule Management UI.
 *
 * Features:
 *  • Weekly grid view with activities
 *  • Drag-and-drop to move activities between time slots
 *  • Add / Edit / Delete activities via REST API
 *  • Conflict detection with resolution dialog
 */

(function () {
    'use strict';

    const DAYS = ['Luni', 'Marti', 'Miercuri', 'Joi', 'Vineri', 'Sambata', 'Duminica'];
    const HOUR_START = 7;
    const HOUR_END = 22;

    // ── State ───────────────────────────────────────────────────
    let activities = [];
    let currentWeekFilter = 'odd';
    let pendingSave = null;       // stashed form data when conflict arises
    let pendingConflicts = [];    // list of conflicting activities

    // ── Drag-and-drop state ─────────────────────────────────────
    let dragState = null;         // { act, ghost, startX, startY, offsetX, offsetY, hasMoved }

    // ── DOM refs ────────────────────────────────────────────────
    const grid = document.getElementById('manage-grid');
    const weekTabs = document.querySelectorAll('.week-tab');
    const btnAdd = document.getElementById('btn-add');

    // Activity modal
    const modalOverlay = document.getElementById('modal-overlay');
    const modalTitle = document.getElementById('modal-title');
    const form = document.getElementById('activity-form');
    const formId = document.getElementById('form-id');
    const formTitleInput = document.getElementById('form-title');
    const formDay = document.getElementById('form-day');
    const formStart = document.getElementById('form-start');
    const formEnd = document.getElementById('form-end');
    const formWeekType = document.getElementById('form-week-type');
    const btnCancel = document.getElementById('btn-cancel');
    const modalClose = document.getElementById('modal-close');

    // Conflict modal
    const conflictOverlay = document.getElementById('conflict-overlay');
    const conflictList = document.getElementById('conflict-list');
    const conflictClose = document.getElementById('conflict-close');
    const conflictDelete = document.getElementById('conflict-delete');
    const conflictChoose = document.getElementById('conflict-choose-another');

    // Delete modal
    const deleteOverlay = document.getElementById('delete-overlay');
    const deleteMessage = document.getElementById('delete-message');
    const deleteCancel = document.getElementById('delete-cancel');
    const deleteConfirm = document.getElementById('delete-confirm');
    let pendingDeleteId = null;

    // ── API helpers ─────────────────────────────────────────────
    async function fetchActivities() {
        const res = await fetch('/api/activities');
        activities = await res.json();
        renderGrid();
    }

    async function saveActivity(data) {
        const isEdit = !!data.id;
        const url = isEdit ? `/api/activities/${data.id}` : '/api/activities';
        const method = isEdit ? 'PUT' : 'POST';

        const res = await fetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });

        const body = await res.json();

        if (res.status === 409 && body.needs_resolution) {
            // Conflict! Show conflict dialog
            pendingSave = data;
            pendingConflicts = body.conflicts;
            showConflictModal(body.conflicts);
            return false;
        }

        if (!res.ok) {
            showToast(body.error || 'Something went wrong', 'error');
            return false;
        }

        return true;
    }

    async function removeActivity(id) {
        const res = await fetch(`/api/activities/${id}`, { method: 'DELETE' });
        return res.ok;
    }

    // ── Grid rendering ──────────────────────────────────────────
    function renderGrid() {
        grid.innerHTML = '';

        // Filter activities for current week tab
        const filtered = activities.filter(a =>
            a.week_type === currentWeekFilter || a.week_type === 'both'
        );

        // Time column header (empty corner cell)
        const corner = el('div', 'grid-corner', '');
        grid.appendChild(corner);

        // Day headers
        DAYS.forEach(day => {
            const header = el('div', 'grid-day-header', day.substring(0, 3));
            header.title = day;
            grid.appendChild(header);
        });

        // Time rows
        for (let hour = HOUR_START; hour < HOUR_END; hour++) {
            // Time label
            const timeLabel = el('div', 'grid-time-label',
                `${String(hour).padStart(2, '0')}:00`);
            grid.appendChild(timeLabel);

            // Cells for each day
            DAYS.forEach(day => {
                const cell = el('div', 'grid-cell', '');
                cell.dataset.day = day;
                cell.dataset.hour = hour;

                // Click empty cell to add activity at that slot
                cell.addEventListener('click', (e) => {
                    if (dragState) return; // don't open modal after drag
                    // Only trigger if the click is directly on the cell, not on a card inside it
                    if (e.target !== cell) return;
                    const startStr = `${String(hour).padStart(2, '0')}:00`;
                    const endHour = Math.min(hour + 2, HOUR_END);
                    const endStr = `${String(endHour).padStart(2, '0')}:00`;
                    openAddModal(day, startStr, endStr);
                });

                grid.appendChild(cell);
            });
        }

        // Place activity cards on the grid
        filtered.forEach(act => {
            placeActivity(act);
        });
    }

    function placeActivity(act) {
        const startParts = act.start_time.split(':');
        const endParts = act.end_time.split(':');
        const startHour = parseInt(startParts[0]);
        const startMin = parseInt(startParts[1]);
        const endHour = parseInt(endParts[0]);
        const endMin = parseInt(endParts[1]);

        if (startHour < HOUR_START || startHour >= HOUR_END) return;

        const dayIndex = DAYS.indexOf(act.day);
        if (dayIndex === -1) return;

        // Find the starting cell
        const cells = grid.querySelectorAll('.grid-cell');
        const rowIndex = startHour - HOUR_START;
        const cellIndex = rowIndex * DAYS.length + dayIndex;
        const targetCell = cells[cellIndex];
        if (!targetCell) return;

        // Calculate position and height
        const durationMinutes = (endHour * 60 + endMin) - (startHour * 60 + startMin);
        const heightSlots = durationMinutes / 60;
        const topOffset = (startMin / 60) * 100;

        // Parse title for display
        const parts = act.title.split(' | ');
        const subject = parts[0] || act.title;
        const room = parts[1] || '';

        const card = document.createElement('div');
        card.className = 'activity-card';
        card.style.top = `${topOffset}%`;
        card.style.height = `calc(${heightSlots * 100}% - 2px)`;
        card.dataset.id = act.id;

        // Week-type indicator
        if (act.week_type !== 'both') {
            card.classList.add(`week-${act.week_type}`);
        }

        card.innerHTML = `
            <div class="card-subject">${escapeHtml(subject)}</div>
            ${room ? `<div class="card-room">${escapeHtml(room)}</div>` : ''}
            <div class="card-time">${act.start_time} – ${act.end_time}</div>
            <button class="card-delete" aria-label="Delete activity" data-id="${act.id}">&times;</button>
        `;

        // ── Drag-and-drop via pointer events ────────────────
        card.style.touchAction = 'none'; // prevent scroll during drag
        card.addEventListener('pointerdown', (e) => {
            if (e.target.classList.contains('card-delete')) return;
            e.preventDefault();
            const rect = card.getBoundingClientRect();
            dragState = {
                act,
                card,
                ghost: null,
                startX: e.clientX,
                startY: e.clientY,
                offsetX: e.clientX - rect.left,
                offsetY: e.clientY - rect.top,
                hasMoved: false,
                cardWidth: rect.width,
                cardHeight: rect.height,
            };
            card.setPointerCapture(e.pointerId);
        });

        card.addEventListener('pointermove', (e) => {
            if (!dragState || dragState.card !== card) return;
            const dx = e.clientX - dragState.startX;
            const dy = e.clientY - dragState.startY;

            // Require 6px of movement to start dragging (avoids accidental drags)
            if (!dragState.hasMoved && Math.abs(dx) < 6 && Math.abs(dy) < 6) return;

            if (!dragState.hasMoved) {
                dragState.hasMoved = true;
                // Create ghost
                const ghost = card.cloneNode(true);
                ghost.className = 'activity-card drag-ghost';
                ghost.style.width = dragState.cardWidth + 'px';
                ghost.style.height = dragState.cardHeight + 'px';
                document.body.appendChild(ghost);
                dragState.ghost = ghost;
                card.classList.add('dragging');
            }

            // Position ghost at pointer
            dragState.ghost.style.left = (e.clientX - dragState.offsetX) + 'px';
            dragState.ghost.style.top = (e.clientY - dragState.offsetY) + 'px';

            // Highlight the target cell
            highlightDropTarget(e.clientX, e.clientY);
        });

        card.addEventListener('pointerup', async (e) => {
            if (!dragState || dragState.card !== card) return;
            const wasDrag = dragState.hasMoved;

            if (wasDrag) {
                // Remove ghost and highlights
                if (dragState.ghost) dragState.ghost.remove();
                card.classList.remove('dragging');
                clearDropHighlights();

                // Find drop target cell
                const targetInfo = getDropTarget(e.clientX, e.clientY);
                if (targetInfo) {
                    await handleDrop(dragState.act, targetInfo.day, targetInfo.hour);
                }
            }

            const hadMoved = dragState.hasMoved;
            dragState = null;

            // If it wasn't a drag, treat as click-to-edit
            if (!hadMoved) {
                openEditModal(act);
            }
        });

        card.addEventListener('pointercancel', () => {
            if (dragState && dragState.card === card) {
                if (dragState.ghost) dragState.ghost.remove();
                card.classList.remove('dragging');
                clearDropHighlights();
                dragState = null;
            }
        });

        // Delete button
        card.querySelector('.card-delete').addEventListener('click', (e) => {
            e.stopPropagation();
            openDeleteModal(act);
        });

        targetCell.appendChild(card);
    }

    // ── Drag-and-drop helpers ────────────────────────────────────
    function highlightDropTarget(clientX, clientY) {
        clearDropHighlights();
        const cell = getCellAt(clientX, clientY);
        if (cell) cell.classList.add('drop-target');
    }

    function clearDropHighlights() {
        grid.querySelectorAll('.drop-target').forEach(c => c.classList.remove('drop-target'));
    }

    function getCellAt(clientX, clientY) {
        // Temporarily hide ghost so elementFromPoint hits the cell
        if (dragState && dragState.ghost) dragState.ghost.style.pointerEvents = 'none';
        const el = document.elementFromPoint(clientX, clientY);
        if (dragState && dragState.ghost) dragState.ghost.style.pointerEvents = '';
        if (!el) return null;
        // Could be the cell itself or a child inside it
        return el.closest('.grid-cell');
    }

    function getDropTarget(clientX, clientY) {
        const cell = getCellAt(clientX, clientY);
        if (!cell) return null;
        return {
            day: cell.dataset.day,
            hour: parseInt(cell.dataset.hour),
        };
    }

    async function handleDrop(act, newDay, newHour) {
        // Calculate duration to preserve it
        const startParts = act.start_time.split(':');
        const endParts = act.end_time.split(':');
        const durationMin = (parseInt(endParts[0]) * 60 + parseInt(endParts[1]))
            - (parseInt(startParts[0]) * 60 + parseInt(startParts[1]));

        const newStart = `${String(newHour).padStart(2, '0')}:00`;
        const endTotal = newHour * 60 + durationMin;
        const newEnd = `${String(Math.floor(endTotal / 60)).padStart(2, '0')}:${String(endTotal % 60).padStart(2, '0')}`;

        // Skip if nothing changed
        if (act.day === newDay && act.start_time === newStart) return;

        // Validate end time
        if (endTotal > HOUR_END * 60) {
            showToast('Activity would end past the grid. Choose an earlier slot.', 'error');
            return;
        }

        const data = {
            id: act.id,
            day: newDay,
            start_time: newStart,
            end_time: newEnd,
        };

        const ok = await saveActivity(data);
        if (ok) {
            showToast('Activity moved!');
            await fetchActivities();
        }
    }

    // ── Modal helpers ───────────────────────────────────────────
    function openAddModal(day, start, end) {
        modalTitle.textContent = 'Add Activity';
        formId.value = '';
        formTitleInput.value = '';
        formDay.value = day || 'Luni';
        formStart.value = start || '08:00';
        formEnd.value = end || '10:00';
        formWeekType.value = currentWeekFilter;
        showModal(modalOverlay);
        formTitleInput.focus();
    }

    function openEditModal(act) {
        modalTitle.textContent = 'Edit Activity';
        formId.value = act.id;
        formTitleInput.value = act.title;
        formDay.value = act.day;
        formStart.value = act.start_time;
        formEnd.value = act.end_time;
        formWeekType.value = act.week_type;
        showModal(modalOverlay);
        formTitleInput.focus();
    }

    function openDeleteModal(act) {
        pendingDeleteId = act.id;
        const parts = act.title.split(' | ');
        deleteMessage.textContent = `Are you sure you want to delete "${parts[0]}"?`;
        showModal(deleteOverlay);
    }

    function showConflictModal(conflicts) {
        conflictList.innerHTML = '';
        conflicts.forEach(c => {
            const parts = c.title.split(' | ');
            const item = el('div', 'conflict-item', '');
            item.innerHTML = `
                <span class="conflict-name">${escapeHtml(parts[0])}</span>
                <span class="conflict-detail">${c.day} · ${c.start_time} – ${c.end_time} · ${c.week_type}</span>
            `;
            conflictList.appendChild(item);
        });
        showModal(conflictOverlay);
    }

    function showModal(overlay) {
        overlay.setAttribute('aria-hidden', 'false');
        overlay.classList.add('visible');
        // Prevent body scroll
        document.body.style.overflow = 'hidden';
    }

    function hideModal(overlay) {
        overlay.setAttribute('aria-hidden', 'true');
        overlay.classList.remove('visible');
        document.body.style.overflow = '';
    }

    function hideAllModals() {
        hideModal(modalOverlay);
        hideModal(conflictOverlay);
        hideModal(deleteOverlay);
    }

    // ── Toast notifications ─────────────────────────────────────
    function showToast(message, type = 'success') {
        const existing = document.querySelector('.toast');
        if (existing) existing.remove();

        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        document.body.appendChild(toast);

        requestAnimationFrame(() => toast.classList.add('visible'));
        setTimeout(() => {
            toast.classList.remove('visible');
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }

    // ── Event handlers ──────────────────────────────────────────

    // Week tabs
    weekTabs.forEach(tab => {
        tab.addEventListener('click', () => {
            weekTabs.forEach(t => {
                t.classList.remove('active');
                t.setAttribute('aria-selected', 'false');
            });
            tab.classList.add('active');
            tab.setAttribute('aria-selected', 'true');
            currentWeekFilter = tab.dataset.week;
            renderGrid();
        });
    });

    // Add button
    btnAdd.addEventListener('click', () => openAddModal());

    // Form submit
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const data = {
            title: formTitleInput.value.trim(),
            day: formDay.value,
            start_time: formStart.value,
            end_time: formEnd.value,
            week_type: formWeekType.value,
        };
        if (formId.value) data.id = parseInt(formId.value);

        const ok = await saveActivity(data);
        if (ok) {
            hideModal(modalOverlay);
            showToast(data.id ? 'Activity updated!' : 'Activity added!');
            await fetchActivities();
        }
    });

    // Cancel / close
    btnCancel.addEventListener('click', () => hideModal(modalOverlay));
    modalClose.addEventListener('click', () => hideModal(modalOverlay));

    // Conflict resolution
    conflictClose.addEventListener('click', () => {
        hideModal(conflictOverlay);
        pendingSave = null;
        pendingConflicts = [];
    });

    conflictChoose.addEventListener('click', () => {
        // Go back to the form — keep it open
        hideModal(conflictOverlay);
        showModal(modalOverlay);
        formTitleInput.focus();
    });

    conflictDelete.addEventListener('click', async () => {
        // Delete all conflicting activities, then retry save with force
        for (const c of pendingConflicts) {
            await removeActivity(c.id);
        }
        if (pendingSave) {
            pendingSave.force = true;
            await saveActivity(pendingSave);
        }
        hideAllModals();
        showToast('Conflicts resolved and activity saved!');
        pendingSave = null;
        pendingConflicts = [];
        await fetchActivities();
    });

    // Delete confirmation
    deleteCancel.addEventListener('click', () => {
        hideModal(deleteOverlay);
        pendingDeleteId = null;
    });

    deleteConfirm.addEventListener('click', async () => {
        if (pendingDeleteId !== null) {
            await removeActivity(pendingDeleteId);
            hideModal(deleteOverlay);
            showToast('Activity deleted.');
            pendingDeleteId = null;
            await fetchActivities();
        }
    });

    // Close modals on overlay click
    [modalOverlay, conflictOverlay, deleteOverlay].forEach(overlay => {
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) hideModal(overlay);
        });
    });

    // Close on Escape
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') hideAllModals();
    });

    // ── Utility ─────────────────────────────────────────────────
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

    // ── Auto-set end time when start time changes (+2h default) ─
    formStart.addEventListener('change', () => {
        const parts = formStart.value.split(':');
        if (parts.length === 2) {
            const h = parseInt(parts[0]);
            const m = parseInt(parts[1]);
            const endH = Math.min(h + 2, 23);
            formEnd.value = `${String(endH).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
        }
    });

    // ── Init ────────────────────────────────────────────────────
    fetchActivities();

})();
