// Apply the saved light / dark choice before first paint (dark is the default).
// An account's own colour theme comes from the server as <style id="user-theme">
// and wins over this; the browser bar is then tinted with its background.
(function () {
    try {
        var t = localStorage.getItem('iambusy-theme');
        if (t === 'light') document.documentElement.setAttribute('data-theme', 'light');
    } catch (_) { }
    document.addEventListener('DOMContentLoaded', function () {
        if (!document.getElementById('user-theme')) return;
        var paper = getComputedStyle(document.documentElement).getPropertyValue('--paper').trim();
        var meta = document.querySelector('meta[name="theme-color"]');
        if (meta && paper) meta.content = paper;
    });
})();
