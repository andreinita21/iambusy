/**
 * IamBusy — Schedule management UI.
 *
 *  • Weekly grid (07:00–22:00, widened to fit earlier / later activities)
 *    for odd / even weeks, plus a "Calendar" tab showing real weeks with
 *    one-off events, ranged weekly activities and friends' busy overlay
 *  • Click an empty cell to add, click a card to edit, drag to move
 *  • Per-activity category, repeat (weekly with optional range / once),
 *    visibility ("who sees it") and invitations
 *  • Activities friends invited you to appear as locked cards; click one to
 *    answer, pick its category in your calendar or leave it
 *  • Conflict detection with a resolution dialog
 *  • Pre-fill from the URL: /manage?day=Marti&start=12:00&end=14:00&week=odd
 *    or /manage?date=2026-11-07&start=18:00&end=20:00&invite=ana,ion
 */

(function () {
    'use strict';

    const DAYS = ['Luni', 'Marti', 'Miercuri', 'Joi', 'Vineri', 'Sambata', 'Duminica'];
    const DAY_LABELS = { Luni: 'Luni', Marti: 'Marți', Miercuri: 'Miercuri', Joi: 'Joi', Vineri: 'Vineri', Sambata: 'Sâmbătă', Duminica: 'Duminică' };
    const WEEK_LABELS = { both: 'ambele săptămâni', odd: 'săpt. impară', even: 'săpt. pară' };
    const MONTHS = ['ian', 'feb', 'mar', 'apr', 'mai', 'iun', 'iul', 'aug', 'sep', 'oct', 'nov', 'dec'];
    const LEVEL_LABELS = { full: 'Complet', busy: 'Doar ocupat', hidden: 'Ascuns' };
    const DEFAULT_HOUR_START = 7;
    const DEFAULT_HOUR_END = 22;

    const pad = (n) => String(n).padStart(2, '0');

    // ── Dates (UTC day numbers: no DST surprises) ───────────────
    const dayNum = (s) => { const [y, m, d] = s.split('-').map(Number); return Date.UTC(y, m - 1, d) / 86400000; };
    const fromNum = (n) => new Date(n * 86400000);
    const isoOf = (n) => { const d = fromNum(n); return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`; };
    const weekdayOf = (iso) => (fromNum(dayNum(iso)).getUTCDay() + 6) % 7;
    const mondayOf = (iso) => isoOf(dayNum(iso) - weekdayOf(iso));
    const addDays = (iso, n) => isoOf(dayNum(iso) + n);
    const shortDate = (iso) => { const d = fromNum(dayNum(iso)); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`; };

    function weekIndex(week1, iso) {
        return Math.floor((dayNum(mondayOf(iso)) - dayNum(mondayOf(week1))) / 7);
    }
    function isOddWeek(week1, iso) { return ((weekIndex(week1, iso) % 2) + 2) % 2 === 0; }

    /** Same rules as schedule_engine.occurs_on. */
    function occursOn(a, iso, week1) {
        if (a.date) return a.date === iso;
        if (a.day !== DAYS[weekdayOf(iso)] || iso < week1) return false;
        if (a.valid_from && iso < a.valid_from) return false;
        if (a.valid_until && iso > a.valid_until) return false;
        if (a.week_type === 'both') return true;
        return (a.week_type === 'odd') === isOddWeek(week1, iso);
    }

    // ── State ───────────────────────────────────────────────────
    const root = document.getElementById('manage');
    const TODAY = root.dataset.today;
    let WEEK1 = root.dataset.week1;
    let activities = [];
    let shared = [];
    let ctx = { categories: [], friends: [], groups: [] };
    let hourStart = DEFAULT_HOUR_START;
    let hourEnd = DEFAULT_HOUR_END;
    let currentView = document.getElementById('week-tabs').dataset.current || 'odd';
    let calMonday = mondayOf(TODAY);
    let overlayUsers = [];
    let overlayData = null;
    let pendingSave = null;
    let pendingConflicts = [];
    let pendingDeleteId = null;
    let dragState = null;
    let editing = null;       // own activity being edited (or null for new)
    let sharedEditing = null; // shared activity in the shared modal

    // ── DOM ─────────────────────────────────────────────────────
    const $ = (id) => document.getElementById(id);
    const grid = $('wgrid');
    const gridWrap = $('grid-wrap');
    const weekTabs = document.querySelectorAll('#week-tabs .seg-btn');
    const legend = $('legend');
    const calNav = $('cal-nav');

    const modalOverlay = $('modal-overlay');
    const form = $('activity-form');
    const formId = $('form-id');
    const formTitleInput = $('form-title');
    const formCategory = $('form-category');
    const formDay = $('form-day');
    const formFrom = $('form-from');
    const formUntil = $('form-until');
    const formDate = $('form-date');
    const formStart = $('form-start');
    const formEnd = $('form-end');
    const visAll = $('vis-all');
    const visExceptions = $('vis-exceptions');
    const invList = $('inv-list');

    const sharedOverlay = $('shared-overlay');
    const busyOverlay = $('busy-overlay');
    const conflictOverlay = $('conflict-overlay');
    const conflictList = $('conflict-list');
    const deleteOverlay = $('delete-overlay');
    const deleteMessage = $('delete-message');
    const ALL_OVERLAYS = [modalOverlay, sharedOverlay, busyOverlay, conflictOverlay, deleteOverlay];

    const radioValue = (name) => (form.querySelector(`input[name="${name}"]:checked`) || {}).value;
    const setRadio = (name, v) => {
        const r = form.querySelector(`input[name="${name}"][value="${v}"]`);
        if (r) r.checked = true;
    };

    // ── Theme toggle ────────────────────────────────────────────
    const themeToggle = $('theme-toggle');
    if (themeToggle) {
        themeToggle.addEventListener('click', () => {
            const r = document.documentElement;
            const isLight = r.getAttribute('data-theme') === 'light';
            if (isLight) r.removeAttribute('data-theme');
            else r.setAttribute('data-theme', 'light');
            try { localStorage.setItem('iambusy-theme', isLight ? 'dark' : 'light'); } catch (_) { }
        });
    }

    // ── API ─────────────────────────────────────────────────────
    const csrfMeta = document.querySelector('meta[name="csrf-token"]');
    const CSRF_TOKEN = csrfMeta ? csrfMeta.content : '';

    // Every call goes through here: adds the CSRF token and sends the user
    // back to the login page when the session is gone.
    async function api(url, options = {}) {
        const headers = Object.assign({ 'X-CSRF-Token': CSRF_TOKEN }, options.headers || {});
        const res = await fetch(url, Object.assign({ credentials: 'same-origin' }, options, { headers }));
        if (res.status === 401) {
            window.location.href = '/login?next=' + encodeURIComponent('/manage');
            throw new Error('auth');
        }
        return res;
    }

    const jsonOpts = (method, body) => ({
        method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });

    async function fetchAll() {
        const [ra, rc] = await Promise.all([api('/api/activities'), api('/api/manage/context')]);
        if (!ra.ok || !rc.ok) { showToast('Nu am putut încărca orarul.', 'error'); return; }
        activities = await ra.json();
        ctx = await rc.json();
        shared = ctx.shared || [];
        WEEK1 = ctx.week1_start || WEEK1;
        fillCategorySelects();
        renderLegend();
        renderInviteBanner();
        updateHourRange();
        renderGrid();
    }

    async function saveActivity(data) {
        const isEdit = !!data.id;
        const url = isEdit ? `/api/activities/${data.id}` : '/api/activities';
        const res = await api(url, jsonOpts(isEdit ? 'PUT' : 'POST', data));
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
        const res = await api(`/api/activities/${id}`, { method: 'DELETE' });
        return res.ok;
    }

    // ── Lookups ─────────────────────────────────────────────────
    const categoryById = (id) => ctx.categories.find(c => c.id === id);
    const colorOf = (catId) => (categoryById(catId) || {}).color || 'x';

    function fillCategorySelects() {
        [formCategory, $('shared-category')].forEach(sel => {
            const keep = sel.value;
            sel.innerHTML = '';
            ctx.categories.forEach(c => sel.appendChild(new Option(c.name, c.id)));
            if (keep) sel.value = keep;
        });
    }

    function renderLegend() {
        legend.innerHTML = '';
        ctx.categories.forEach(c => {
            const s = document.createElement('span');
            s.innerHTML = `<i class="sw-${c.color}"></i>`;
            s.appendChild(document.createTextNode(c.name));
            legend.appendChild(s);
        });
        legend.insertAdjacentHTML('beforeend',
            '<span><i class="only"></i>doar într-o săptămână</span>' +
            '<span><i class="lg-shared"></i>invitat de un prieten</span>' +
            (currentView === 'cal' ? '<span><i class="lg-once"></i>o singură dată</span>' : '') +
            (overlayData ? '<span><i class="lg-overlay"></i>prieten ocupat</span>' : ''));
    }

    function renderInviteBanner() {
        const banner = $('invite-banner');
        const n = shared.filter(s => s.status === 'pending').length;
        banner.hidden = !n;
        banner.textContent = n === 1 ? 'Ai o invitație la o activitate — răspunde aici sau pe pagina Prieteni →'
            : `Ai ${n} invitații la activități — răspunde aici sau pe pagina Prieteni →`;
    }

    // ── What is on the grid right now ───────────────────────────
    /** [{act, kind: 'own'|'shared', dayIndex, iso?}] for the current view. */
    function placedItems() {
        const items = [];
        if (currentView === 'cal') {
            for (let i = 0; i < 7; i++) {
                const iso = addDays(calMonday, i);
                activities.forEach(a => { if (occursOn(a, iso, WEEK1)) items.push({ act: a, kind: 'own', dayIndex: i, iso }); });
                shared.forEach(s => {
                    if (occursOn(s, iso, s.owner_week1_start || WEEK1)) items.push({ act: s, kind: 'shared', dayIndex: i, iso });
                });
            }
        } else {
            const match = (a) => !a.date && (a.week_type === currentView || a.week_type === 'both');
            activities.filter(match).forEach(a => items.push({ act: a, kind: 'own', dayIndex: DAYS.indexOf(a.day) }));
            shared.filter(match).forEach(s => items.push({ act: s, kind: 'shared', dayIndex: DAYS.indexOf(s.day) }));
        }
        return items.filter(it => it.dayIndex >= 0);
    }

    // ── Grid ────────────────────────────────────────────────────
    // The grid shows 07:00–22:00 and grows to cover any activity outside
    // that range (all views, so the rows don't shift between tabs) —
    // otherwise e.g. a 04:00 course could never be edited or deleted.
    function updateHourRange() {
        hourStart = DEFAULT_HOUR_START;
        hourEnd = DEFAULT_HOUR_END;
        activities.concat(shared).forEach(a => {
            const [sh] = a.start_time.split(':').map(Number);
            const [eh, em] = a.end_time.split(':').map(Number);
            hourStart = Math.min(hourStart, sh);
            hourEnd = Math.max(hourEnd, em > 0 ? eh + 1 : eh);
        });
    }

    function renderGrid() {
        grid.innerHTML = '';
        const items = placedItems();
        calNav.hidden = currentView !== 'cal';
        if (currentView === 'cal') {
            const end = addDays(calMonday, 6);
            const wk = weekIndex(WEEK1, calMonday) + 1;
            $('cal-week-label').textContent = `${shortDate(calMonday)} – ${shortDate(end)}` +
                (calMonday >= mondayOf(WEEK1) ? ` · S${wk} ${isOddWeek(WEEK1, calMonday) ? 'impară' : 'pară'}` : ' · vacanță');
        }

        grid.appendChild(el('div', 'g-corner', ''));
        DAYS.forEach((day, i) => {
            const h = el('div', 'g-day' + (i >= 5 ? ' weekend' : ''), '');
            const n = items.filter(it => it.dayIndex === i).length;
            let label = DAY_LABELS[day].substring(0, 3);
            if (currentView === 'cal') {
                const iso = addDays(calMonday, i);
                label += ` ${fromNum(dayNum(iso)).getUTCDate()}`;
                if (iso === TODAY) h.classList.add('is-today');
            }
            h.innerHTML = `<span>${escapeHtml(label)}</span><small>${n ? n + ' act.' : 'liber'}</small>`;
            h.title = DAY_LABELS[day];
            grid.appendChild(h);
        });

        for (let hour = hourStart; hour < hourEnd; hour++) {
            grid.appendChild(el('div', 'g-time', `${pad(hour)}:00`));
            DAYS.forEach((day, i) => {
                const cell = el('div', 'g-cell' + (i >= 5 ? ' weekend' : ''), '');
                cell.dataset.day = day;
                cell.dataset.dayIndex = i;
                cell.dataset.hour = hour;
                cell.title = `${DAY_LABELS[day]} ${pad(hour)}:00 — click pentru a adăuga`;
                cell.addEventListener('click', (e) => {
                    if (dragState || e.target !== cell) return;
                    const endHour = Math.min(hour + 2, hourEnd);
                    const end = endHour >= 24 ? '23:59' : `${pad(endHour)}:00`;
                    if (currentView === 'cal') {
                        openAddModal({ date: addDays(calMonday, i), once: true, start: `${pad(hour)}:00`, end });
                    } else {
                        openAddModal({ day, start: `${pad(hour)}:00`, end, weekType: currentView });
                    }
                });
                grid.appendChild(cell);
            });
        }

        if (currentView === 'cal' && overlayData) placeOverlay();
        items.forEach(placeItem);
        renderLegend();
    }

    function cellAt(dayIndex, hour) {
        return grid.querySelectorAll('.g-cell')[(hour - hourStart) * DAYS.length + dayIndex];
    }

    function placeOverlay() {
        overlayData.people.forEach(p => {
            for (let i = 0; i < 7; i++) {
                const iso = addDays(calMonday, i);
                (p.days[iso] || []).forEach(([s, e]) => {
                    const sh = Math.floor(s / 60);
                    if (sh < hourStart || sh >= hourEnd) return;
                    const cell = cellAt(i, sh);
                    if (!cell) return;
                    const block = el('div', 'busy-ghost', '');
                    block.style.top = `${((s % 60) / 60) * 100}%`;
                    block.style.height = `calc(${((e - s) / 60) * 100}% - 2px)`;
                    block.title = `${p.display_name} e ocupat ${pad(sh)}:${pad(s % 60)}–${pad(Math.floor(e / 60))}:${pad(e % 60)}`;
                    block.appendChild(el('span', '', p.display_name.split(' ')[0]));
                    cell.appendChild(block);
                });
            }
        });
    }

    function placeItem(item) {
        const act = item.act;
        const own = item.kind === 'own';
        const [sh, sm] = act.start_time.split(':').map(Number);
        const [eh, em] = act.end_time.split(':').map(Number);
        if (sh < hourStart || sh >= hourEnd) return;
        const targetCell = cellAt(item.dayIndex, sh);
        if (!targetCell) return;

        const durationMinutes = (eh * 60 + em) - (sh * 60 + sm);
        const parts = act.title.split(' | ');
        const subject = parts[0] || act.title;
        const room = parts[1] || '';
        // Weekly activities only move on the odd / even tabs; shared ones never.
        const draggable = own && (currentView !== 'cal' || !!act.date);

        const card = document.createElement('div');
        card.className = `act c-${colorOf(own ? act.category_id : act.my_category_id)}`;
        card.style.top = `${(sm / 60) * 100}%`;
        card.style.height = `calc(${(durationMinutes / 60) * 100}% - 4px)`;
        card.dataset.id = act.id;
        card.tabIndex = 0;
        card.setAttribute('role', 'button');
        if (act.week_type !== 'both' && !act.date) card.classList.add('only-week');
        if (act.date) card.classList.add('once');
        if (!own) card.classList.add('shared', `st-${act.status}`);
        if (!draggable) card.classList.add('locked');

        const when = act.date ? shortDate(act.date) : WEEK_LABELS[act.week_type];
        const range = !act.date && (act.valid_from || act.valid_until)
            ? ` · ${act.valid_from ? shortDate(act.valid_from) : '…'}–${act.valid_until ? shortDate(act.valid_until) : '…'}` : '';
        card.setAttribute('aria-label', `${subject}, ${act.start_time}–${act.end_time}, ${when}${range}` +
            (own ? '' : `, de la ${act.owner_name}${act.status === 'pending' ? ', invitație fără răspuns' : ''}`));
        card.title = card.getAttribute('aria-label');

        const going = own ? (act.participants || []).filter(p => p.status === 'accepted').length : 0;
        let badge = '';
        if (!own) badge = `<span class="act-week">${act.status === 'pending' ? '?' : '👥'}</span>`;
        else if (act.date) badge = '<span class="act-week">1×</span>';
        else if (act.week_type !== 'both') badge = `<span class="act-week">${act.week_type === 'odd' ? 'IMP' : 'PAR'}</span>`;

        card.innerHTML = `
            <div class="act-subject">${escapeHtml(subject)}</div>
            ${room ? `<div class="act-room">${escapeHtml(room)}</div>` : ''}
            ${!own ? `<div class="act-room">de la ${escapeHtml(act.owner_name)}</div>` : ''}
            <div class="act-time">${act.start_time}–${act.end_time}${range ? '<span class="act-range">↔</span>' : ''}${going ? ` · 👥${going}` : ''}</div>
            ${badge}
            ${own ? `<button type="button" class="act-del" aria-label="Șterge ${escapeHtml(subject)}">&times;</button>` : ''}
        `;

        if (!own) {
            card.addEventListener('click', () => openSharedModal(act));
            card.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openSharedModal(act); }
            });
            targetCell.appendChild(card);
            return;
        }

        // ── Drag & drop (pointer events) ────────────────────
        if (draggable) card.style.touchAction = 'none';
        card.addEventListener('pointerdown', (e) => {
            if (e.target.classList.contains('act-del')) return;
            if (!draggable) return;
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
            if (e.target.classList.contains('act-del')) return;
            if (!draggable) { openEditModal(act); return; }
            if (!dragState || dragState.card !== card) return;
            const wasDrag = dragState.hasMoved;
            if (wasDrag) {
                if (dragState.ghost) dragState.ghost.remove();
                card.classList.remove('dragging');
                clearDropHighlights();
                const target = getDropTarget(e.clientX, e.clientY);
                if (target) await handleDrop(dragState.act, target);
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
        return cell ? {
            day: cell.dataset.day, dayIndex: parseInt(cell.dataset.dayIndex, 10),
            hour: parseInt(cell.dataset.hour, 10),
        } : null;
    }

    async function handleDrop(act, target) {
        const [sh, sm] = act.start_time.split(':').map(Number);
        const [eh, em] = act.end_time.split(':').map(Number);
        const durationMin = (eh * 60 + em) - (sh * 60 + sm);
        const newStart = `${pad(target.hour)}:00`;
        const endTotal = target.hour * 60 + durationMin;
        const newEnd = `${pad(Math.floor(endTotal / 60))}:${pad(endTotal % 60)}`;
        const newDate = act.date ? addDays(calMonday, target.dayIndex) : null;

        if (act.start_time === newStart && (act.date ? act.date === newDate : act.day === target.day)) return;
        if (endTotal > Math.min(hourEnd * 60, 23 * 60 + 59)) {
            showToast('Activitatea ar ieși din grilă. Alege un interval mai devreme.', 'error');
            return;
        }
        const payload = { id: act.id, start_time: newStart, end_time: newEnd };
        if (act.date) payload.date = newDate; else payload.day = target.day;
        const ok = await saveActivity(payload);
        if (ok) {
            showToast(`Mutat: ${act.date ? shortDate(newDate) : DAY_LABELS[target.day]} ${newStart}`);
            await fetchAll();
        }
    }

    // ── Add / edit modal ────────────────────────────────────────
    function setRepeat(mode) {
        setRadio('repeat', mode);
        $('repeat-weekly').hidden = mode !== 'weekly';
        $('repeat-once').hidden = mode !== 'once';
    }

    function fillVisibility(rules) {
        const cat = categoryById(parseInt(formCategory.value, 10));
        const inherited = cat ? LEVEL_LABELS[cat.friends_level] : LEVEL_LABELS.busy;
        const all = (rules || []).find(r => r.subject_type === 'all');
        visAll.innerHTML = '';
        visAll.appendChild(new Option(`Ca în categorie (${inherited})`, ''));
        Object.entries(LEVEL_LABELS).forEach(([k, v]) => visAll.appendChild(new Option(v, k)));
        visAll.value = all ? all.level : '';
        visExceptions.innerHTML = '';
        (rules || []).filter(r => r.subject_type !== 'all').forEach(r => addException(r));
        updateSummaries();
    }

    function subjectOptions(select, selected) {
        select.innerHTML = '';
        if (ctx.groups.length) {
            const og = document.createElement('optgroup');
            og.label = 'Grupuri';
            ctx.groups.forEach(gr => og.appendChild(new Option(gr.name, `group:${gr.id}`)));
            select.appendChild(og);
        }
        if (ctx.friends.length) {
            const of = document.createElement('optgroup');
            of.label = 'Prieteni';
            ctx.friends.forEach(f => of.appendChild(new Option(`${f.display_name} (@${f.username})`, `friend:${f.id}`)));
            select.appendChild(of);
        }
        if (selected) select.value = selected;
    }

    function addException(rule) {
        if (!ctx.groups.length && !ctx.friends.length) {
            showToast('Adaugă întâi prieteni sau grupuri.', 'error');
            return;
        }
        const row = el('div', 'vis-row', '');
        const subj = document.createElement('select');
        subj.className = 'select select-sm';
        subj.setAttribute('aria-label', 'Pentru cine');
        subjectOptions(subj, rule ? `${rule.subject_type}:${rule.subject_id}` : '');
        const lvl = document.createElement('select');
        lvl.className = 'select select-sm';
        lvl.setAttribute('aria-label', 'Ce vede');
        Object.entries(LEVEL_LABELS).forEach(([k, v]) => lvl.appendChild(new Option(v, k)));
        lvl.value = rule ? rule.level : 'full';
        const del = el('button', 'btn btn-sm btn-ghost', '×');
        del.type = 'button';
        del.setAttribute('aria-label', 'Scoate excepția');
        del.addEventListener('click', () => { row.remove(); updateSummaries(); });
        [subj, lvl].forEach(s => {
            const w = el('div', 'select-wrap', '');
            w.appendChild(s);
            row.appendChild(w);
            s.addEventListener('change', updateSummaries);
        });
        row.appendChild(del);
        visExceptions.appendChild(row);
        updateSummaries();
    }

    function collectRules() {
        const rules = [];
        if (visAll.value) rules.push({ subject_type: 'all', subject_id: 0, level: visAll.value });
        visExceptions.querySelectorAll('.vis-row').forEach(row => {
            const [subj, lvl] = row.querySelectorAll('select');
            const [type, id] = subj.value.split(':');
            if (type && id) rules.push({ subject_type: type, subject_id: parseInt(id, 10), level: lvl.value });
        });
        return rules;
    }

    function fillInvitees(participants, preselect) {
        invList.innerHTML = '';
        const status = {};
        (participants || []).forEach(p => { status[p.user_id] = p.status; });
        if (!ctx.friends.length) {
            invList.appendChild(el('span', 'hint', 'Nu ai prieteni încă — adaugă-i din pagina Prieteni.'));
        }
        ctx.friends.forEach(f => {
            const label = el('label', 'check-chip', '');
            const box = document.createElement('input');
            box.type = 'checkbox';
            box.value = f.id;
            box.checked = f.id in status || (preselect || []).includes(f.username);
            box.addEventListener('change', updateSummaries);
            const span = el('span', '', f.display_name);
            if (status[f.id]) {
                const s = el('i', `rsvp rsvp-${status[f.id]}`,
                    { accepted: 'vine', pending: 'așteaptă', declined: 'nu vine' }[status[f.id]]);
                span.appendChild(document.createTextNode(' '));
                span.appendChild(s);
            }
            label.append(box, span);
            invList.appendChild(label);
        });
        updateSummaries();
    }

    function updateSummaries() {
        const rules = collectRules();
        $('vis-summary').textContent = rules.length
            ? `· ${rules.length} ${rules.length === 1 ? 'regulă' : 'reguli'}` : '· după categorie';
        const n = invList.querySelectorAll('input:checked').length;
        $('inv-summary').textContent = n ? `· ${n}` : '';
    }

    function openAddModal(opts = {}) {
        editing = null;
        $('modal-title').textContent = 'Adaugă activitate';
        formId.value = '';
        formTitleInput.value = '';
        const fallback = ctx.categories.find(c => c.name === 'Altele') || ctx.categories[0];
        formCategory.value = fallback ? fallback.id : '';
        formDay.value = opts.day || (opts.date ? DAYS[weekdayOf(opts.date)] : 'Luni');
        setRadio('week_type', opts.weekType && opts.weekType !== 'cal' ? opts.weekType : 'both');
        formFrom.value = formUntil.value = '';
        formDate.value = opts.date || (currentView === 'cal' ? calMonday : TODAY);
        formStart.value = opts.start || '08:00';
        formEnd.value = opts.end || '10:00';
        setRepeat(opts.once ? 'once' : 'weekly');
        fillVisibility([]);
        fillInvitees([], opts.invite);
        $('vis-section').open = false;
        $('inv-section').open = !!(opts.invite && opts.invite.length);
        showModal(modalOverlay);
        formTitleInput.focus();
    }

    function openEditModal(act) {
        editing = act;
        $('modal-title').textContent = 'Editează activitatea';
        formId.value = act.id;
        formTitleInput.value = act.title;
        formCategory.value = act.category_id || '';
        formDay.value = act.day;
        setRadio('week_type', act.week_type);
        formFrom.value = act.valid_from || '';
        formUntil.value = act.valid_until || '';
        formDate.value = act.date || (currentView === 'cal' ? calMonday : TODAY);
        formStart.value = act.start_time;
        formEnd.value = act.end_time;
        setRepeat(act.date ? 'once' : 'weekly');
        fillVisibility(act.rules);
        fillInvitees(act.participants);
        $('vis-section').open = (act.rules || []).length > 0;
        $('inv-section').open = (act.participants || []).length > 0;
        showModal(modalOverlay);
        formTitleInput.focus();
    }

    function openDeleteModal(act) {
        pendingDeleteId = act.id;
        const when = act.date ? shortDate(act.date) : DAY_LABELS[act.day];
        const people = (act.participants || []).filter(p => p.status !== 'declined').length;
        deleteMessage.textContent = `Sigur vrei să ștergi „${act.title.split(' | ')[0]}” (${when}, ${act.start_time}–${act.end_time})?` +
            (people ? ` Cei ${people} invitați vor fi anunțați că s-a anulat.` : '');
        showModal(deleteOverlay);
    }

    function showConflictModal(conflicts) {
        conflictList.innerHTML = '';
        conflicts.forEach(c => {
            const item = el('div', 'conflict-item', '');
            const when = c.date ? shortDate(c.date) : `${DAY_LABELS[c.day] || c.day} · ${WEEK_LABELS[c.week_type] || c.week_type}`;
            item.innerHTML = `<b>${escapeHtml(c.title.split(' | ')[0])}</b>
                <span>${escapeHtml(when)} · ${c.start_time}–${c.end_time}</span>`;
            conflictList.appendChild(item);
        });
        showModal(conflictOverlay);
    }

    // ── Shared activity modal ───────────────────────────────────
    function describeWhen(a) {
        const times = `${a.start_time}–${a.end_time}`;
        if (a.date) return `${DAY_LABELS[DAYS[weekdayOf(a.date)]]}, ${shortDate(a.date)} · ${times}`;
        let s = `În fiecare ${DAY_LABELS[a.day].toLowerCase()} · ${times}`;
        if (a.week_type !== 'both') s += ` · ${WEEK_LABELS[a.week_type]}`;
        if (a.valid_from || a.valid_until) s += ` · ${a.valid_from ? shortDate(a.valid_from) : '…'} – ${a.valid_until ? shortDate(a.valid_until) : '…'}`;
        return s;
    }

    function openSharedModal(act) {
        sharedEditing = act;
        $('shared-name').textContent = act.title;
        $('shared-when').textContent = describeWhen(act);
        $('shared-owner').textContent = `Organizată de ${act.owner_name} (@${act.owner_username})`;
        const people = $('shared-people');
        people.innerHTML = '';
        (act.participants || []).forEach(p => {
            const li = el('li', '', p.display_name + ' ');
            li.appendChild(el('i', `rsvp rsvp-${p.status}`, { accepted: 'vine', pending: 'nu a răspuns', declined: 'nu vine' }[p.status]));
            people.appendChild(li);
        });
        const sel = $('shared-category');
        sel.value = act.my_category_id || (ctx.categories.find(c => c.name === 'Altele') || ctx.categories[0] || {}).id || '';
        const vis = $('shared-vis');
        vis.innerHTML = '';
        vis.appendChild(new Option('Ca în categorie', ''));
        Object.entries(LEVEL_LABELS).forEach(([k, v]) => vis.appendChild(new Option(v, k)));
        const all = (act.rules || []).find(r => r.subject_type === 'all');
        vis.value = all ? all.level : '';
        const pending = act.status === 'pending';
        $('shared-title').textContent = pending ? 'Invitație' : 'Activitate comună';
        $('shared-save').textContent = pending ? 'Vin' : 'Salvează';
        $('shared-decline').hidden = !pending;
        $('shared-leave').hidden = pending;
        showModal(sharedOverlay);
    }

    $('shared-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const act = sharedEditing;
        if (!act) return;
        const category_id = parseInt($('shared-category').value, 10);
        const rules = (act.rules || []).filter(r => r.subject_type !== 'all');
        if ($('shared-vis').value) rules.push({ subject_type: 'all', subject_id: 0, level: $('shared-vis').value });
        if (act.status === 'pending') {
            const r = await api(`/api/shared/${act.id}/respond`, jsonOpts('POST', { accept: true, category_id }));
            if (!r.ok) { showToast('Invitația nu mai există.', 'error'); hideModal(sharedOverlay); await fetchAll(); return; }
        }
        const res = await api(`/api/shared/${act.id}`, jsonOpts('PUT', { category_id, rules }));
        hideModal(sharedOverlay);
        showToast(res.ok ? (act.status === 'pending' ? 'Ai acceptat — apare în calendarul tău.' : 'Salvat.') : 'Ceva nu a mers.', res.ok ? 'success' : 'error');
        await fetchAll();
    });

    async function leaveShared(message) {
        const act = sharedEditing;
        if (!act) return;
        const res = await api(`/api/shared/${act.id}/leave`, { method: 'POST' });
        hideModal(sharedOverlay);
        showToast(res.ok ? message : 'Ceva nu a mers.', res.ok ? 'success' : 'error');
        await fetchAll();
    }
    $('shared-leave').addEventListener('click', () => leaveShared('Ai renunțat — organizatorul a fost anunțat.'));
    $('shared-decline').addEventListener('click', () => leaveShared('Invitație refuzată.'));
    $('shared-close').addEventListener('click', () => hideModal(sharedOverlay));

    // ── Friends' busy overlay ───────────────────────────────────
    function openBusyPicker() {
        const list = $('busy-list');
        list.innerHTML = '';
        if (!ctx.friends.length) list.appendChild(el('span', 'hint', 'Nu ai prieteni încă.'));
        ctx.friends.forEach(f => {
            const label = el('label', 'check-chip', '');
            const box = document.createElement('input');
            box.type = 'checkbox';
            box.value = f.username;
            box.checked = overlayUsers.includes(f.username);
            label.append(box, el('span', '', f.display_name));
            list.appendChild(label);
        });
        showModal(busyOverlay);
    }

    async function loadOverlay() {
        if (!overlayUsers.length || currentView !== 'cal') { overlayData = null; renderGrid(); return; }
        const params = new URLSearchParams({
            users: overlayUsers.slice(0, 10).join(','), from: calMonday, to: addDays(calMonday, 6),
            me: '0', start: '0', end: '24', min: '15',
        });
        const res = await api(`/api/busy?${params}`);
        overlayData = res.ok ? await res.json() : null;
        if (!res.ok) showToast('Nu am putut încărca programul prietenilor.', 'error');
        renderGrid();
    }

    $('btn-overlay').addEventListener('click', openBusyPicker);
    $('busy-close').addEventListener('click', () => hideModal(busyOverlay));
    $('busy-clear').addEventListener('click', () => {
        overlayUsers = [];
        hideModal(busyOverlay);
        $('btn-overlay').textContent = 'Prieteni ocupați…';
        loadOverlay();
    });
    $('busy-apply').addEventListener('click', () => {
        overlayUsers = [...$('busy-list').querySelectorAll('input:checked')].map(i => i.value);
        hideModal(busyOverlay);
        $('btn-overlay').textContent = overlayUsers.length ? `Prieteni ocupați (${overlayUsers.length})` : 'Prieteni ocupați…';
        loadOverlay();
    });

    // ── Modal plumbing ──────────────────────────────────────────
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

    function hideAllModals() { ALL_OVERLAYS.forEach(hideModal); }

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
    function activateTab(view) {
        currentView = view;
        weekTabs.forEach(t => t.setAttribute('aria-selected', String(t.dataset.week === view)));
        if (view === 'cal' && overlayUsers.length) loadOverlay();
        else renderGrid();
    }

    weekTabs.forEach(tab => tab.addEventListener('click', () => activateTab(tab.dataset.week)));
    weekTabs.forEach(t => t.setAttribute('aria-selected', String(t.dataset.week === currentView)));

    $('cal-week-prev').addEventListener('click', () => { calMonday = addDays(calMonday, -7); loadOverlay(); });
    $('cal-week-next').addEventListener('click', () => { calMonday = addDays(calMonday, 7); loadOverlay(); });
    $('cal-week-today').addEventListener('click', () => { calMonday = mondayOf(TODAY); loadOverlay(); });

    $('btn-add').addEventListener('click', () => openAddModal(
        currentView === 'cal' ? { once: true } : { weekType: currentView }));
    $('invite-banner').addEventListener('click', (e) => {
        const first = shared.find(s => s.status === 'pending');
        if (first) { e.preventDefault(); openSharedModal(first); }
    });

    form.addEventListener('change', (e) => {
        if (e.target.name === 'repeat') setRepeat(e.target.value);
        if (e.target === formCategory) fillVisibility(collectRules());
        if (e.target === formDate && formDate.value) formDay.value = DAYS[weekdayOf(formDate.value)];
    });
    $('vis-add').addEventListener('click', () => addException(null));

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const once = radioValue('repeat') === 'once';
        const data = {
            title: formTitleInput.value.trim(),
            category_id: parseInt(formCategory.value, 10) || undefined,
            start_time: formStart.value,
            end_time: formEnd.value,
            rules: collectRules(),
            invitees: [...invList.querySelectorAll('input:checked')].map(i => parseInt(i.value, 10)),
        };
        if (once) {
            if (!formDate.value) { showToast('Alege data.', 'error'); return; }
            data.date = formDate.value;
        } else {
            Object.assign(data, {
                date: null, day: formDay.value, week_type: radioValue('week_type') || 'both',
                valid_from: formFrom.value || null, valid_until: formUntil.value || null,
            });
            if (data.valid_from && data.valid_until && data.valid_until < data.valid_from) {
                showToast('Perioada se termină înainte să înceapă.', 'error');
                return;
            }
        }
        if (formId.value) data.id = parseInt(formId.value, 10);
        if (data.end_time <= data.start_time) {
            showToast('Ora de sfârșit trebuie să fie după cea de început.', 'error');
            return;
        }
        const ok = await saveActivity(data);
        if (ok) {
            hideModal(modalOverlay);
            const invited = data.invitees.length - ((editing && editing.participants) || []).length;
            showToast(data.id ? 'Activitate actualizată.' : (invited > 0 ? 'Activitate adăugată, invitațiile au plecat.' : 'Activitate adăugată.'));
            if (once && currentView !== 'cal') { calMonday = mondayOf(data.date); activateTab('cal'); }
            await fetchAll();
        }
    });

    $('btn-cancel').addEventListener('click', () => hideModal(modalOverlay));
    $('modal-close').addEventListener('click', () => hideModal(modalOverlay));

    $('conflict-close').addEventListener('click', () => {
        hideModal(conflictOverlay);
        pendingSave = null;
        pendingConflicts = [];
    });

    $('conflict-choose-another').addEventListener('click', () => {
        hideModal(conflictOverlay);
        if (pendingSave && pendingSave.title !== undefined) {
            // Came from the form → reopen it with the same values.
            showModal(modalOverlay);
            formStart.focus();
        }
        pendingSave = null;
        pendingConflicts = [];
    });

    $('conflict-delete').addEventListener('click', async () => {
        for (const c of pendingConflicts) await removeActivity(c.id);
        if (pendingSave) {
            pendingSave.force = true;
            await saveActivity(pendingSave);
        }
        hideAllModals();
        showToast('Suprapunere rezolvată, activitate salvată.');
        pendingSave = null;
        pendingConflicts = [];
        await fetchAll();
    });

    $('delete-cancel').addEventListener('click', () => { hideModal(deleteOverlay); pendingDeleteId = null; });
    $('delete-close').addEventListener('click', () => { hideModal(deleteOverlay); pendingDeleteId = null; });
    $('delete-confirm').addEventListener('click', async () => {
        if (pendingDeleteId === null) return;
        await removeActivity(pendingDeleteId);
        hideModal(deleteOverlay);
        showToast('Activitate ștearsă.');
        pendingDeleteId = null;
        await fetchAll();
    });

    ALL_OVERLAYS.forEach(overlay => {
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
    fetchAll().then(() => {
        if (gridWrap) gridWrap.scrollTop = 0;

        // Pre-fill from the daily view's "Adaugă" links and the free-time finder.
        const q = new URLSearchParams(location.search);
        if (q.has('day') || q.has('start') || q.has('date')) {
            const week = q.get('week');
            const date = /^\d{4}-\d{2}-\d{2}$/.test(q.get('date') || '') ? q.get('date') : null;
            const invite = (q.get('invite') || '').split(',').filter(Boolean);
            const weekly = week === 'odd' || week === 'even' || week === 'both';
            if (weekly && week !== 'both') activateTab(week);
            else if (!weekly && date) { calMonday = mondayOf(date); activateTab('cal'); }
            const day = DAYS.includes(q.get('day')) ? q.get('day') : null;
            openAddModal({
                day, date, once: !weekly && !!date, invite,
                start: q.get('start') || '10:00', end: q.get('end') || '12:00',
                weekType: weekly ? week : 'both',
            });
            history.replaceState(null, '', '/manage');
        }
    });
})();
