/**
 * IamBusy — Client-side enhancements.
 *
 * Features:
 *  • Live clock that updates every second
 *  • Auto-scroll to the current schedule block on load
 *  • Native date-picker navigation (change event only)
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


    // ── Date Picker ─────────────────────────────────────────────
    // The <input type="date"> is hidden. We open it programmatically
    // via showPicker() when the user clicks/taps the visible date
    // display area. This is far more reliable than the opacity-0
    // overlay approach which silently fails in many browsers.
    const datePicker = document.getElementById('date-picker');
    const dateDisplay = document.getElementById('date-display');

    if (datePicker && dateDisplay) {
        // Open native picker when date area is clicked
        dateDisplay.addEventListener('click', function () {
            try {
                datePicker.showPicker();
            } catch (_) {
                // Fallback for browsers without showPicker()
                datePicker.focus();
                datePicker.click();
            }
        });

        // Navigate when a date is selected
        datePicker.addEventListener('change', function (e) {
            if (e.target.value) {
                window.location.href = '?date=' + e.target.value;
            }
        });
    }
})();
