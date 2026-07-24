"""reading_guard.guard_readings 결측치/이상치 방어 로직 검증."""

import pytest

from reading_guard import UnknownCropError, guard_readings, _load_weight_matrix, _crop_weights, _normal_reliability_threshold
from reference_data import (
    TEMP_THRESHOLDS, TEMP_THRESHOLDS_INSURANCE, PRECIP_THRESHOLDS, SUNSHINE_THRESHOLDS, PH_THRESHOLDS,
    PH_PHYSICAL_RANGE, get_valid_range,
)

NORMAL_READINGS = {
    "온도": 15,
    "강수": 800,
    "일조": 1100,
    "pH": 6.2,
    "유기물": 20,
    "유효인산": 300,
    "EC": 1.5,
}


def _sum_adjusted_weights(result):
    return round(sum(result["adjusted_weights"].values()), 2)


def test_정상_케이스_7개_변수_모두_유효():
    result = guard_readings("사과", NORMAL_READINGS)

    assert result["reliability"] == "정상"
    assert result["excluded_variables"] == []
    assert result["flagged_outliers"] == []
    assert set(result["usable_readings"]) == set(NORMAL_READINGS)
    assert _sum_adjusted_weights(result) == 100.0


def test_결측_케이스_EC_없으면_나머지_비례_재분배():
    readings = dict(NORMAL_READINGS, EC=None)
    result = guard_readings("사과", readings)

    assert result["excluded_variables"] == [{"변수": "EC", "사유": "결측"}]
    assert "EC" not in result["usable_readings"]
    assert "EC" not in result["adjusted_weights"]
    assert set(result["adjusted_weights"]) == {"온도", "강수", "일조", "pH", "유기물", "유효인산"}
    assert _sum_adjusted_weights(result) == 100.0
    # 사과는 EC 가중치가 8%뿐이라 남은 커버리지 92%->"정상". 예전(개수 기준)엔
    # "무엇이든 하나만 빠져도 주의"였지만, 가중치 기준으로 바뀌면서 비중 작은
    # 변수 하나 빠진 정도는 더 이상 "주의"로 내리지 않는다(의도된 변경).
    assert result["reliability"] == "정상"


def test_물리적_이상치_pH_영하값은_결측과_동일하게_처리():
    readings = dict(NORMAL_READINGS, pH=-3)
    result = guard_readings("사과", readings)

    assert result["excluded_variables"] == [
        {"변수": "pH", "사유": "물리적 이상치(유효범위 0~14 벗어남)"}
    ]
    assert "pH" not in result["usable_readings"]
    assert "pH" not in result["adjusted_weights"]
    assert _sum_adjusted_weights(result) == 100.0


def test_통계적_이상치_위험값보다_20퍼센트_초과면_flagged_되지만_계산에는_포함():
    # 감자 온도 위험(고온)=39.11, near(고온)=36.97 -> 39.11+ (39.11-36.97)*0.2=39.538 이상이면 플래그
    readings = {
        "온도": 45,
        "강수": 300,
        "일조": 900,
        "pH": 5.8,
        "유기물": 20,
        "유효인산": 300,
        "EC": 1.5,
    }
    result = guard_readings("감자", readings, cultivation_type="봄재배")

    assert result["excluded_variables"] == []
    assert "온도" in result["usable_readings"]
    assert result["usable_readings"]["온도"] == 45.0  # 값 자체는 버리지 않는다
    assert any(f["변수"] == "온도" for f in result["flagged_outliers"])
    assert result["reliability"] == "주의"


def test_신뢰불가_5개_결측이라도_남은_가중치가_50퍼센트_넘으면_통과():
    """개수 기준(구버전)이었다면 5개 결측 -> 무조건 신뢰불가였겠지만, 사과 가중치
    기준 온도(38)+강수(22)=60%가 남아 50% 기준을 넘으므로 이제는 통과해야 한다
    (흙토람 장애로 pH·유기물·유효인산·EC 4개가 통째로 빠져도 온도·강수·일조가
    살아있으면 신뢰할 만하다는 이번 개선의 핵심 시나리오)."""
    readings = {"온도": 15, "강수": 800}  # 나머지 5개는 키 자체가 없음
    result = guard_readings("사과", readings)

    assert len(result["excluded_variables"]) == 5
    assert result["reliability"] != "신뢰불가"
    assert result["adjusted_weights"] != {}


def test_신뢰불가_남은_가중치가_38퍼센트뿐이면_여전히_신뢰불가():
    """온도(38%) 하나만 남고 강수·일조까지 포함해 6개가 빠지면(실제로 창원시
    마산회원구 등에서 계절 요인 + 흙토람 결측이 겹쳐 발생) 여전히 신뢰불가여야
    한다 - 임계치 완화가 "고가중치 항목까지 여러 개 빠진" 위험한 경우로 새면
    안 된다는 걸 확인하는 회귀 테스트."""
    readings = {"온도": 15}  # 나머지 6개는 키 자체가 없음
    result = guard_readings("사과", readings)

    assert len(result["excluded_variables"]) == 6
    assert result["reliability"] == "신뢰불가"
    assert result["adjusted_weights"] == {}
    assert result["reliability_reason"]


class Test가중치_커버리지_경계값:
    """사과 가중치(온도38·강수22·일조18·pH6·유기물4·유효인산4·EC8, 합=100)로
    MIN_RELIABLE_WEIGHT_COVERAGE(0.5) 경계 위/아래/정확히를 검증한다."""

    def test_커버리지_44퍼센트_경계_바로_아래는_신뢰불가(self):
        # 포함: 강수22+일조18+유효인산4 = 44% (<50%)
        readings = {"강수": 800, "일조": 1100, "유효인산": 300}
        result = guard_readings("사과", readings)
        assert result["reliability"] == "신뢰불가"

    def test_커버리지_정확히_50퍼센트는_신뢰불가_아님(self):
        # 포함: 강수22+일조18+pH6+유효인산4 = 50% (경계값, strict less-than이라 통과)
        readings = {"강수": 800, "일조": 1100, "pH": 6.2, "유효인산": 300}
        result = guard_readings("사과", readings)
        assert result["reliability"] != "신뢰불가"
        assert result["reliability"] == "주의"  # 50%<80%이므로 "주의"

    def test_커버리지_58퍼센트_경계_바로_위는_주의(self):
        # 포함: 강수22+일조18+pH6+유효인산4+EC8 = 58% (>50%, <80%)
        readings = {"강수": 800, "일조": 1100, "pH": 6.2, "유효인산": 300, "EC": 1.5}
        result = guard_readings("사과", readings)
        assert result["reliability"] == "주의"


@pytest.mark.parametrize(
    "crop, readings, cultivation_type",
    [
        ("사과", NORMAL_READINGS, None),
        ("사과", dict(NORMAL_READINGS, EC=None), None),
        ("사과", dict(NORMAL_READINGS, pH=-3), None),
        (
            "감자",
            {
                "온도": 45,
                "강수": 300,
                "일조": 900,
                "pH": 5.8,
                "유기물": 20,
                "유효인산": 300,
                "EC": 1.5,
            },
            "봄재배",
        ),
    ],
)
def test_가중치_재정규화_합은_항상_100(crop, readings, cultivation_type):
    result = guard_readings(crop, readings, cultivation_type=cultivation_type)
    assert _sum_adjusted_weights(result) == 100.0


def test_존재하지_않는_작물명은_예외():
    with pytest.raises(UnknownCropError):
        guard_readings("사고", NORMAL_READINGS)  # "사과" 오타

    with pytest.raises(UnknownCropError):
        guard_readings("Apple", NORMAL_READINGS)


def test_재배형태_필수인_작물에_안_주면_예외():
    from reference_data import MissingCultivationTypeError

    with pytest.raises(MissingCultivationTypeError):
        guard_readings("오이", NORMAL_READINGS)  # cultivation_type 누락

    with pytest.raises(MissingCultivationTypeError):
        guard_readings("감자", NORMAL_READINGS)

    with pytest.raises(MissingCultivationTypeError):
        guard_readings("상추", NORMAL_READINGS)


def test_존재하지_않는_재배형태는_예외():
    from reference_data import InvalidCultivationTypeError

    with pytest.raises(InvalidCultivationTypeError):
        guard_readings("오이", NORMAL_READINGS, cultivation_type="노지재배")  # 존재 안 하는 값


def test_재배형태_구분_없는_작물은_None으로도_정상_동작():
    # 사과·배는 cultivation_type을 안 줘도(None) 예외 없이 정상 동작해야 한다
    result = guard_readings("사과", NORMAL_READINGS, cultivation_type=None)
    assert result["reliability"] == "정상"


def test_방어함수와_스코어링이_같은_재배형태_기준을_쓰는지_확인():
    """이전에 발견된 버그(오이 기본 재배형태가 방어함수/스코어링에서 서로 달랐던 것) 재발 방지용."""
    from scoring_engine import score_crop

    # 일조=700은 촉성재배 기준(near=522.6)으로는 정상, 반촉성재배 기준(near=999.2)으로는 위험값 이하
    readings = {
        "온도": 20, "강수": 500, "일조": 700, "pH": 6.2,
        "유기물": 25, "유효인산": 450, "EC": 0.8,
    }

    for ctype in ["촉성재배", "반촉성재배"]:
        guard_result = guard_readings("오이", readings, cultivation_type=ctype)
        일조_flagged = any(f["변수"] == "일조" for f in guard_result["flagged_outliers"])

        score_result = score_crop(
            "오이",
            guard_result["usable_readings"],
            guard_result["adjusted_weights"],
            cultivation_type=ctype,
        )
        일조_점수 = score_result["breakdown"]["일조"]["score"]

        if ctype == "촉성재배":
            # near(522.6) 이상이라 방어함수는 플래그 안 달고, 스코어링도 100점이어야 함
            assert 일조_flagged is False
            assert 일조_점수 == 100
        else:  # 반촉성재배: near=999.2, danger=930.1 -> 700은 위험값보다도 낮음
            assert 일조_flagged is True
            assert 일조_점수 < 30  # near~위험값 구간보다도 낮은 점수여야 함


CROP_SAFE_CTYPE = {"사과": None, "배": None, "오이": "촉성재배", "감자": "봄재배", "상추": "고랭지재배"}


def _safe_readings(crop, ctype):
    """그 작물·재배형태의 near값 안쪽(=이상치 플래그가 안 뜨는) 값들로 구성한
    "깨끗한" 결측 없는 읽기값. 유기물·유효인산은 reading_guard가 이상치 판정을
    아예 안 하므로(근거 없음) 아무 유효값이나 써도 안전하다."""
    if crop in TEMP_THRESHOLDS_INSURANCE:
        t = TEMP_THRESHOLDS_INSURANCE[crop]
        temp = (t["cold_near"] + t["heat_near"]) / 2
    else:
        t = TEMP_THRESHOLDS[crop][ctype]
        temp = (t["cold_near"] + t["heat_near"]) / 2

    precip_entry = PRECIP_THRESHOLDS[crop]
    precip_near = precip_entry["near"] if "near" in precip_entry else precip_entry[ctype]["near"]
    sun_entry = SUNSHINE_THRESHOLDS[crop]
    sun_near = sun_entry["near"] if "near" in sun_entry else sun_entry[ctype]["near"]
    ph_th = PH_THRESHOLDS[crop]

    return {
        "온도": temp,
        "강수": precip_near * 1.1,
        "일조": sun_near * 1.1,
        "pH": (ph_th["min"] + ph_th["max"]) / 2,
        "유기물": 25,
        "유효인산": 300,
        "EC": 1.0,
    }


class Test작물별_정상경계_자동도출:
    """2026-07-24: 고정 NORMAL_RELIABILITY_WEIGHT_COVERAGE=0.8을 작물별 자동도출
    경계로 교체 - 오이(EC 가중치 22%)와 배(EC 가중치 8%) 사이의 역전(오이 EC-only는
    주의인데 배 4개결측은 정상이던 문제)이 해소됐는지 검증."""

    @pytest.mark.parametrize("crop", ["사과", "배", "오이", "감자", "상추"])
    def test_EC만_결측이면_정상(self, crop):
        readings = _safe_readings(crop, CROP_SAFE_CTYPE[crop])
        readings["EC"] = None
        result = guard_readings(crop, readings, cultivation_type=CROP_SAFE_CTYPE[crop])

        assert result["flagged_outliers"] == []
        assert result["reliability"] == "정상"

    @pytest.mark.parametrize("crop", ["사과", "배", "오이", "감자", "상추"])
    def test_흙토람_4개_전부_결측이면_정상이_아님(self, crop):
        """pH·유기물·유효인산·EC 4개 전부 빠지면(도심 구 패턴) 작물이 뭐든
        여전히 "정상"으로 새면 안 된다(50~65~80%대 어디에 떨어지든 최소
        "주의" 이상이어야 함 - 자동도출 경계의 핵심 안전장치)."""
        readings = _safe_readings(crop, CROP_SAFE_CTYPE[crop])
        for var in ("pH", "유기물", "유효인산", "EC"):
            readings[var] = None
        result = guard_readings(crop, readings, cultivation_type=CROP_SAFE_CTYPE[crop])

        assert result["reliability"] != "정상"

    @pytest.mark.parametrize("crop", ["사과", "배", "오이", "감자", "상추"])
    def test_정상경계는_EC_가중치가_클수록_낮아진다(self, crop):
        """_normal_reliability_threshold가 실제로 가중치표에서 자동 도출되는지-
        EC 가중치가 클수록(예: 오이 22%) 경계가 낮아지고(78%), 작을수록(배 8%)
        경계가 높아짐(92%)을 공식 그대로 재확인한다."""
        wm = _load_weight_matrix()
        weights = _crop_weights(crop, wm)
        threshold = _normal_reliability_threshold(weights)

        assert threshold == pytest.approx((100 - weights["EC"]) / 100)

    def test_오이와_배의_정상경계가_실제로_다르다(self):
        """이번 개선의 핵심 동기: 오이(EC 22%)와 배(EC 8%)는 서로 다른 경계를
        가져야 하고, 오이 쪽이 더 낮아야 한다(EC 비중이 큰 작물일수록 "EC 하나
        빠진 것"의 타격이 커서, 더 낮은 coverage에서도 "정상으로 봐줄 만하다"는
        기준 자체는 동일하게 적용되지만 결과 숫자는 낮게 나온다)."""
        wm = _load_weight_matrix()
        cucumber_threshold = _normal_reliability_threshold(_crop_weights("오이", wm))
        pear_threshold = _normal_reliability_threshold(_crop_weights("배", wm))

        assert cucumber_threshold == pytest.approx(0.78)
        assert pear_threshold == pytest.approx(0.92)
        assert cucumber_threshold < pear_threshold


class Test유효인산_지목별_물리범위:
    """2026-07-24: VALID_RANGES["유효인산"]을 지목(land-use)별로 분리 - 시설재배(오이)
    최상위 정상구간(soil.py 개방구간 근사대표값 2201)까지는 더 이상 물리적 이상치로
    안 걸리는지, 그 상한(2500)을 넘는 값과 나머지 지목(과수원 등)은 여전히
    걸리는지 확인한다."""

    def test_오이_유효인산_2201은_더이상_물리적_이상치가_아님(self):
        readings = _safe_readings("오이", "촉성재배")
        readings["유효인산"] = 2201  # 흙토람 시설 최상위 개방구간(2001↑)의 근사대표값
        result = guard_readings("오이", readings, cultivation_type="촉성재배")

        assert result["excluded_variables"] == []
        assert "유효인산" in result["usable_readings"]

    def test_오이_유효인산_3000은_여전히_물리적_이상치(self):
        readings = _safe_readings("오이", "촉성재배")
        readings["유효인산"] = 3000  # 흙토람 시설 지목 이론상 최댓값(2201)을 훨씬 초과
        result = guard_readings("오이", readings, cultivation_type="촉성재배")

        assert result["excluded_variables"] == [
            {"변수": "유효인산", "사유": "물리적 이상치(유효범위 0~2500 벗어남)"}
        ]

    def test_배_유효인산_2100은_여전히_물리적_이상치_지목별_상한_안_바뀜(self):
        """배(과수원)는 흙토람 구간 최댓값이 651이라 과다플래그 문제가 없었으므로
        상한을 그대로 2000에 뒀다 - 2000을 넘는 값은 여전히 걸려야 한다."""
        readings = _safe_readings("배", None)
        readings["유효인산"] = 2100
        result = guard_readings("배", readings)

        assert result["excluded_variables"] == [
            {"변수": "유효인산", "사유": "물리적 이상치(유효범위 0~2000 벗어남)"}
        ]

    def test_get_valid_range_지목별_분기(self):
        assert get_valid_range("유효인산", "Fachs") == (0, 2500)
        assert get_valid_range("유효인산", "Fruit") == (0, 2000)
        assert get_valid_range("유효인산", "Pfld") == (0, 2000)
        assert get_valid_range("pH") == (0, 14)  # 지목 무관 변수는 land_use_category 없이도 동작


class TestPH_이상치_물리범위_분리:
    """2026-07-24: pH 이상치 탐지를 작물별 적정범위(PH_THRESHOLDS, 스코어링 전용)가
    아니라 물리적 상식범위(PH_PHYSICAL_RANGE=4.0~9.0)로 분리 - 정상적인 한국 토양
    pH(적정범위 밖이지만 4~9 안)는 더 이상 안 걸리고, 진짜 비정상(4 미만/9 초과)은
    여전히 걸리는지 확인한다."""

    def test_오이_pH_5_0은_적정범위_밖이지만_더이상_flagged_안됨(self):
        # 오이 PH_THRESHOLDS는 6.0~6.5(옛 기준이면 flagged) - 새 물리범위(4~9) 안이라 통과
        readings = _safe_readings("오이", "촉성재배")
        readings["pH"] = 5.0
        result = guard_readings("오이", readings, cultivation_type="촉성재배")

        assert result["flagged_outliers"] == []
        assert result["reliability"] == "정상"

    def test_pH_3_0은_물리범위_밖이라_여전히_flagged(self):
        readings = _safe_readings("사과", None)
        readings["pH"] = 3.0
        result = guard_readings("사과", readings)

        assert any(f["변수"] == "pH" for f in result["flagged_outliers"])
        assert result["reliability"] == "주의"

    def test_pH_9_5는_물리범위_밖이라_여전히_flagged(self):
        readings = _safe_readings("사과", None)
        readings["pH"] = 9.5
        result = guard_readings("사과", readings)

        assert any(f["변수"] == "pH" for f in result["flagged_outliers"])
        assert result["reliability"] == "주의"

    def test_PH_THRESHOLDS는_여전히_스코어링에서_쓰인다(self):
        """PH_THRESHOLDS 자체를 삭제하지 않고 scoring_engine 전용으로 남겨뒀는지 확인."""
        from scoring_engine import score_ph

        th = PH_THRESHOLDS["오이"]
        assert score_ph("오이", (th["min"] + th["max"]) / 2) == 100
