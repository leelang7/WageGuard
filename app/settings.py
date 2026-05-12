from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
# 키가 .env.example에만 채워져 있는 경우에도 누락분 보충 (override=False)
load_dotenv(ROOT / ".env.example", override=False)

# dotenv 값 앞뒤 공백/탭 제거. 공공데이터 키에 탭이 섞이면 인증이 실패한다.
for _key, _value in list(os.environ.items()):
    if _key.endswith("_KEY") or _key.endswith("_SECRET") or _key.startswith("WORK24_AUTH_KEY"):
        os.environ[_key] = _value.strip()

# 일부 키는 과거 단일 이름으로 저장되어 있어 앱 내부의 세부 키 이름으로 보강한다.
if os.getenv("WORKNET_KEY"):
    for _name in (
        "WORK24_AUTH_KEY_JOB",
        "WORK24_AUTH_KEY_DUTY",
        "WORK24_AUTH_KEY_TRAINING",
        "WORK24_AUTH_KEY_CAREER",
    ):
        os.environ.setdefault(_name, os.getenv("WORKNET_KEY", ""))

DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "wageguard.sqlite"

CASE_FILES_DIR = DATA_DIR / "case_files"
CASE_FILES_DIR.mkdir(exist_ok=True)
ALLOWED_FILE_EXTS = {".jpg", ".jpeg", ".png", ".pdf", ".heic", ".webp", ".gif"}
MAX_FILE_BYTES = 8 * 1024 * 1024   # 8MB / 파일

SAMPLES = ROOT / "samples"
TEMPLATES = Path(__file__).parent / "templates"
STATIC = Path(__file__).parent / "static"

APP_NAME = "WageGuard"
APP_TAGLINE = "임금체불 사전 차단 + 부정수급 실시간 탐지 AI"
APP_OPERATOR = "한국장애인고용공단·한국고용정보원 운영(안)"
APP_USERS = "근로감독관 · KEAD 점검관 · 사업주 · 장애인 근로자"
