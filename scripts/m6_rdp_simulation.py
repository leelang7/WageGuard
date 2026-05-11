"""
M6: 원격접속(RDP) 기반 부정수급 시뮬레이션
Phase 1  : 브라우저/네트워크 신호 (L1~L4)
Phase 2.5: + 고용노동부 행정 신호 5종 (출입국 MOU 없이 즉시 구현 가능)
           L5-E 체불명단, L5-D 이전신청이력, L5-F 상실신고역순,
           L5-C 훈련IP, L5-A EIS실업급여급증
Phase 3  : + 출입국 기록 (정책 협의)
"""

from __future__ import annotations

import csv
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from common import ROOT, save_sample

random.seed(42)

# 시그널 임계값(데모용 — 운영시 학습)
TH_MOUSE_JITTER_MS = 70
TH_KEY_BURST_RATIO = 1.8
TH_DEVICE_DRIFT = 0.6
TH_RDP_LATENCY_MS = 60


@dataclass
class Application:
    apply_id: str
    user_hash: str
    apply_ts: str
    ip: str
    ip_country: str           # KR / others
    device_fp: str
    device_drift: float       # 0~1: 평소 디바이스와의 차이
    mouse_jitter_ms: float    # ms (RDP일수록 큼)
    key_burst_ratio: float    # RDP는 burst 비율 ↑
    rdp_latency_ms: float     # RDP 특유 ping
    immig_overseas: bool      # ground truth: 출입국 데이터로 본 해외 체류 여부
    # ── Phase 2.5: 고용노동부 행정 신호 (신규) ──────────────────
    prev_in_defaulter: bool = False   # L5-E: 이전 사업장 체불명단 등재
    separation_voluntary: bool = False # L5-E: 자의퇴직 주장 (체불사업장인데)
    device_changed: bool = False      # L5-D: 이전 신청 대비 디바이스 변경
    tz_changed: bool = False          # L5-D: 이전 신청 대비 timezone 변경
    apply_count_6m: int = 0           # L5-D: 6개월 내 반복 신청 횟수
    loss_retroactive_days: int = 0    # L5-F: 상실신고 소급일수 (음수=역순)
    training_ip_overseas: bool = False # L5-C: 직업훈련 해외 IP
    region_surge_pct: float = 0.0    # L5-A: 지역 실업급여 급증률
    label: int = 0                    # 0=정상, 1=부정


def gen_normal(i: int) -> Application:
    return Application(
        apply_id=f"A{i:06d}",
        user_hash=f"U{random.randint(1, 9999):04d}",
        apply_ts=f"2026-04-{random.randint(1,30):02d}T{random.randint(9,18):02d}:00:00+09:00",
        ip=f"211.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(0,255)}",
        ip_country="KR",
        device_fp=f"FP_{random.randint(1, 99999):05d}",
        device_drift=random.gauss(0.05, 0.05),
        mouse_jitter_ms=max(5, random.gauss(20, 10)),
        key_burst_ratio=max(0.8, random.gauss(1.0, 0.2)),
        rdp_latency_ms=max(0, random.gauss(5, 5)),
        immig_overseas=False,
        # Phase 2.5 정상 분포
        prev_in_defaulter=random.random() < 0.03,   # 정상 신청자도 3%는 체불사업장 출신
        separation_voluntary=random.random() < 0.15,
        device_changed=random.random() < 0.05,
        tz_changed=False,
        apply_count_6m=random.choices([0, 1, 2], weights=[80, 15, 5])[0],
        loss_retroactive_days=random.randint(-3, 3),  # ±3일은 정상
        training_ip_overseas=False,
        region_surge_pct=random.gauss(0, 10),
        label=0,
    )


def gen_fraud_direct_overseas(i: int) -> Application:
    """해외에서 직접 신청 — 단순 케이스"""
    a = gen_normal(i)
    overseas_ip_prefixes = ["123.45.", "203.0.", "104.16.", "172.217.", "8.8."]
    a.ip = random.choice(overseas_ip_prefixes) + f"{random.randint(0,255)}.{random.randint(0,255)}"
    a.ip_country = "OVERSEAS"
    a.immig_overseas = True
    a.training_ip_overseas = True         # 해외 체류 중 훈련도 해외 IP
    a.label = 1
    return a


def gen_fraud_rdp(i: int) -> Application:
    """해외에서 RDP로 한국 본가 PC 접속 → IP는 KR이지만 행동 시그널 이상.
    부정 케이스 안에서도 이상 정도가 다양하도록 살짝 변동.
    """
    a = gen_normal(i)
    severity = random.uniform(0.4, 1.0)               # 능숙한 우회자일수록 ↓
    a.mouse_jitter_ms = max(10, random.gauss(60 + 80 * severity, 25))
    a.key_burst_ratio = max(1.0, random.gauss(1.4 + 1.2 * severity, 0.3))
    a.rdp_latency_ms = max(0, random.gauss(40 + 120 * severity, 30))
    a.device_drift = random.gauss(0.4 + 0.4 * severity, 0.15)
    a.immig_overseas = True
    # Phase 2.5 부정 행정 신호
    a.device_changed = random.random() < 0.55          # RDP는 다른 디바이스에서 접속
    a.tz_changed = random.random() < 0.45              # 해외 timezone
    a.training_ip_overseas = random.random() < 0.50   # 훈련도 해외에서
    a.loss_retroactive_days = random.randint(-30, -8) # 상실신고 역순 처리
    a.apply_count_6m = random.choices([1, 2, 3, 4], weights=[30, 30, 25, 15])[0]
    # 체불사업장 자의퇴직 위장 (부정 케이스의 40%)
    if random.random() < 0.40:
        a.prev_in_defaulter = True
        a.separation_voluntary = True
    a.label = 1
    return a


def score(a: Application, phase: int = 1) -> tuple[int, list[str]]:
    """다중 신호 결합 → 0~100 위험점수 + 적발 사유.
    phase=1  : 브라우저/네트워크 신호만 (L1~L4)
    phase=25 : + 고용노동부 행정 신호 5종 (L5, 출입국 불필요)
    phase=3  : + 출입국 기록 (정책 협의)
    """
    pts = 0
    why: list[str] = []

    # ── L1~L4: 브라우저/네트워크 ──────────────────────────────────
    if a.ip_country != "KR":
        pts += 60; why.append("ip_overseas")
    if a.mouse_jitter_ms > TH_MOUSE_JITTER_MS:
        pts += 15; why.append(f"mouse_jitter={a.mouse_jitter_ms:.0f}ms")
    if a.key_burst_ratio > TH_KEY_BURST_RATIO:
        pts += 10; why.append(f"key_burst={a.key_burst_ratio:.2f}")
    if a.rdp_latency_ms > TH_RDP_LATENCY_MS:
        pts += 15; why.append(f"rdp_latency={a.rdp_latency_ms:.0f}ms")
    if a.device_drift > TH_DEVICE_DRIFT:
        pts += 10; why.append(f"device_drift={a.device_drift:.2f}")

    # ── Phase 2.5: 고용노동부 행정 신호 ──────────────────────────
    if phase >= 25:
        # L5-E: 체불명단 등재 사업장 자의퇴직 위장
        if a.prev_in_defaulter and a.separation_voluntary:
            pts += 35; why.append("L5E:체불사업장_자의퇴직위장")
        elif a.prev_in_defaulter:
            pts += 20; why.append("L5E:체불사업장이직")

        # L5-D: 이전 신청 대비 디바이스/timezone 변경
        if a.device_changed and a.tz_changed:
            pts += 22; why.append("L5D:device+tz_변경")
        elif a.device_changed:
            pts += 18; why.append("L5D:device_변경")
        elif a.tz_changed:
            pts += 22; why.append("L5D:tz_변경")
        if a.apply_count_6m >= 3:
            pts += 12; why.append(f"L5D:반복신청_{a.apply_count_6m}회")

        # L5-F: 상실신고 역순 (소급 처리)
        if a.loss_retroactive_days < -14:
            pts += 25; why.append(f"L5F:상실소급_{abs(a.loss_retroactive_days)}일")
        elif a.loss_retroactive_days < -7:
            pts += 10; why.append(f"L5F:상실지연_{abs(a.loss_retroactive_days)}일")

        # L5-C: 직업훈련 해외 IP
        if a.training_ip_overseas:
            pts += 30; why.append("L5C:훈련_해외IP")

        # L5-A: 지역 실업급여 급증
        if a.region_surge_pct > 40:
            pts += 8; why.append(f"L5A:지역급증_{a.region_surge_pct:.0f}%")

    # ── Phase 3: 출입국 기록 ─────────────────────────────────────
    if phase >= 3 and a.immig_overseas:
        pts += 30; why.append("L5B:immig_overseas")

    return min(pts, 100), why


def main() -> None:
    samples: list[Application] = []
    for i in range(900):
        samples.append(gen_normal(i))
    for i in range(900, 970):
        samples.append(gen_fraud_rdp(i))             # 7% RDP 우회
    for i in range(970, 1000):
        samples.append(gen_fraud_direct_overseas(i)) # 3% 해외 직접

    random.shuffle(samples)
    n = len(samples)
    pos = sum(1 for a in samples if a.label == 1)

    THRESHOLD = 50
    print(f"\n■ M6 RDP 부정수급 시뮬레이션")
    print(f"  표본: {n}건 (정상 {n-pos} + 부정 {pos}), 임계 risk_score >= {THRESHOLD}\n")

    phase_labels = {
        1:  "Phase 1  (브라우저/네트워크 신호만)",
        25: "Phase 2.5 (+ 고용노동부 행정 신호 5종)",
        3:  "Phase 3  (+ 출입국 기록)",
    }

    rows_p25: list[dict] = []
    for phase in (1, 25, 3):
        tp = fp = tn = fn = 0
        rows: list[dict] = []
        for a in samples:
            s, why = score(a, phase=phase)
            pred = 1 if s >= THRESHOLD else 0
            if pred == 1 and a.label == 1: tp += 1
            elif pred == 1 and a.label == 0: fp += 1
            elif pred == 0 and a.label == 0: tn += 1
            else: fn += 1
            col = "pred_p25" if phase == 25 else "pred"
            rows.append({**asdict(a), "risk_score": s, "reasons": "|".join(why), col: pred})

        precision = tp / max(tp + fp, 1)
        recall = tp / max(pos, 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-9)

        print(f"  ── {phase_labels[phase]} ──")
        print(f"  TP={tp:>3}  FP={fp:>3}  TN={tn:>3}  FN={fn:>3}")
        print(f"  Precision={precision:.3f}  Recall={recall:.3f}  F1={f1:.3f}\n")

        if phase == 25:
            rows_p25 = rows

    # Phase 2.5 결과를 CSV로
    out25 = ROOT / "samples" / "m6_phase25_simulation.csv"
    out25.parent.mkdir(parents=True, exist_ok=True)
    fields = [*asdict(samples[0]).keys(), "risk_score", "reasons", "pred_p25"]
    with out25.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows_p25:
            r["mouse_jitter_ms"] = round(r["mouse_jitter_ms"], 1)
            r["key_burst_ratio"] = round(r["key_burst_ratio"], 2)
            r["rdp_latency_ms"] = round(r["rdp_latency_ms"], 1)
            r["device_drift"] = round(r["device_drift"], 3)
            w.writerow(r)
    print(f"[+] 저장: {out25.relative_to(ROOT)} (Phase 2.5 결과)")


if __name__ == "__main__":
    main()
