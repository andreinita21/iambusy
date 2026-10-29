/**
 * IamBusy — login / account / admin pages.
 *
 *  • Theme toggle
 *  • Show / hide password
 *  • Password strength meter + "passwords match" hint (advisory only —
 *    the server enforces the real rules)
 *  • Copy-to-clipboard buttons
 */

(function () {
    'use strict';

    // ── Theme toggle ────────────────────────────────────────────
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

    // ── Show / hide password ────────────────────────────────────
    document.querySelectorAll('.pw-toggle').forEach(btn => {
        const input = document.getElementById(btn.dataset.target);
        if (!input) return;
        btn.addEventListener('click', () => {
            const show = input.type === 'password';
            input.type = show ? 'text' : 'password';
            btn.textContent = show ? 'Ascunde' : 'Arată';
            btn.setAttribute('aria-pressed', String(show));
            btn.setAttribute('aria-label', show ? 'Ascunde parola' : 'Arată parola');
            input.focus();
        });
        // Never submit (or let the browser remember) a revealed password field.
        if (input.form) input.form.addEventListener('submit', () => { input.type = 'password'; });
    });

    // ── Strength meter ──────────────────────────────────────────
    const LABELS = ['minimum 10 caractere', 'slabă', 'acceptabilă', 'bună', 'puternică'];

    function strength(pw) {
        if (pw.length < 10) return 0;
        let classes = 0;
        if (/[a-z]/.test(pw)) classes++;
        if (/[A-Z]/.test(pw)) classes++;
        if (/\d/.test(pw)) classes++;
        if (/[^A-Za-z0-9]/.test(pw)) classes++;
        let score = 1;
        if (classes >= 2) score++;
        if (pw.length >= 14 || classes >= 3) score++;
        if (pw.length >= 16 && classes >= 3) score++;
        if (new Set(pw).size < 6) score = 1;
        return Math.min(score, 4);
    }

    document.querySelectorAll('input[data-strength]').forEach(input => {
        const meter = document.getElementById(input.dataset.strength);
        if (!meter) return;
        const label = meter.querySelector('.pw-meter-label');
        input.addEventListener('input', () => {
            const level = input.value ? strength(input.value) : 0;
            meter.dataset.level = String(level);
            if (label) {
                label.textContent = input.value && level === 0
                    ? `încă ${10 - input.value.length} caractere`
                    : LABELS[level];
            }
        });
    });

    // ── Passwords match ─────────────────────────────────────────
    document.querySelectorAll('input[data-match]').forEach(confirm => {
        const source = document.getElementById(confirm.dataset.match);
        const hint = confirm.closest('.field').querySelector('.match-hint');
        if (!source || !hint) return;
        const check = () => { hint.hidden = !confirm.value || confirm.value === source.value; };
        confirm.addEventListener('input', check);
        source.addEventListener('input', check);
    });

    // ── Invite code: upper-case as you type ─────────────────────
    const invite = document.getElementById('invite');
    if (invite) {
        invite.addEventListener('input', () => {
            const pos = invite.selectionStart;
            invite.value = invite.value.toUpperCase();
            try { invite.setSelectionRange(pos, pos); } catch (_) { }
        });
    }

    // ── Share buttons (phones: native share sheet) ──────────────
    if (navigator.share) {
        document.querySelectorAll('[data-share]').forEach(btn => {
            const source = document.getElementById(btn.dataset.share);
            if (!source) return;
            btn.hidden = false;
            btn.addEventListener('click', () => {
                navigator.share({
                    title: btn.dataset.shareTitle || 'Invitație IamBusy',
                    text: btn.dataset.shareText || 'Creează-ți contul IamBusy:',
                    url: source.textContent.trim(),
                }).catch(() => { });
            });
        });
    }

    // ── Copy buttons ────────────────────────────────────────────
    document.querySelectorAll('[data-copy]').forEach(btn => {
        const source = document.getElementById(btn.dataset.copy);
        if (!source) return;
        btn.addEventListener('click', async () => {
            const original = btn.textContent;
            try {
                await navigator.clipboard.writeText(source.textContent.trim());
                btn.textContent = 'Copiat ✓';
            } catch (_) {
                const range = document.createRange();
                range.selectNodeContents(source);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
                btn.textContent = 'Selectat — Ctrl+C';
            }
            setTimeout(() => { btn.textContent = original; }, 1800);
        });
    });
})();
