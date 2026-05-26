(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        applyTimeBasedGreeting();
        setupHighlightToggle();
        setupColorCopy();
        logWelcome();
    });

    function applyTimeBasedGreeting() {
        const el = document.getElementById("greeting");
        if (!el) return;

        const hour = new Date().getHours();
        let label = "HELLO, I'M";
        if (hour < 6) label = "LATE NIGHT, I'M";
        else if (hour < 12) label = "GOOD MORNING, I'M";
        else if (hour < 18) label = "GOOD AFTERNOON, I'M";
        else label = "GOOD EVENING, I'M";

        el.textContent = label;
    }

    function setupHighlightToggle() {
        const btn = document.getElementById("highlightBtn");
        const quote = document.getElementById("introQuote");
        if (!btn || !quote) return;

        btn.addEventListener("click", function () {
            const on = quote.classList.toggle("highlight");
            btn.textContent = on ? "강조 해제하기" : "한 줄 소개 강조하기";
        });
    }

    function setupColorCopy() {
        const swatch = document.getElementById("colorSwatch");
        const codeEl = document.getElementById("colorCode");
        if (!swatch || !codeEl) return;

        const color = swatch.dataset.color || "#87CEEB";
        const original = codeEl.textContent;

        swatch.addEventListener("click", function () {
            if (!navigator.clipboard) return;
            navigator.clipboard
                .writeText(color)
                .then(function () {
                    codeEl.textContent = "복사되었습니다 ✓";
                    setTimeout(function () {
                        codeEl.textContent = original;
                    }, 1500);
                })
                .catch(function () {
                    /* 조용히 무시 */
                });
        });
    }

    function logWelcome() {
        console.log(
            "%cSK %csquare",
            "color:#EA002C;font-size:20px;font-weight:800;",
            "color:#F26E22;font-size:20px;font-weight:500;"
        );
        console.log("Sean의 자기소개 페이지에 오신 것을 환영합니다.");
    }
})();
