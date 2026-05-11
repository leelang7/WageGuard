"""
키가 채워진 .env에서 모든 API 테스트를 일괄 실행한다.
- 각 테스트는 독립 실행. 실패해도 다음 테스트는 진행.
- 실행 결과 요약을 마지막에 출력.
"""

from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

TESTS = [
    "test_eis",            # 인증키 불필요
    "fetch_defaulter_list",  # 인증 불필요
    "test_nts_status",     # data.go.kr 키
    "test_kcomwel_api",    # data.go.kr 키
    "test_opendart",       # OpenDART 키 (대기 중이면 SKIP 예상)
    "download_kcomwel_file",
]


def main() -> None:
    results: list[tuple[str, str]] = []
    for name in TESTS:
        print("\n" + "=" * 70)
        print(f"▶ {name}")
        print("=" * 70)
        try:
            mod = importlib.import_module(name)
            mod.main()
            results.append((name, "ok"))
        except SystemExit as e:
            results.append((name, f"skipped (exit={e.code})"))
        except Exception as e:
            traceback.print_exc()
            results.append((name, f"FAIL: {type(e).__name__}: {e}"))

    print("\n" + "=" * 70)
    print("요약")
    print("=" * 70)
    for name, status in results:
        mark = "✔" if status == "ok" else ("·" if status.startswith("skipped") else "✘")
        print(f"  {mark}  {name:32s}  {status}")


if __name__ == "__main__":
    main()
