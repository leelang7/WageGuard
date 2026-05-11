/* 한국 17 광역시도 타일맵 — 지도 한반도 위치를 격자로 근사. GeoJSON 없이 즉시 동작. */
(function (global) {
    'use strict';

    // [row, col] (row 0 = 위쪽), label
    const TILES = {
        "서울": { r: 1, c: 4 }, "인천": { r: 1, c: 3 }, "경기": { r: 2, c: 4 },
        "강원": { r: 1, c: 6 }, "충북": { r: 3, c: 5 }, "충남": { r: 3, c: 3 },
        "대전": { r: 3, c: 4 }, "세종": { r: 3, c: 4, half: true },
        "전북": { r: 4, c: 3 }, "전남": { r: 5, c: 3 }, "광주": { r: 5, c: 2 },
        "경북": { r: 4, c: 5 }, "대구": { r: 4, c: 5, half: true },
        "경남": { r: 5, c: 5 }, "부산": { r: 5, c: 6 }, "울산": { r: 4, c: 6 },
        "제주": { r: 6, c: 2 },
    };

    function colorFor(v, max) {
        if (!v || max <= 0) return '#f1f5f9';
        const t = Math.min(1, v / max);
        if (t > 0.66) return '#dc2626';   // 빨강
        if (t > 0.33) return '#f59e0b';   // 주황
        if (t > 0.10) return '#3b82f6';   // 파랑
        return '#94a3b8';
    }

    function render(elId, data, opts) {
        opts = opts || {};
        const el = document.getElementById(elId);
        if (!el) return;
        const map = {};
        for (const r of (data || [])) map[r.region] = r;
        const max = Math.max(0, ...Object.values(map).map(r => r.value || r.count || 0));

        // 6 행 7 열 그리드. 세종은 대전과 같은 셀의 반쪽으로 표시.
        const ROWS = 6, COLS = 7;
        let html = `<div class="korea-tilemap" style="display:grid;grid-template-columns:repeat(${COLS},1fr);grid-template-rows:repeat(${ROWS},1fr);gap:6px;aspect-ratio:1.05;">`;

        // 상단 별도 셀: 서울/인천/강원 한 줄
        // 일반 셀
        for (const [name, pos] of Object.entries(TILES)) {
            const row = pos.r;
            const col = pos.c;
            const v = (map[name] || {}).count || 0;
            const bg = colorFor(v, max);
            const fg = v > 0 ? 'white' : '#475569';
            const subtle = v === 0 ? 'opacity:.55;' : '';
            const half = pos.half ? 'border:2px dashed white;' : '';
            html += `
                <div title="${name} ${v}건" data-region="${name}"
                     class="tile" onclick="(${onClick.toString()})('${name}')"
                     style="grid-row:${row + 1};grid-column:${col};
                            background:${bg};color:${fg};
                            border-radius:8px;display:flex;flex-direction:column;
                            align-items:center;justify-content:center;
                            font-size:11px;font-weight:600;cursor:pointer;
                            transition:transform .12s;${subtle}${half}"
                     onmouseover="this.style.transform='scale(1.08)'"
                     onmouseout="this.style.transform='scale(1)'">
                    <div>${name}</div>
                    <div style="font-size:14px;margin-top:2px;font-variant-numeric:tabular-nums">${v.toLocaleString()}</div>
                </div>`;
        }
        html += '</div>';

        // 범례
        html += `<div style="display:flex;gap:8px;font-size:11px;margin-top:8px;color:#64748b;">
            <span>🟦 저</span><span>🟧 중</span><span>🟥 고</span>
            <span style="margin-left:auto">최대 ${max} 건</span>
        </div>`;

        el.innerHTML = html;

        function onClick(name) {
            if (opts.onClick) opts.onClick(name);
            else location.href = `/region`;
        }
    }

    global.KoreaTileMap = { render };
})(window);
