/**
 * IamBusy — small helpers for the social pages (no inline scripts: CSP).
 *
 *  • [data-show-if="name=value"] blocks appear only while that radio is picked
 *  • <select data-navigate> jumps to the selected option's URL
 *  • [data-day-picker] builds a comma-separated list of dates from a date input
 */

(function () {
    'use strict';

    // ── Conditional fields ──────────────────────────────────────
    const conditional = [...document.querySelectorAll('[data-show-if]')];
    function refresh() {
        conditional.forEach(block => {
            const [name, value] = block.dataset.showIf.split('=');
            const picked = document.querySelector(`input[name="${name}"]:checked`);
            const on = !!picked && picked.value === value;
            block.hidden = !on;
            block.querySelectorAll('input, select, textarea').forEach(i => { i.disabled = !on; });
        });
    }
    if (conditional.length) {
        document.addEventListener('change', (e) => { if (e.target.type === 'radio') refresh(); });
        refresh();
    }

    // ── Navigate on select ──────────────────────────────────────
    document.querySelectorAll('select[data-navigate]').forEach(sel => {
        sel.addEventListener('change', () => { if (sel.value) window.location.href = sel.value; });
    });

    // ── Day picker → comma-separated list ───────────────────────
    document.querySelectorAll('[data-day-picker]').forEach(picker => {
        const target = document.getElementById(picker.dataset.dayPicker);
        const input = picker.querySelector('[data-day-input]');
        const add = picker.querySelector('[data-day-add]');
        const chips = picker.querySelector('[data-day-chips]');
        if (!target || !input || !add || !chips) return;

        const read = () => target.value.split(/[\s,;]+/).filter(Boolean);
        function render() {
            chips.innerHTML = '';
            [...new Set(read())].sort().forEach(day => {
                const chip = document.createElement('button');
                chip.type = 'button';
                chip.className = 'chip chip-x';
                chip.textContent = day;
                chip.setAttribute('aria-label', `Scoate ${day}`);
                chip.addEventListener('click', () => {
                    target.value = read().filter(d => d !== day).join(', ');
                    render();
                });
                chips.appendChild(chip);
            });
        }
        add.addEventListener('click', () => {
            if (!input.value) return;
            target.value = [...new Set([...read(), input.value])].sort().join(', ');
            input.value = '';
            render();
        });
        target.addEventListener('change', render);
        render();
    });
})();
