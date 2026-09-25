/**
 * IamBusy — Daily view client script.
 *
 *  • Live clock, countdown and progress bar for the current block
 *  • Auto-reload when the current block ends or the day rolls over
 *  • Auto-scroll to the current block
 *  • Navigation: ← / → keys, swipe, "T" for today, "C" for calendar
 *  • Month calendar popover with per-day busy bars and a day preview
 *  • Theme toggle (dark by default, light opt-in)
 */

(function () {
    'use strict';

    const pad = (n) => String(n).padStart(2, '0');
    const loadedOn = new Date().toDateString();

    const MONTHS = ['ianuarie', 'februarie', 'martie', 'aprilie', 'mai', 'iunie',
        'iulie', 'august', 'septembrie', 'octombrie', 'noiembrie', 'decembrie'];
    const MONTHS_SHORT = ['ian', 'feb', 'mar', 'apr', 'mai', 'iun', 'iul', 'aug', 'sep', 'oct', 'nov', 'dec'];
    const DOW = ['L', 'Ma', 'Mi', 'J', 'V', 'S', 'D'];
    const DAY_KEYS = ['Luni', 'Marti', 'Miercuri', 'Joi', 'Vineri', 'Sambata', 'Duminica'];
    const DAY_LABELS = ['Luni', 'Marți', 'Miercuri', 'Joi', 'Vineri', 'Sâmbătă', 'Duminică'];

    function formatDuration(totalMinutes) {
        const h = Math.floor(totalMinutes / 60);
        const m = totalMinutes % 60;
        if (h && m) return `${h}h ${m}min`;
        if (h) return `${h}h`;
        return `${m} min`;
    }

    // ═══════════════════════════════════════════════ Theme ═══
    const themeToggle = document.getElementById('theme-toggle');
    if (themeToggle) {
        themeToggle.addEventListener('click', () => {
            const root = document.documentElement;
            const isLight = root.getAttribute('data-theme') === 'light';
            if (isLight) root.removeAttribute('data-theme');
            else root.setAttribute('data-theme', 'light');
            try { localStorage.setItem('iambusy-theme', isLight ? 'dark' : 'light'); } catch (_) { }
            const meta = document.querySelector('meta[name="theme-color"]');
            if (meta) meta.content = isLight ? '#17171c' : '#f3efe4';
        });
    }

    // ═══════════════════════════════════ Clock & countdown ═══
    const statusSub = document.getElementById('status-sub');
    const endTime = statusSub && statusSub.dataset.end ? new Date(statusSub.dataset.end) : null;
    if (endTime && endTime.getHours() === 23 && endTime.getMinutes() === 59) {
        endTime.setMinutes(60); // 23:59 is the end-of-day sentinel → midnight
    }

    const currentBlock = document.querySelector('[data-current="true"]');
    const progressFill = currentBlock && currentBlock.querySelector('.progress-fill');
    const blockStart = currentBlock ? new Date(currentBlock.dataset.start) : null;
    const blockEnd = currentBlock ? new Date(currentBlock.dataset.end) : null;
    const clockEl = document.getElementById('clock');

    function tick() {
        const now = new Date();
        if (clockEl) {
            clockEl.innerHTML = `${pad(now.getHours())}:${pad(now.getMinutes())}<span class="sec">${pad(now.getSeconds())}</span>`;
        }
        if (endTime) {
            const minutesLeft = Math.ceil((endTime - now) / 60000);
            if (minutesLeft <= 0) { window.location.reload(); return; }
            statusSub.textContent = `Mai sunt ${formatDuration(minutesLeft)}.`;
        }
        if (progressFill && blockStart && blockEnd) {
            const pct = Math.min(100, Math.max(0, ((now - blockStart) / (blockEnd - blockStart)) * 100));
            progressFill.style.width = `${pct}%`;
        }
        if (now.toDateString() !== loadedOn && !window.location.search.includes('date=')) {
            window.location.reload();
        }
    }

    tick();
    setInterval(tick, 1000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(); });

    if (currentBlock) {
        setTimeout(() => {
            const rect = currentBlock.getBoundingClientRect();
            if (rect.top < 0 || rect.bottom > window.innerHeight) {
                currentBlock.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }, 350);
    }

    // ═══════════════════════════════════════ Month calendar ═══
    const cal = document.getElementById('cal');
    const calBackdrop = document.getElementById('cal-backdrop');
    const dateDisplay = document.getElementById('date-display');
    const calGrid = document.getElementById('cal-grid');
    const calMonth = document.getElementById('cal-month');
    const calSub = document.getElementById('cal-sub');
    const calPrev = document.getElementById('cal-prev');
    const calNext = document.getElementById('cal-next');
    const calTodayBtn = document.getElementById('cal-today');
    const pvTitle = document.getElementById('cal-preview-title');
    const pvWeek = document.getElementById('cal-preview-week');
    const pvList = document.getElementById('cal-preview-list');
    const pvEmpty = document.getElementById('cal-preview-empty');
    const pvOpen = document.getElementById('cal-open');
    const pvAdd = document.getElementById('cal-add');

    const todayIso = cal ? cal.dataset.today : null;
    let selectedIso = cal ? cal.dataset.selected : null;
    let viewYear, viewMonth;          // month currently displayed (0-based month)
    const dayCache = new Map();       // iso → day summary from /api/days
    let calOpen = false;
    let lastFocus = null;

    const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const fromIso = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
    const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };

    function gridRange(year, month) {
        const first = new Date(year, month, 1);
        const start = addDays(first, -((first.getDay() + 6) % 7)); // back to Monday
        return { start, end: addDays(start, 41) };                 // 6 rows × 7 days
    }

    async function loadRange(start, end) {
        const missing = [];
        for (let d = new Date(start); d <= end; d = addDays(d, 1)) {
            if (!dayCache.has(iso(d))) missing.push(iso(d));
        }
        if (!missing.length) return;
        const res = await fetch(`/api/days?from=${missing[0]}&to=${missing[missing.length - 1]}`);
        if (!res.ok) return;
        const body = await res.json();
        body.days.forEach(day => dayCache.set(day.date, day));
    }

    function busyBar(segments, selectedClass) {
        const bar = document.createElement('span');
        bar.className = 'busybar';
        bar.setAttribute('aria-hidden', 'true');
        (segments || []).forEach(s => {
            const i = document.createElement('i');
            i.className = `k-${s.kind}`;
            i.style.left = `${s.left}%`;
            i.style.width = `${s.width}%`;
            bar.appendChild(i);
        });
        return bar;
    }

    async function renderCalendar() {
        if (!calGrid) return;
        const { start, end } = gridRange(viewYear, viewMonth);
        calMonth.textContent = `${MONTHS[viewMonth]} ${viewYear}`;
        calSub.textContent = 'se încarcă…';
        await loadRange(start, end);

        calGrid.innerHTML = '';
        calGrid.appendChild(Object.assign(document.createElement('div'), { className: 'cal-dow', textContent: '#' }));
        DOW.forEach(n => {
            const h = document.createElement('div');
            h.className = 'cal-dow';
            h.setAttribute('role', 'columnheader');
            h.textContent = n;
            calGrid.appendChild(h);
        });

        let busyDays = 0;
        for (let row = 0; row < 6; row++) {
            const monday = addDays(start, row * 7);
            const mondayInfo = dayCache.get(iso(monday));
            const wk = document.createElement('div');
            wk.className = 'cal-wk';
            if (mondayInfo && mondayInfo.in_semester) {
                wk.textContent = `S${mondayInfo.week_num}`;
                wk.classList.add(mondayInfo.parity);
                wk.title = `Săptămâna ${mondayInfo.week_num} · ${mondayInfo.parity === 'odd' ? 'impară' : 'pară'}`;
            } else {
                wk.textContent = '–';
                wk.classList.add('vac');
                wk.title = 'Vacanță';
            }
            calGrid.appendChild(wk);

            for (let col = 0; col < 7; col++) {
                const d = addDays(monday, col);
                const key = iso(d);
                const info = dayCache.get(key);
                const acts = info ? info.activities : [];
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'cal-day';
                btn.dataset.iso = key;
                btn.setAttribute('role', 'gridcell');
                if (d.getMonth() !== viewMonth) btn.classList.add('other');
                if (col >= 5) btn.classList.add('weekend');
                if (key === todayIso) btn.classList.add('today');
                if (key === selectedIso) { btn.classList.add('selected'); btn.setAttribute('aria-selected', 'true'); }
                if (!acts.length) btn.classList.add('free');
                if (acts.length && d.getMonth() === viewMonth) busyDays++;
                btn.setAttribute('aria-label',
                    `${DAY_LABELS[col]} ${d.getDate()} ${MONTHS[d.getMonth()]} · ${acts.length ? acts.length + ' activități' : 'liber'}`);

                const num = document.createElement('span');
                num.className = 'cal-num';
                num.textContent = d.getDate();
                btn.appendChild(num);
                btn.appendChild(busyBar(info ? info.segments : []));
                const cnt = document.createElement('span');
                cnt.className = 'cal-cnt';
                cnt.textContent = acts.length ? `${acts.length}×` : '·';
                btn.appendChild(cnt);

                btn.addEventListener('click', () => {
                    if (selectedIso === key) { window.location.href = `?date=${key}`; return; }
                    selectDay(key);
                });
                btn.addEventListener('mouseenter', () => previewDay(key));
                btn.addEventListener('mouseleave', () => previewDay(selectedIso));
                calGrid.appendChild(btn);
            }
        }
        calSub.textContent = busyDays ? `${busyDays} zile cu activități` : 'nicio activitate';
        previewDay(selectedIso);
    }

    function selectDay(key) {
        selectedIso = key;
        calGrid.querySelectorAll('.cal-day').forEach(b => {
            const on = b.dataset.iso === key;
            b.classList.toggle('selected', on);
            if (on) b.setAttribute('aria-selected', 'true'); else b.removeAttribute('aria-selected');
        });
        previewDay(key);
    }

    function previewDay(key) {
        if (!key) return;
        const info = dayCache.get(key);
        const d = fromIso(key);
        const dow = (d.getDay() + 6) % 7;
        const rel = key === todayIso ? ' (azi)' : '';
        pvTitle.textContent = `${DAY_LABELS[dow]}, ${d.getDate()} ${MONTHS_SHORT[d.getMonth()]}${rel}`;
        pvList.innerHTML = '';

        if (!info) {
            pvWeek.hidden = true;
            pvEmpty.hidden = false;
            pvEmpty.textContent = 'Se încarcă…';
            pvOpen.hidden = pvAdd.hidden = true;
            return;
        }

        pvWeek.hidden = false;
        pvWeek.textContent = info.in_semester
            ? `Săpt. ${info.week_num} · ${info.parity === 'odd' ? 'impară' : 'pară'}`
            : 'Vacanță';

        const acts = info.activities;
        if (!acts.length) {
            pvEmpty.hidden = false;
            pvEmpty.textContent = 'Zi complet liberă — poți programa orice.';
        } else {
            pvEmpty.hidden = true;
            // Interleave courses and the free windows between them.
            const windows = info.free_windows || [];
            acts.forEach((a, i) => {
                const meta = [a.kind_label, a.room].filter(Boolean).join(' · ');
                pvList.appendChild(previewRow(a.start, a.end, a.kind || 'x', a.subject, meta));
                const w = windows.find(w => w.start === a.end);
                if (w && i < acts.length - 1) {
                    pvList.appendChild(previewRow(w.start, w.end, 'free', 'Liber', w.duration));
                }
            });
            const last = acts[acts.length - 1];
            pvList.appendChild(previewRow(last.end, '', 'free', 'Liber', 'restul zilei'));
        }

        pvOpen.hidden = false;
        pvOpen.href = `?date=${key}`;
        pvOpen.textContent = key === cal.dataset.selected ? 'Închide' : 'Vezi ziua';
        // Suggest a free slot to schedule into.
        let sStart = '10:00', sEnd = '12:00';
        if (info.free_windows && info.free_windows.length) {
            sStart = info.free_windows[0].start; sEnd = info.free_windows[0].end;
        } else if (acts.length) {
            const [h, m] = acts[acts.length - 1].end.split(':').map(Number);
            const startMin = h * 60 + m;
            if (startMin < 20 * 60) {
                sStart = `${pad(h)}:${pad(m)}`;
                sEnd = `${pad(Math.min(22, h + 2))}:${pad(m)}`;
            }
        }
        pvAdd.hidden = false;
        pvAdd.href = `/manage?day=${DAY_KEYS[dow]}&week=${info.parity}&start=${sStart}&end=${sEnd}`;
    }

    function previewRow(start, end, kind, subject, meta) {
        const li = document.createElement('li');
        li.className = `k-${kind}`;

        const t = document.createElement('span');
        t.className = 'pt';
        const t1 = document.createElement('b');
        t1.textContent = start;
        t.appendChild(t1);
        const t2 = document.createElement('i');
        t2.textContent = end || '→';
        t.appendChild(t2);

        const s = document.createElement('span');
        s.className = 'ps';
        const s1 = document.createElement('span');
        s1.className = 'ps-subject';
        s1.textContent = subject;
        s.appendChild(s1);
        if (meta) {
            const s2 = document.createElement('span');
            s2.className = 'ps-meta';
            s2.textContent = meta;
            s.appendChild(s2);
        }

        li.append(t, s);
        return li;
    }

    function openCalendar() {
        if (!cal || calOpen) return;
        calOpen = true;
        lastFocus = document.activeElement;
        const d = fromIso(selectedIso || todayIso);
        viewYear = d.getFullYear();
        viewMonth = d.getMonth();
        cal.classList.add('open');
        calBackdrop.classList.add('open');
        dateDisplay.setAttribute('aria-expanded', 'true');
        document.body.style.overflow = 'hidden';
        renderCalendar().then(() => {
            const sel = calGrid.querySelector('.cal-day.selected') || calGrid.querySelector('.cal-day');
            if (sel) sel.focus({ preventScroll: true });
        });
    }

    function closeCalendar() {
        if (!cal || !calOpen) return;
        calOpen = false;
        cal.classList.remove('open');
        calBackdrop.classList.remove('open');
        dateDisplay.setAttribute('aria-expanded', 'false');
        document.body.style.overflow = '';
        if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
    }

    if (cal) {
        dateDisplay.addEventListener('click', () => (calOpen ? closeCalendar() : openCalendar()));
        calBackdrop.addEventListener('click', closeCalendar);
        calPrev.addEventListener('click', () => {
            viewMonth--; if (viewMonth < 0) { viewMonth = 11; viewYear--; }
            renderCalendar();
        });
        calNext.addEventListener('click', () => {
            viewMonth++; if (viewMonth > 11) { viewMonth = 0; viewYear++; }
            renderCalendar();
        });
        calTodayBtn.addEventListener('click', () => {
            const t = fromIso(todayIso);
            viewYear = t.getFullYear(); viewMonth = t.getMonth();
            selectedIso = todayIso;
            renderCalendar();
        });
        pvOpen.addEventListener('click', (e) => {
            if (pvOpen.textContent === 'Închide') { e.preventDefault(); closeCalendar(); }
        });

        // Keyboard navigation inside the grid
        calGrid.addEventListener('keydown', (e) => {
            const cur = e.target.closest('.cal-day');
            if (!cur) return;
            const delta = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[e.key];
            if (delta !== undefined) {
                e.preventDefault();
                const next = iso(addDays(fromIso(cur.dataset.iso), delta));
                let btn = calGrid.querySelector(`.cal-day[data-iso="${next}"]`);
                if (!btn) {
                    const nd = fromIso(next);
                    viewYear = nd.getFullYear(); viewMonth = nd.getMonth();
                    selectDayLater(next);
                    return;
                }
                selectDay(next);
                btn.focus();
            } else if (e.key === 'Enter') {
                e.preventDefault();
                window.location.href = `?date=${cur.dataset.iso}`;
            }
        });

        function selectDayLater(key) {
            selectedIso = key;
            renderCalendar().then(() => {
                const btn = calGrid.querySelector(`.cal-day[data-iso="${key}"]`);
                if (btn) btn.focus();
            });
        }
    }

    // ═══════════════════════════════════════ Keyboard & swipe ═══
    const prevLink = document.getElementById('nav-prev');
    const nextLink = document.getElementById('nav-next');

    document.addEventListener('keydown', (e) => {
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        if (e.key === 'Escape') { closeCalendar(); return; }
        if (e.target.closest('input, textarea, select')) return;
        if (calOpen) return; // the grid handles its own keys
        if (e.key === 'ArrowLeft' && prevLink) prevLink.click();
        else if (e.key === 'ArrowRight' && nextLink) nextLink.click();
        else if (e.key === 't' || e.key === 'T') window.location.href = '/';
        else if (e.key === 'c' || e.key === 'C') openCalendar();
    });

    let touchX = null, touchY = null;
    document.addEventListener('touchstart', (e) => {
        if (e.touches.length !== 1 || calOpen) { touchX = null; return; }
        touchX = e.touches[0].clientX;
        touchY = e.touches[0].clientY;
    }, { passive: true });

    document.addEventListener('touchend', (e) => {
        if (touchX === null) return;
        const dx = e.changedTouches[0].clientX - touchX;
        const dy = e.changedTouches[0].clientY - touchY;
        touchX = touchY = null;
        if (Math.abs(dx) < 70 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
        if (dx > 0 && prevLink) prevLink.click();
        else if (dx < 0 && nextLink) nextLink.click();
    }, { passive: true });
})();
