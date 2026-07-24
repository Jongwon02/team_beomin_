"""reading_guard.guard_readings 결측치/이상치 방어 로직 검증."""

import pytest

from reading_guard import UnknownCropError, guard_readings

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
    assert result["reliability"] == "주의"


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


def test_신뢰불가_7개중_5개_결측이면_점수계산_안함():
    readings = {"온도": 15, "강수": 800}  # 나머지 5개는 키 자체가 없음
    result = guard_readings("사과", readings)

    assert result["reliability"] == "신뢰불가"
    assert result["adjusted_weights"] == {}
    assert len(result["excluded_variables"]) == 5
    assert result["reliability_reason"]


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
