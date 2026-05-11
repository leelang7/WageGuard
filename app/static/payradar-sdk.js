/**
 * PayRadar RDP Detection SDK
 * --------------------------
 * 신청 페이지에 한 줄로 이식해 행동·환경 신호를 수집·점수화한다.
 * 정부 시스템 내재화 시 endpoint를 정부 서버로 변경. 외부로 나가는 신호 없음.
 *
 * Usage:
 *   <script src="/static/payradar-sdk.js"
 *           data-endpoint="/api/m6/probe"
 *           data-on-submit-form="#applyForm"
 *           data-threshold="40"></script>
 *
 * 또는 프로그램적:
 *   const pr = PayRadar.start({endpoint: '/api/m6/probe'});
 *   const result = await pr.probe();
 *   if (result.score >= 40) showStepUpAuth();
 */
(function (global) {
    'use strict';

    function readScriptConfig() {
        const cur = document.currentScript;
        if (!cur) return {};
        return {
            endpoint: cur.dataset.endpoint || '/api/m6/probe',
            threshold: parseInt(cur.dataset.threshold || '40'),
            onSubmitForm: cur.dataset.onSubmitForm || null,
            autoStart: cur.dataset.autoStart !== 'false',
            // L5 Phase 2.5 컨텍스트 — 페이지가 data-* 로 전달
            userHash: cur.dataset.userHash || '',
            prevBno: cur.dataset.prevBno || '',
            regionCode: cur.dataset.regionCode || '',
            trainingIpCountry: cur.dataset.trainingIpCountry || '',
            separationReason: cur.dataset.separationReason || '',
            insuranceLossDate: cur.dataset.insuranceLossDate || '',
            applyDate: cur.dataset.applyDate || '',
        };
    }

    const config = Object.assign(
        {
            endpoint: '/api/m6/probe', threshold: 40, onSubmitForm: null, autoStart: true,
            userHash: '', prevBno: '', regionCode: '',
            trainingIpCountry: '', separationReason: '',
            insuranceLossDate: '', applyDate: '',
        },
        readScriptConfig()
    );

    // ── 신호 수집 상태 ────────────────────────────────────────────
    const state = {
        mouseSamples: [],
        lastMove: null,
        keyDowns: {},
        keyHolds: [],
        lastKey: null,
        keyInter: [],
        pixelSkips: 0,
        pixelTotal: 0,
        startedAt: 0,
    };

    function attachListeners() {
        if (state.startedAt) return;
        state.startedAt = Date.now();
        window.addEventListener('mousemove', onMove, { passive: true });
        document.addEventListener('keydown', onKeyDown);
        document.addEventListener('keyup', onKeyUp);
    }

    function onMove(e) {
        const t = performance.now();
        if (state.lastMove) {
            const dx = e.clientX - state.lastMove.x;
            const dy = e.clientY - state.lastMove.y;
            const dt = t - state.lastMove.t;
            if (dt > 0 && dt < 200) {
                state.mouseSamples.push({ dx, dy, dt });
                if (state.mouseSamples.length > 600) state.mouseSamples.shift();
                state.pixelTotal++;
                if (Math.abs(dx) > 8 || Math.abs(dy) > 8) state.pixelSkips++;
            }
        }
        state.lastMove = { x: e.clientX, y: e.clientY, t };
    }
    function onKeyDown(e) {
        if (!state.keyDowns[e.code]) state.keyDowns[e.code] = performance.now();
    }
    function onKeyUp(e) {
        const t = performance.now();
        if (state.keyDowns[e.code]) {
            const hold = t - state.keyDowns[e.code];
            if (hold > 0 && hold < 5000) state.keyHolds.push(hold);
            delete state.keyDowns[e.code];
            if (state.lastKey) state.keyInter.push(t - state.lastKey);
            state.lastKey = t;
        }
    }

    function mouseStats() {
        if (state.mouseSamples.length < 30) return null;
        const dts = state.mouseSamples.map(s => s.dt);
        const mean = dts.reduce((a, b) => a + b, 0) / dts.length;
        const variance = dts.reduce((a, b) => a + (b - mean) ** 2, 0) / dts.length;
        const std = Math.sqrt(variance);
        return {
            n: dts.length,
            mean: round2(mean),
            std: round2(std),
            jitter: round3(std / mean),
            pixel_skip_ratio: state.pixelTotal ? round3(state.pixelSkips / state.pixelTotal) : 0,
        };
    }
    function keyStats() {
        if (state.keyHolds.length < 5) return null;
        const m = state.keyHolds.reduce((a, b) => a + b, 0) / state.keyHolds.length;
        const v = state.keyHolds.reduce((a, b) => a + (b - m) ** 2, 0) / state.keyHolds.length;
        const inter = state.keyInter.length ? state.keyInter.reduce((a, b) => a + b, 0) / state.keyInter.length : 0;
        return {
            n: state.keyHolds.length,
            mean_hold_ms: Math.round(m),
            std_hold_ms: Math.round(Math.sqrt(v)),
            mean_inter_key_ms: Math.round(inter),
        };
    }
    const round2 = v => Math.round(v * 100) / 100;
    const round3 = v => Math.round(v * 1000) / 1000;

    function getWebRTCIPs(timeout = 3000) {
        return new Promise(resolve => {
            try {
                const pc = new RTCPeerConnection({
                    iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
                });
                const ips = new Set();
                pc.createDataChannel('');
                pc.onicecandidate = e => {
                    if (!e.candidate) {
                        try { pc.close(); } catch (_) {}
                        return resolve([...ips]);
                    }
                    const m = (e.candidate.candidate || '').match(/\b(\d+\.\d+\.\d+\.\d+)\b/);
                    if (m) ips.add(m[1]);
                };
                pc.createOffer().then(o => pc.setLocalDescription(o)).catch(() => resolve([]));
                setTimeout(() => { try { pc.close(); } catch (_) {} resolve([...ips]); }, timeout);
            } catch (_) {
                resolve([]);
            }
        });
    }

    function getWebGL() {
        try {
            const c = document.createElement('canvas');
            const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
            if (!gl) return { vendor: '', renderer: '' };
            const dbg = gl.getExtension('WEBGL_debug_renderer_info');
            return {
                vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : '',
                renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : '',
            };
        } catch (_) { return { vendor: '', renderer: '' }; }
    }

    async function _canvasHash() {
        try {
            const cv = document.createElement('canvas');
            const ctx = cv.getContext('2d');
            ctx.textBaseline = 'top';
            ctx.font = '14px Arial';
            ctx.fillStyle = '#069';
            ctx.fillText('PayRadar probe fingerprint \u{1F50C}', 2, 2);
            const data = cv.toDataURL();
            const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(data));
            return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
        } catch (_) { return ''; }
    }

    async function probe(extra = {}) {
        attachListeners();
        const tz = (Intl.DateTimeFormat().resolvedOptions() || {}).timeZone || '';
        const tzOff = new Date().getTimezoneOffset();
        const screen = {
            w: window.screen.width, h: window.screen.height,
            depth: window.screen.colorDepth,
            pixel_ratio: window.devicePixelRatio || 1,
        };
        const [webrtc, gl, canvasHash] = await Promise.all([
            getWebRTCIPs(),
            Promise.resolve(getWebGL()),
            _canvasHash(),
        ]);

        const payload = Object.assign({
            timezone: tz,
            timezone_offset_min: tzOff,
            language: navigator.language || '',
            languages: Array.from(navigator.languages || []),
            user_agent: navigator.userAgent || '',
            platform: navigator.platform || '',
            screen,
            hardware_concurrency: navigator.hardwareConcurrency || 0,
            device_memory: navigator.deviceMemory || 0,
            webrtc_ips: webrtc,
            mouse_stats: mouseStats(),
            key_stats: keyStats(),
            webgl_vendor: gl.vendor,
            webgl_renderer: gl.renderer,
            canvas_hash: canvasHash,
            // L5 Phase 2.5 행정 컨텍스트 (page data-* → server L5 검사)
            user_hash: config.userHash,
            prev_company_bno: config.prevBno,
            region_code: config.regionCode,
            training_ip_country: config.trainingIpCountry,
            separation_reason: config.separationReason,
            insurance_loss_date: config.insuranceLossDate,
            apply_date: config.applyDate,
        }, extra);

        const res = await fetch(config.endpoint, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify(payload),
            credentials: 'same-origin',
        });
        const r = await res.json();
        return r;
    }

    // ── 자동 폼 가로채기 (선택) ─────────────────────────────────
    function attachFormGuard(selector) {
        const f = document.querySelector(selector);
        if (!f) return;
        f.addEventListener('submit', async e => {
            if (f.dataset.payradarPassed === '1') return; // 이미 통과
            e.preventDefault();
            const r = await probe();
            window.PayRadar._lastResult = r;
            const ev = new CustomEvent('payradar:result', { detail: r });
            document.dispatchEvent(ev);
            if (r.score >= config.threshold) {
                if (typeof window.PayRadarOnSuspicious === 'function') {
                    window.PayRadarOnSuspicious(r);
                } else {
                    alert(`⚠ 추가 본인확인이 필요합니다 (위험점수 ${r.score})\n사유: ${(r.factors || []).slice(0,3).map(x => x.label).join(' / ')}`);
                }
                return;
            }
            f.dataset.payradarPassed = '1';
            f.submit();
        });
    }

    // ── Public API ────────────────────────────────────────────────
    const PayRadar = {
        config,
        start(opts = {}) {
            Object.assign(config, opts);
            attachListeners();
            if (config.onSubmitForm) attachFormGuard(config.onSubmitForm);
            return PayRadar;
        },
        probe,
        getMouseStats: mouseStats,
        getKeyStats: keyStats,
        version: '0.2.0',
    };
    global.PayRadar = PayRadar;

    if (config.autoStart) {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => PayRadar.start());
        } else {
            PayRadar.start();
        }
    }
})(window);
