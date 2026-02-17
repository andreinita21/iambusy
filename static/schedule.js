/**
 * IamBusy — Client-side enhancements.
 *
 * Features:
 *  • Live clock that updates every second
 *  • Auto-scroll to the current schedule block on load
 *  • Date-picker integration (click the date header)
 */

(function () {
    'use strict';

    // ── Live Clock ──────────────────────────────────────────────
    const clockEl = document.getElementById('current-time');

    function updateClock() {
        const now = new Date();
        const hours = String(now.getHours()).padStart(2, '0');
        const minutes = String(now.getMinutes()).padStart(2, '0');
        const seconds = String(now.getSeconds()).padStart(2, '0');
        if (clockEl) {
            clockEl.textContent = `${hours}:${minutes}:${seconds}`;
        }
    }

    // Update immediately, then every second
    updateClock();
    setInterval(updateClock, 1000);


    // ── Auto-Scroll to Current Block ────────────────────────────
    function scrollToCurrentBlock() {
        const currentBlock = document.querySelector('.schedule-block[data-current="true"]');
        if (currentBlock) {
            // Small delay so CSS animations finish
            setTimeout(() => {
                currentBlock.scrollIntoView({
                    behavior: 'smooth',
                    block: 'center',
                });
            }, 350);
        }
    }

    scrollToCurrentBlock();


    // ── Date Picker Integration ─────────────────────────────────
    const datePicker = document.getElementById('date-picker');
    const dateHeader = document.getElementById('date-header');

    if (datePicker && dateHeader) {
        // Navigate on date change
        datePicker.addEventListener('change', function (e) {
            if (e.target.value) {
                window.location.href = '?date=' + e.target.value;
            }
        });

        // Open picker on click / keyboard
        function openDatePicker() {
            try {
                if (typeof datePicker.showPicker === 'function') {
                    datePicker.showPicker();
                } else {
                    datePicker.style.pointerEvents = 'auto';
                    datePicker.focus();
                    datePicker.click();
                    datePicker.style.pointerEvents = 'none';
                }
            } catch (err) {
                console.warn('[IamBusy] Date picker not available:', err.message);
            }
        }

        dateHeader.addEventListener('click', openDatePicker);
        dateHeader.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                openDatePicker();
            }
        });
    }
})();
