// Apply the saved theme before first paint (dark is the default).
(function () {
    try {
        var t = localStorage.getItem('iambusy-theme');
        if (t === 'light') document.documentElement.setAttribute('data-theme', 'light');
    } catch (_) { }
})();
