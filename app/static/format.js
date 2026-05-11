// 한국 금액 표기 통일 — 콤마/소수점 혼합 방지
window.fmtKRW = function (won) {
    if (won == null) return '-';
    const n = Math.round(won);
    if (n >= 100000000) return (n / 100000000).toFixed(1).replace(/\.0$/, '') + '억';
    if (n >= 10000)     return Math.round(n / 10000).toLocaleString() + '만';
    return n.toLocaleString();
};

window.fmtKRWFull = function (won) {
    if (won == null) return '-';
    return Math.round(won).toLocaleString() + '원';
};

window.fmtScore = function (s) {
    if (s == null) return '-';
    const cls = s >= 70 ? 'bg-rose-100 text-rose-700' :
                s >= 40 ? 'bg-amber-100 text-amber-700' :
                          'bg-emerald-100 text-emerald-700';
    return `<span class="px-2 py-0.5 rounded text-xs font-semibold ${cls}">${Math.round(s)}</span>`;
};
