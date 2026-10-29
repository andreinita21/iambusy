/**
 * IamBusy — common free time finder.
 *
 *  • Pick friends, a week, a minimum slot length and daily hours
 *  • One row per day: a lane per person (busy blocks) and a lane with the
 *    slots where everybody is free; each slot links to the editor with the
 *    time and the invitees pre-filled
 */

(function () {
    'use strict';

    const root = document.getElementById('ft');
    if (!root) return;

    const DAY_LABELS = ['Luni', 'Marți', 'Miercuri', 'Joi', 'Vineri', 'Sâmbătă', 'Duminică'];
    const MONTHS = ['ian', 'feb', 'mar', 'apr', 'mai', 'iun', 'iul', 'aug', 'sep', 'oct', 'nov', 'dec'];
    const pad = (n) => String(n).padStart(2, '0');
    const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const fromIso = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
    const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
    const hhmm = (min) => `${pad(Math.floor(min / 60))}:${pad(min % 60)}`;

    const grid = document.getElementById('ft-grid');
    const rangeLabel = document.getElementById('ft-range');
    const minSel = document.getElementById('ft-min');
    const fromInp = document.getElementById('ft-from');
    const toInp = document.getElementById('ft-to');
    const meChk = document.getElementById('ft-me');
    const todayIso = root.dataset.today;
    let monday = fromIso(root.dataset.monday);
    let timer = null;

    function selected() {
        return [...document.querySelectorAll('input[name="ft-friend"]:checked')].map(i => i.value);
    }

    function el(tag, cls, text) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text !== undefined) e.textContent = text;
        return e;
    }

    function lane(label, intervals, cls, w0, w1) {
        const row = el('div', 'ft-lane');
        row.appendChild(el('span', 'ft-who', label));
        const bar = el('span', 'ft-bar');
        intervals.forEach(([s, e]) => {
            const a = Math.max(s, w0), b = Math.min(e, w1);
            if (b <= a) return;
            const i = el('i', cls);
            i.style.left = `${((a - w0) / (w1 - w0)) * 100}%`;
            i.style.width = `${((b - a) / (w1 - w0)) * 100}%`;
            i.title = `${hhmm(a)}–${hhmm(b)}`;
            bar.appendChild(i);
        });
        row.appendChild(bar);
        return row;
    }

    async function load() {
        const end = addDays(monday, 6);
        rangeLabel.textContent = `${monday.getDate()} ${MONTHS[monday.getMonth()]} – ${end.getDate()} ${MONTHS[end.getMonth()]}`;
        const friends = selected();
        if (!friends.length) {
            grid.innerHTML = '';
            grid.appendChild(el('p', 'hint', 'Bifează cel puțin un prieten.'));
            return;
        }
        const params = new URLSearchParams({
            users: friends.join(','), from: iso(monday), to: iso(end),
            min: minSel.value, start: fromInp.value || '8', end: toInp.value || '22',
            me: meChk.checked ? '1' : '0',
        });
        const res = await fetch(`/api/busy?${params}`, { credentials: 'same-origin' });
        if (res.status === 401) { window.location.href = '/login?next=' + encodeURIComponent(location.pathname); return; }
        const body = await res.json();
        grid.innerHTML = '';
        if (!res.ok) { grid.appendChild(el('p', 'hint', body.error || 'Nu am putut încărca programul.')); return; }

        const [w0, w1] = body.window;
        const scale = el('div', 'ft-scale');
        scale.appendChild(el('span', 'ft-who', ''));
        const ticks = el('span', 'ft-ticks');
        for (let h = Math.ceil(w0 / 60); h <= Math.floor(w1 / 60); h += 2) {
            const t = el('i', '', pad(h));
            t.style.left = `${((h * 60 - w0) / (w1 - w0)) * 100}%`;
            ticks.appendChild(t);
        }
        scale.appendChild(ticks);
        grid.appendChild(scale);

        let total = 0;
        for (let i = 0; i < 7; i++) {
            const d = addDays(monday, i);
            const key = iso(d);
            const day = el('div', 'ft-day' + (key === todayIso ? ' today' : '') + (key < todayIso ? ' past' : ''));
            day.appendChild(el('h3', 'ft-day-title', `${DAY_LABELS[i]}, ${d.getDate()} ${MONTHS[d.getMonth()]}`));
            body.people.forEach(p => day.appendChild(lane(p.me ? 'Eu' : p.display_name, p.days[key] || [], 'sw-busy', w0, w1)));
            const slots = body.common[key] || [];
            day.appendChild(lane('Toți', slots.map(s => [s.start, s.end]), 'sw-lime', w0, w1));

            const list = el('div', 'ft-slots');
            if (!slots.length) list.appendChild(el('span', 'hint', 'Niciun interval comun.'));
            slots.forEach(s => {
                total++;
                const end = Math.min(s.end, s.start + 120);
                const a = el('a', 'btn btn-sm', `${s.start_hhmm}–${s.end_hhmm} · Invită`);
                a.href = `/manage?date=${key}&start=${hhmm(s.start)}&end=${hhmm(end)}&invite=${encodeURIComponent(friends.join(','))}`;
                if (key < todayIso) a.classList.add('btn-ghost');
                list.appendChild(a);
            });
            day.appendChild(list);
            grid.appendChild(day);
        }
        if (!total) grid.appendChild(el('p', 'hint', 'Nu există niciun interval comun săptămâna aceasta — încearcă alt interval orar sau mai puțini prieteni.'));
    }

    function schedule() { clearTimeout(timer); timer = setTimeout(load, 200); }

    document.getElementById('ft-prev').addEventListener('click', () => { monday = addDays(monday, -7); load(); });
    document.getElementById('ft-next').addEventListener('click', () => { monday = addDays(monday, 7); load(); });
    root.addEventListener('change', schedule);
    load();
})();
