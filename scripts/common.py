from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
SAMPLES.mkdir(exist_ok=True)

load_dotenv(ROOT / ".env")


def need(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        print(f"[!] .env에 {key} 가 비어 있습니다. 채워주세요.", file=sys.stderr)
        sys.exit(2)
    return val


def save_sample(name: str, payload, *, fmt: str = "json") -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SAMPLES / f"{name}_{ts}.{fmt}"
    if fmt == "json":
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    else:
        path.write_text(str(payload), encoding="utf-8")
    print(f"[+] saved → {path.relative_to(ROOT)}")
    return path


def head(s: str, n: int = 600) -> str:
    s = s if isinstance(s, str) else str(s)
    return s if len(s) <= n else s[:n] + f"… (+{len(s) - n} chars)"


_SECRET_PARAM_KEYS = (
    "serviceKey",
    "ServiceKey",
    "authKey",
    "apiKey",
    "crtfc_key",
    "access_key",
    "key",
)


def redact_url(url: str) -> str:
    """쿼리 파라미터 중 인증/키 류만 *** 로 가린다."""
    import re

    def _sub(m):
        name = m.group(1)
        return f"{name}=***REDACTED***"

    pattern = "(" + "|".join(_SECRET_PARAM_KEYS) + r")=[^&\s]+"
    return re.sub(pattern, _sub, url)
