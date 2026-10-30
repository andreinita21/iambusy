/**
 * IamBusy — theme page: live preview of a theme on the whole page, and the
 * colour editor.  The colour maths mirrors themes.py (css_vars) so that the
 * preview is exactly what the server will render once the theme is saved.
 */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const data = JSON.parse($('theme-data').textContent);
    const activeEl = $('theme-active');
    const activeKey = activeEl.dataset.key || 'preset:iambusy-dark';
    const all = data.presets.concat(data.custom);
    const byKey = (k) => all.find(t => t.key === k);
    const KEYS = data.keys.map(k => k.key);
    const LABELS = Object.fromEntries(data.keys.map(k => [k.key, k.label]));

    // ── Colour maths (same as themes.py) ─────────────────────────
    const HEX = /^#[0-9a-f]{6}$/;
    const rgb = (h) => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
    const hex = (c) => '#' + c.map(x => Math.max(0, Math.min(255, Math.round(x))).toString(16).padStart(2, '0')).join('');
    const mix = (a, b, t) => { const x = rgb(a), y = rgb(b); return hex(x.map((v, i) => v + (y[i] - v) * t)); };
    function luminance(h) {
        const [r, g, b] = rgb(h).map(c => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); });
        return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    }
    function contrast(a, b) {
        const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
        return (x + 0.05) / (y + 0.05);
    }

    function cssVars(c, derived) {
        derived = derived || {};
        const dark = luminance(c.paper) < 0.4;
        const [r, g, b] = rgb(c.ink);
        const vars = {
            'color-scheme': dark ? 'dark' : 'light',
            '--paper': c.paper,
            '--paper-2': derived.paper_2 || mix(c.paper, c.ink, 0.06),
            '--card': c.card,
            '--card-2': derived.card_2 || mix(c.card, c.paper, 0.5),
            '--ink': c.ink,
            '--ink-2': derived.ink_2 || mix(c.ink, c.paper, 0.14),
            '--line': c.line || c.ink,
            '--muted': c.muted,
            '--line-soft': `rgba(${r}, ${g}, ${b}, ${dark ? 0.13 : 0.14})`,
            '--on-accent': c.on_accent,
            '--on-accent-inv': luminance(c.on_accent) < 0.4 ? '#ffffff' : '#111111',
        };
        ['yellow', 'pink', 'lime', 'cyan', 'orange', 'purple', 'red', 'blue'].forEach(k => { vars[`--${k}`] = c[k]; });
        return vars;
    }

    // ── Whole-page preview ──────────────────────────────────────
    const previewStyle = document.createElement('style');
    previewStyle.id = 'theme-preview';
    document.head.appendChild(previewStyle);
    const meta = document.querySelector('meta[name="theme-color"]');
    const originalMeta = meta ? meta.content : '';

    function preview(colors, derived) {
        const body = Object.entries(cssVars(colors, derived)).map(([k, v]) => `${k}:${v};`).join('');
        // One more :root than the saved theme's selector, so the preview wins.
        previewStyle.textContent = `html:root:root,html:root:root[data-theme]{${body}}`;
        if (meta) meta.content = colors.paper;
    }

    function clearPreview() {
        previewStyle.textContent = '';
        if (meta) meta.content = originalMeta;
    }

    // ── Gallery: tap a theme to try it on ───────────────────────
    const bar = $('theme-bar');
    const cards = document.querySelectorAll('.theme-card');
    let previewing = null;

    function markCards(key) {
        cards.forEach(c => c.classList.toggle('is-previewing', c.dataset.key === key));
    }

    function showBar(t) {
        previewing = t;
        $('theme-bar-name').textContent = t.name;
        $('theme-use-key').value = t.key;
        const isActive = t.key === activeKey;
        $('theme-use').hidden = isActive;
        const del = $('theme-delete-form');
        del.hidden = !t.id;
        if (t.id) del.action = `/account/theme/${t.id}/delete`;
        bar.hidden = false;
        document.body.classList.add('has-theme-bar');
    }

    function hideBar() {
        previewing = null;
        bar.hidden = true;
        document.body.classList.remove('has-theme-bar');
        markCards(null);
    }

    cards.forEach(card => card.addEventListener('click', () => {
        const t = byKey(card.dataset.key);
        if (!t) return;
        closeEditor(false);
        preview(t.colors, t.derived);
        markCards(t.key);
        showBar(t);
    }));

    $('theme-revert').addEventListener('click', () => { clearPreview(); hideBar(); });
    $('theme-edit').addEventListener('click', () => { if (previewing) openEditor(previewing); });
    $('theme-delete-form').addEventListener('submit', (e) => {
        if (!confirm(`Ștergi tema „${previewing ? previewing.name : ''}”?`)) e.preventDefault();
    });

    // A theme saved on the account replaces the old light / dark switch.
    const forgetToggle = () => { try { localStorage.removeItem('iambusy-theme'); } catch (_) { } };
    $('theme-use-form').addEventListener('submit', forgetToggle);

    // ── Editor ──────────────────────────────────────────────────
    const editor = $('editor');
    const form = $('theme-form');
    const nameInput = $('te-name');
    const idInput = $('te-id');
    const baseSelect = $('te-base');
    const copyBtn = $('te-copy');
    const warn = $('te-warn');
    const pickers = Object.fromEntries(KEYS.map(k => [k, form.querySelector(`input[type=color][data-key="${k}"]`)]));
    const hexes = Object.fromEntries(KEYS.map(k => [k, form.querySelector(`.te-hex[data-key="${k}"]`)]));
    let editingDerived = null;

    function setColors(colors) {
        KEYS.forEach(k => { pickers[k].value = colors[k]; hexes[k].value = colors[k]; hexes[k].classList.remove('is-bad'); });
    }

    function readColors() {
        const out = {};
        for (const k of KEYS) {
            const v = hexes[k].value.trim().toLowerCase();
            if (!HEX.test(v)) return null;
            out[k] = v;
        }
        return out;
    }

    function update() {
        const colors = readColors();
        if (!colors) return;
        preview(colors, editingDerived);
        checkContrast(colors);
    }

    function checkContrast(c) {
        const issues = [];
        const need = (a, b, min, label) => {
            const r = contrast(c[a], c[b]);
            if (r < min) issues.push(`${label} (${r.toFixed(1)}:1)`);
        };
        need('ink', 'paper', 4.5, 'Text pe fundal');
        need('ink', 'card', 4.5, 'Text pe carduri');
        need('muted', 'card', 3, 'Text secundar pe carduri');
        ['yellow', 'pink', 'lime', 'blue', 'orange', 'purple', 'cyan', 'red'].forEach(k =>
            need('on_accent', k, 3, `Text pe culori, pe ${LABELS[k]}`));
        warn.hidden = !issues.length;
        warn.textContent = issues.length
            ? 'Contrast slab, poate fi greu de citit: ' + issues.join(' · ') + '.' : '';
    }

    function openEditor(t, fresh = false) {
        hideBar();
        editor.hidden = false;
        const own = !fresh && !!t.id;
        idInput.value = own ? t.id : '';
        nameInput.value = fresh ? '' : (own ? t.name : `${t.name} (a mea)`).slice(0, 40);
        baseSelect.value = t.key;
        copyBtn.hidden = !own;
        editingDerived = t.derived || null;
        setColors(t.colors);
        update();
        editor.scrollIntoView({ behavior: 'smooth', block: 'start' });
        if (fresh) setTimeout(() => nameInput.focus({ preventScroll: true }), 350);
    }

    function closeEditor(restore = true) {
        if (editor.hidden) return;
        editor.hidden = true;
        warn.hidden = true;
        if (restore) clearPreview();
    }

    $('theme-new').addEventListener('click', () => {
        const count = parseInt(activeEl.dataset.count, 10) || 0;
        const max = parseInt(activeEl.dataset.max, 10) || 12;
        if (count >= max) {
            alert(`Poți păstra cel mult ${max} teme. Șterge una ca să faci loc.`);
            return;
        }
        openEditor(byKey(activeKey) || data.presets[0], true);
    });

    baseSelect.addEventListener('change', () => {
        const t = byKey(baseSelect.value);
        if (!t) return;
        editingDerived = t.derived || null;
        setColors(t.colors);
        update();
    });

    KEYS.forEach(k => {
        pickers[k].addEventListener('input', () => {
            hexes[k].value = pickers[k].value;
            hexes[k].classList.remove('is-bad');
            editingDerived = null;  // the shades follow the edited colours from now on
            update();
        });
        hexes[k].addEventListener('input', () => {
            let v = hexes[k].value.trim().toLowerCase();
            if (/^[0-9a-f]{6}$/.test(v)) { v = '#' + v; hexes[k].value = v; }
            const ok = HEX.test(v);
            hexes[k].classList.toggle('is-bad', !ok);
            if (ok) { pickers[k].value = v; editingDerived = null; update(); }
        });
    });

    copyBtn.addEventListener('click', () => { idInput.value = ''; });
    $('te-cancel').addEventListener('click', () => closeEditor(true));
    form.addEventListener('submit', forgetToggle);

    if (location.hash === '#editor') openEditor(byKey(activeKey) || data.presets[0], true);
})();
