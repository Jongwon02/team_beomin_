"""
작물 5종 가중치·근거값 참고 데이터
출처: 작물5종_가중치표_최종.md (2026-07-23)

이 파일이 온도·강수·일조·EC 등 근거값의 유일한 출처(single source of truth)입니다.
reading_guard.py(방어함수)와 scoring_engine.py(스코어링)는 둘 다 이 파일의 상수를
가져다 쓰며, 별도로 값을 복제해두지 않습니다 — 복제하면 한쪽만 수정됐을 때
서로 다른 기준으로 판단하는 버그가 생기기 때문입니다(실제로 발견됐던 사례).
"""

# ── 1. 최종 가중치 매트릭스 (0~100, 행별 합=100) ──────────────────
WEIGHT_MATRIX = {
    "사과": {"온도": 38, "강수": 22, "일조": 18, "pH": 6, "유기물": 4, "유효인산": 4, "EC": 8},
    "배":   {"온도": 40, "강수": 22, "일조": 18, "pH": 5, "유기물": 3, "유효인산": 4, "EC": 8},
    "오이": {"온도": 30, "강수": 10, "일조": 25, "pH": 6, "유기물": 3, "유효인산": 4, "EC": 22},
    "감자": {"온도": 28, "강수": 15, "일조": 32, "pH": 7, "유기물": 3, "유효인산": 4, "EC": 11},
    "상추": {"온도": 35, "강수": 10, "일조": 25, "pH": 8, "유기물": 3, "유효인산": 3, "EC": 16},
}

VARIABLES = ["온도", "강수", "일조", "pH", "유기물", "유효인산", "EC"]

# 작물이 대표하는 흙토람 지목(land-use). soil.py가 API 조회에, reading_guard.py가
# 유효인산 물리범위 판정(아래 VALID_RANGES 참고)에 공통으로 쓴다 - 두 곳에 따로
# 정의해두면 한쪽만 바뀌었을 때 서로 다른 지목을 가리키는 버그가 생기기 때문에
# 여기 하나로 둔다.
LAND_USE_CATEGORY = {
    "사과": "Fruit", "배": "Fruit",
    "오이": "Fachs",
    "감자": "Pfld", "상추": "Pfld",
}

# 물리적 유효범위 - near/위험값보다 훨씬 넉넉한 "말이 안 되는 값 거르기"용 (방어함수에서 사용)
# 유효인산만 지목(land-use)별 dict다 - 흙토람 자체가 지목별로 구간 스케일을 다르게
# 잡아뒀기 때문(논/밭/과수원 최상위 구간 "601 이상" vs 시설(Fachs) "2001 이상" - 시설
# 재배지의 인산 축적이 노지보다 훨씬 심하다는 걸 흙토람이 전제하고 있다는 뜻). 공통
# 상한(2000)을 그대로 쓰면 시설재배 작물(오이)에서 흙토람 자체 정상 최상위구간 값까지
# "물리적 이상치"로 과다플래그됐다(실측 63.8%, 전부 진짜 이상값 아님 - 2026-07-24 진단,
# soil.py의 개방구간 근사대표값 2201이 상한 2000을 근소하게 넘겨서 생긴 문제였다).
# 논/밭/과수원은 흙토람 구간 최댓값(Pfld/Fruit 651, Rfld 276)이 2000에 한참 못 미쳐
# 애초에 과다플래그가 날 수 없으므로 그대로 둔다 - get_valid_range() 참고.
VALID_RANGES = {
    "온도": (-40, 50),
    "강수": (0, 3000),
    "일조": (0, 2000),
    "pH": (0, 14),
    "유기물": (0, 100),
    "유효인산": {
        "Rfld": (0, 2000), "Pfld": (0, 2000), "Fruit": (0, 2000),
        "Fachs": (0, 2500),  # 흙토람 시설 최상위구간 근사대표값(soil.py 2201.0)보다 여유있게
    },
    "EC": (0, 20),
}


def get_valid_range(var, land_use_category=None):
    """VALID_RANGES[var]를 반환한다. 유효인산은 지목별 dict라 land_use_category가
    필요하고(LAND_USE_CATEGORY 참고), 나머지 변수는 지목 무관한 튜플을 그대로 반환한다."""
    entry = VALID_RANGES[var]
    if isinstance(entry, dict):
        return entry[land_use_category]
    return entry


# pH 이상치 탐지용 "물리적 상식범위" - 아래 PH_THRESHOLDS(작물별 적정범위, 스코어링
# 전용)와는 목적이 다르다. PH_THRESHOLDS는 폭이 0.5로 좁아서(예: 오이 6.0~6.5),
# 이걸 이상치 탐지에 그대로 재사용하면 정상적인 한국 토양 pH(보통 5~7대)조차
# "최적이 아니다"는 이유로 과다플래그됐다(실측 배 54.8%, 오이 93.2%, 오이는 중앙값
# 6.95조차 범위 밖 - 2026-07-24 진단). 그래서 이상치 탐지는 이 훨씬 넓은 물리적
# 상식범위(4.0~9.0, 이 밖은 한국 토양에서 실제로 드물다)로 별도 판정한다.
PH_PHYSICAL_RANGE = (4.0, 9.0)

# 작물별로 허용되는 재배형태 목록. None이면 재배형태 구분이 없는 작물(사과·배).
# cultivation_type 파라미터는 이 목록에 있는 값만 허용하며, 필요한데 안 주면 예외를 낸다.
CULTIVATION_TYPES = {
    "사과": None,
    "배": None,
    "오이": ["촉성재배", "반촉성재배"],
    "감자": ["봄재배", "고랭지재배"],
    "상추": ["고랭지재배", "저지대재배"],
}


class MissingCultivationTypeError(ValueError):
    """재배형태 구분이 필요한 작물인데 cultivation_type을 안 줬을 때 발생."""


class InvalidCultivationTypeError(ValueError):
    """그 작물에 존재하지 않는 재배형태를 줬을 때 발생."""


def resolve_cultivation_type(crop, cultivation_type):
    """
    cultivation_type을 검증하고 실제로 사용할 값을 반환한다.
    - 그 작물이 재배형태 구분이 없으면(사과·배) None을 그대로 반환.
    - 재배형태 구분이 있는 작물인데 cultivation_type이 없으면 예외.
    - 목록에 없는 값이면 예외.
    이 함수를 guard_readings와 score_crop이 공통으로 거치게 해서,
    두 함수가 서로 다른 재배형태를 쓰는 일이 구조적으로 불가능하게 만든다.
    """
    valid_types = CULTIVATION_TYPES.get(crop)
    if valid_types is None:
        return None
    if cultivation_type is None:
        raise MissingCultivationTypeError(
            f"'{crop}'은(는) 재배형태 구분이 필요합니다. cultivation_type을 "
            f"{valid_types} 중 하나로 지정해주세요."
        )
    if cultivation_type not in valid_types:
        raise InvalidCultivationTypeError(
            f"'{crop}'에 존재하지 않는 재배형태입니다: '{cultivation_type}' "
            f"(가능한 값: {valid_types})"
        )
    return cultivation_type

# ── 2. 온도 near/위험값 (℃) — 냉해(하한)·폭염(상한) ──────────────
# 사과·배는 "온도×지속시간" 조합 규칙(방법 D)이라 형식이 다름 → 별도 처리
TEMP_THRESHOLDS_INSURANCE = {  # 사과·배: 공식 재해보험 기준
    "사과": {"cold_near": 0.0, "cold_duration_hr": 48, "heat_near": 33.0, "heat_duration_day": 2},
    "배":   {"cold_near": 0.0, "cold_duration_hr": 48, "heat_near": 33.0, "heat_duration_day": 2},
}

# 사과·배는 위험값(danger)이 공식 기준에 없어(cold_near/heat_near만 있음), near값에서
# 이 여유폭만큼 떨어진 지점을 근사 위험값으로 쓴다(scoring_engine.score_temperature,
# live_scoring._build_temperature_risk_signals 공용 - 각자 값을 따로 두지 않는다).
APPLE_PEAR_DANGER_APPROXIMATION_MARGIN = 5.0  # 사과·배 위험값 미확정 시 근사 여유폭(℃)

TEMP_THRESHOLDS = {  # 오이·감자·상추: 방법 C(관측소 연도별 변동폭)
    "오이": {
        "촉성재배":   {"cold_near": -6.85, "cold_danger": -8.37, "heat_near": 37.62, "heat_danger": 39.13},
        "반촉성재배": {"cold_near": -7.88, "cold_danger": -9.85, "heat_near": 36.73, "heat_danger": 38.15},
    },
    "감자": {
        "봄재배":   {"cold_near": -7.70, "cold_danger": -9.41, "heat_near": 36.97, "heat_danger": 39.11},
        "고랭지재배": {"cold_near": -17.75, "cold_danger": -21.06, "heat_near": 31.81, "heat_danger": 33.40},
    },
    "상추": {
        "고랭지재배": {"cold_near": -15.33, "cold_danger": -19.06, "heat_near": 35.33, "heat_danger": 37.76},
        "저지대재배": {"cold_near": -4.72, "cold_danger": -6.45, "heat_near": 36.37, "heat_danger": 37.57},
    },
}

# ── 3. 강수(가뭄) near/위험값 (mm, 생육기간 누적) — 적을수록 위험 ──
PRECIP_THRESHOLDS = {
    "사과": {"near": 750.5, "danger": 430.6},
    "배":   {"near": 791.9, "danger": 461.8},
    "오이": {
        "촉성재배":   {"near": 73.9, "danger": 0.0},
        "반촉성재배": {"near": 482.7, "danger": 247.4},
    },
    "감자": {
        "봄재배":   {"near": 256.0, "danger": 139.3},
        "고랭지재배": {"near": 651.7, "danger": 245.2},
    },
    "상추": {
        "고랭지재배": {"near": 379.3, "danger": 92.6},
        "저지대재배": {"near": 596.5, "danger": 301.3},
    },
}

# ── 4. 일조부족 near/위험값 (h, 생육기간 누적) — 적을수록 위험 ──────
SUNSHINE_THRESHOLDS = {
    "사과": {"near": 1080.2, "danger": 847.5},
    "배":   {"near": 1060.7, "danger": 809.3},
    "오이": {
        "촉성재배":   {"near": 522.6, "danger": 469.3},
        "반촉성재배": {"near": 999.2, "danger": 930.1},
    },
    "감자": {
        "봄재배":   {"near": 841.9, "danger": 774.5},
        "고랭지재배": {"near": 844.9, "danger": 673.6},
    },
    "상추": {
        "고랭지재배": {"near": 333.2, "danger": 185.8},
        "저지대재배": {"near": 938.5, "danger": 747.4},
    },
}

# ── 5. pH near값 (범위, 위험값 없음 — 이분법) ──────────────────────
# ⚠️ 스코어링(scoring_engine.score_ph) 전용 - 작물별 "적정범위"다. reading_guard의
# 이상치 탐지는 더 이상 이 값을 쓰지 않는다(PH_PHYSICAL_RANGE 참고, 2026-07-24 분리).
PH_THRESHOLDS = {
    "사과": {"min": 6.0, "max": 6.5},
    "배":   {"min": 6.0, "max": 6.5},
    "오이": {"min": 6.0, "max": 6.5},
    "감자": {"min": 5.5, "max": 6.2},
    "상추": {"min": 6.5, "max": 7.0},
}

# ── 6. EC near/위험값 (dS/m) — 높을수록 위험 ───────────────────────
EC_THRESHOLDS = {
    "사과": {"near": 2.0, "danger": None},
    "배":   {"near": 1.0, "danger": None},
    "오이": {"near": 1.0, "danger": None},      # 대표값(토성별 0.6~1.5 편차 있음)
    "감자": {"near": 2.0, "danger": 3.29},       # 대표품종(수미) 기준. 품종별 1.2~2.0 / 2.4~3.29
    "상추": {"near": 2.0, "danger": None},
}

# ── 7. 유기물·유효인산 near값 (적정범위, 위험값 없음 — 이분법) ─────
# 출처: 작물별_토양_화학성_적정범위.csv (7번 섹션에서 가중치 계산에만 쓰이고
# near값 자체는 미확정 상태였음 → 이번에 같은 CSV의 적정범위를 그대로 near값으로 채택)
ORGANIC_MATTER_THRESHOLDS = {  # g/kg
    "사과": None,  # CSV에 사과 행 없음 — 미확정
    "배":   {"min": 25, "max": 35},
    "오이": {"min": 20, "max": 30},
    "감자": {"min": 20, "max": 30},
    "상추": {"min": 20, "max": 30},
}

AVAILABLE_PHOSPHATE_THRESHOLDS = {  # mg/kg
    "사과": None,  # CSV에 사과 행 없음 — 미확정
    "배":   {"min": 200, "max": 300},
    "오이": {"min": 400, "max": 500},
    "감자": {"min": 250, "max": 350},
    "상추": {"min": 250, "max": 400},
}

# ── 8. 생육기간 정의 (월, 일) — 강수·일조 near/위험값을 낸 "9~11번 섹션"과 동일 구간.
# 재배형태 구분이 없는 작물은 리스트에 (start, end) 튜플 하나만, 상추 저지대재배처럼
# 연중 두 시기(봄+가을)에 걸치는 경우는 튜플 두 개를 순서대로 둔다.
# 출처: 작물5종_가중치표_최종.md 9~11번 섹션(생육기간 월 구분은 일반 농업기술 지식
# 기반 추정이며, 정확한 파종·수확일 데이터로 검증되지는 않았다고 명시돼 있음).
GROWTH_PERIOD = {
    "사과": [((4, 1), (10, 31))],
    "배":   [((4, 1), (10, 31))],
    "오이": {
        "촉성재배":   [((10, 1), (12, 31))],
        "반촉성재배": [((3, 1), (7, 31))],
    },
    "감자": {
        "봄재배":   [((3, 1), (6, 30))],
        "고랭지재배": [((5, 1), (9, 30))],
    },
    "상추": {
        "고랭지재배": [((6, 1), (8, 31))],
        "저지대재배": [((3, 1), (5, 31)), ((9, 1), (11, 30))],
    },
}


def get_growth_periods(crop, cultivation_type=None):
    """crop(+cultivation_type)에 맞는 생육기간 [((시작월,시작일),(종료월,종료일)), ...]을 반환한다."""
    entry = GROWTH_PERIOD[crop]
    if isinstance(entry, dict):
        ctype = resolve_cultivation_type(crop, cultivation_type)
        return entry[ctype]
    return entry
