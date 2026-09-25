/**
 * IamBusy — Client-side enhancements.
 *
 * Features:
 *  • Live clock that updates every second
 *  • Live countdown + progress bar for the current block
 *  • Auto-reload when the current block ends or the day rolls over
 *  • Auto-scroll to the current schedule block on load
 *  • Date navigation: native picker, ← / → keys, swipe, "T" for today
 */

(function () {
    'use strict';

    const pad = (n) => String(n).padStart(2, '0');
    const loadedOn = new Date().toDateString();

    // ── Live countdown ──────────────────────────────────────────
    const statusSub = document.getElementById('status-sub');
    const endTime = statusSub && statusSub.dataset.end ? new Date(statusSub.dataset.end) : null;
    // 23:59 is the end-of-day sentinel; treat it as midnight.
    if (endTime && endTime.getHours() === 23 && endTime.getMinutes() === 59) {
        endTime.setMinutes(60);
    }

    function formatDuration(totalMinutes) {
        const h = Math.floor(totalMinutes / 60);
        const m = totalMinutes % 60;
        if (h && m) return `${h}h ${m}min`;
        if (h) return `${h}h`;
        return `${m} min`;
    }

    // ── Progress bar on the current block ───────────────────────
    const currentBlock = document.querySelector('.schedule-block[data-current="true"]');
    const progressFill = currentBlock && currentBlock.querySelector('.progress-fill');
    const blockStart = currentBlock ? new Date(currentBlock.dataset.start) : null;
    const blockEnd = currentBlock ? new Date(currentBlock.dataset.end) : null;

    // ── Tick ────────────────────────────────────────────────────
    const clockEl = document.getElementById('current-time');

    function tick() {
        const now = new Date();
        if (clockEl) {
            clockEl.textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
        }

        if (endTime) {
            const minutesLeft = Math.ceil((endTime - now) / 60000);
            if (minutesLeft <= 0) {
                window.location.reload();
                return;
            }
            statusSub.textContent = `Mai sunt ${formatDuration(minutesLeft)}.`;
        }

        if (progressFill && blockStart && blockEnd) {
            const pct = Math.min(100, Math.max(0, ((now - blockStart) / (blockEnd - blockStart)) * 100));
            progressFill.style.width = `${pct}%`;
        }

        // Day rolled over while the tab stayed open → refresh "today".
        if (now.toDateString() !== loadedOn && !window.location.search.includes('date=')) {
            window.location.reload();
        }
    }

    tick();
    setInterval(tick, 1000);

    // Tabs in background may throttle timers — resync when shown again.
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) tick();
    });


    // ── Auto-Scroll to Current Block ────────────────────────────
    if (currentBlock) {
        setTimeout(() => {
            const rect = currentBlock.getBoundingClientRect();
            if (rect.top < 0 || rect.bottom > window.innerHeight) {
                currentBlock.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }, 350);
    }


    // ── Date Picker ─────────────────────────────────────────────
    // The <input type="date"> is hidden; showPicker() opens it when
    // the visible date button is pressed.
    const datePicker = document.getElementById('date-picker');
    const dateDisplay = document.getElementById('date-display');

    if (datePicker && dateDisplay) {
        dateDisplay.addEventListener('click', function () {
            try {
                datePicker.showPicker();
            } catch (_) {
                datePicker.focus();
                datePicker.click();
            }
        });

        datePicker.addEventListener('change', function (e) {
            if (e.target.value) {
                window.location.href = '?date=' + e.target.value;
            }
        });
    }


    // ── Keyboard navigation ─────────────────────────────────────
    const prevLink = document.getElementById('nav-prev');
    const nextLink = document.getElementById('nav-next');

    document.addEventListener('keydown', (e) => {
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        if (e.target.closest('input, textarea, select')) return;
        if (e.key === 'ArrowLeft' && prevLink) prevLink.click();
        else if (e.key === 'ArrowRight' && nextLink) nextLink.click();
        else if (e.key === 't' || e.key === 'T') window.location.href = '/';
    });


    // ── Swipe navigation (touch) ────────────────────────────────
    let touchX = null;
    let touchY = null;

    document.addEventListener('touchstart', (e) => {
        if (e.touches.length !== 1) return;
        touchX = e.touches[0].clientX;
        touchY = e.touches[0].clientY;
    }, { passive: true });

    document.addEventListener('touchend', (e) => {
        if (touchX === null) return;
        const dx = e.changedTouches[0].clientX - touchX;
        const dy = e.changedTouches[0].clientY - touchY;
        touchX = touchY = null;
        // Mostly-horizontal swipe of at least 70px
        if (Math.abs(dx) < 70 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
        if (dx > 0 && prevLink) prevLink.click();
        else if (dx < 0 && nextLink) nextLink.click();
    }, { passive: true });
})();
