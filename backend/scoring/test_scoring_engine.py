"""
scoring_engine.py의 구간별 비선형 감점 함수(_linear_interpolate_beyond,
_binary_range_score) 및 변수별 점수 함수(score_temperature 등) 단위테스트.

과제 요구사항: "표준 범위를 살짝 벗어난 것과 크게 벗어난 것을 점수 차등화했는가?"
-> 이 테스트들이 정확히 그 "차등화가 실제로 단조/연속적으로 일어나는지"를 검증한다.
"""

import pytest

from scoring_engine import (
    _linear_interpolate_beyond,
    _binary_range_score,
    score_temperature,
    score_precipitation,
    score_sunshine,
    score_ph,
    score_ec,
    score_organic_matter,
    score_available_phosphate,
    score_crop,
)


# ═══════════════════════════════════════════════════════════════
# 1. _linear_interpolate_beyond - 핵심 비선형 감점 함수
# ═══════════════════════════════════════════════════════════════

class TestLinearInterpolateBeyond:

    def test_near_이내는_100점(self):
        # near=10, danger=20(값이 커질수록 위험한 방향). value가 near보다 안전한 쪽.
        assert _linear_interpolate_beyond(5, near=10, danger=20) == 100
        assert _linear_interpolate_beyond(10, near=10, danger=20) == 100  # 경계값도 100

    def test_near_danger_중간은_정확히_중간점수(self):
        # near=10(100점) -> danger=20(30점), 중간(15)은 정확히 (100+30)/2=65
        result = _linear_interpolate_beyond(15, near=10, danger=20)
        assert result == pytest.approx(65.0)

    def test_danger_지점은_기본_danger_score(self):
        result = _linear_interpolate_beyond(20, near=10, danger=20)
        assert result == pytest.approx(30.0)

    def test_danger_넘으면_같은_기울기로_계속_하락(self):
        # near=10->danger=20 구간 기울기: 70점/10단위. danger 넘어 5만큼 더 가면
        # 30 - 5*(7) = -5 -> floor(0)에 걸림. 정확한 중간 지점(danger+2.5)로 확인.
        beyond = _linear_interpolate_beyond(22.5, near=10, danger=20)  # danger 넘어 2.5
        # frac=1.25, extra=0.25, score = 30 - 0.25*70 = 12.5
        assert beyond == pytest.approx(12.5)

    def test_매우_크게_벗어나면_0점_바닥(self):
        result = _linear_interpolate_beyond(1000, near=10, danger=20)
        assert result == 0

    def test_방향이_반대인_경우도_동작(self):
        # 냉해처럼 near > danger (값이 작아질수록 위험)
        assert _linear_interpolate_beyond(0, near=-5, danger=-10) == 100  # near보다 높음=안전
        mid = _linear_interpolate_beyond(-7.5, near=-5, danger=-10)
        assert mid == pytest.approx(65.0)  # 중간점

    def test_near_danger_같으면_near_score_반환(self):
        # span=0 엣지케이스 - 0으로 나누기 방지 확인
        assert _linear_interpolate_beyond(999, near=10, danger=10) == 100

    def test_살짝_벗어남과_크게_벗어남이_차등화되는지(self):
        """과제 핵심 요구사항 직접 검증: 이탈 정도가 클수록 점수가 단조 감소해야 한다."""
        near, danger = 30, 40
        slight = _linear_interpolate_beyond(31, near, danger)   # 살짝 벗어남
        moderate = _linear_interpolate_beyond(35, near, danger)  # 중간
        severe = _linear_interpolate_beyond(39, near, danger)    # 크게 벗어남(danger 근접)
        assert 100 > slight > moderate > severe > 30 - 1  # 단조감소 + danger 근접시 near danger_score


# ═══════════════════════════════════════════════════════════════
# 2. _binary_range_score - 위험값 없는 변수용 완충 이분법
# ═══════════════════════════════════════════════════════════════

class TestBinaryRangeScore:

    def test_범위_안은_100점(self):
        assert _binary_range_score(6.2, min_v=6.0, max_v=6.5) == 100
        assert _binary_range_score(6.0, min_v=6.0, max_v=6.5) == 100  # 경계 포함
        assert _binary_range_score(6.5, min_v=6.0, max_v=6.5) == 100

    def test_범위_밖_살짝이_범위밖_많이보다_점수_높음(self):
        """차등화 검증: 범위를 살짝 벗어난 것과 크게 벗어난 것이 달라야 한다."""
        slight = _binary_range_score(6.55, min_v=6.0, max_v=6.5)  # 0.05 벗어남
        far = _binary_range_score(8.0, min_v=6.0, max_v=6.5)      # 1.5 벗어남
        assert slight > far
        assert far == 40  # 버퍼(0.05, 폭의 10%) 훨씬 넘어서 완전히 out_score 고정

    def test_버퍼_끝지점은_out_score(self):
        # 폭 0.5, buffer_ratio=0.1 -> buffer=0.05. min_v=6.0이므로 5.95가 버퍼 경계.
        at_buffer_edge = _binary_range_score(5.95, min_v=6.0, max_v=6.5)
        assert at_buffer_edge == pytest.approx(40.0)

    def test_None_값은_None_반환(self):
        assert _binary_range_score(None, min_v=6.0, max_v=6.5) is None

    def test_폭이_0이어도_안전(self):
        # min_v==max_v인 극단적 엣지케이스 - 0으로 나누기 방지
        result = _binary_range_score(5.0, min_v=6.0, max_v=6.0)
        assert result == 40  # 범위 밖이고 buffer=0.1(fallback) 처리됨


# ═══════════════════════════════════════════════════════════════
# 3. 변수별 점수 함수 - 실제 reference_data 근거값 기반 통합 테스트
# ═══════════════════════════════════════════════════════════════

class TestScoreTemperature:

    def test_감자_봄재배_적정범위는_100점(self):
        assert score_temperature("감자", 10, cultivation_type="봄재배") == 100

    def test_감자_봄재배_냉해_near_danger_차등화(self):
        near_temp = score_temperature("감자", -7.70, cultivation_type="봄재배")
        between = score_temperature("감자", -8.5, cultivation_type="봄재배")
        danger_temp = score_temperature("감자", -9.41, cultivation_type="봄재배")
        assert near_temp == 100  # near 경계는 아직 100점(그 안쪽)
        assert 100 > between > danger_temp

    def test_사과_배는_재배형태_없이도_동작(self):
        assert score_temperature("사과", 18) == 100

    def test_오이는_재배형태_필수(self):
        from reference_data import MissingCultivationTypeError
        with pytest.raises(MissingCultivationTypeError):
            score_temperature("오이", 20)


class TestScorePrecipitationSunshine:

    def test_강수_near_이상은_100점(self):
        assert score_precipitation("사과", 900) == 100  # near=750.5보다 많음

    def test_강수_부족할수록_점수_차등화(self):
        near_score = score_precipitation("사과", 750.5)
        low = score_precipitation("사과", 600)
        very_low = score_precipitation("사과", 430.6)  # 위험값
        assert near_score == 100
        assert low > very_low

    def test_일조_부족할수록_점수_차등화(self):
        ok = score_sunshine("배", 1200)
        low = score_sunshine("배", 950)
        assert ok == 100
        assert ok > low


class TestScorePhEcSoil:

    def test_pH_범위_안팎_차등화(self):
        # 배 pH 적정범위 6.0~6.5(폭 0.5) -> 버퍼 10%=0.05. 6.52는 버퍼 안(0.02 벗어남),
        # 8.0은 버퍼를 훨씬 넘어선 완전한 out_score 고정 구간.
        in_range = score_ph("배", 6.2)
        slightly_out = score_ph("배", 6.52)  # 버퍼(0.05) 안쪽 - 부분 감점
        far_out = score_ph("배", 8.0)         # 버퍼 훨씬 밖 - out_score 고정
        assert in_range == 100
        assert slightly_out > far_out
        assert far_out == 40

    def test_EC_위험값_있는_감자(self):
        ok = score_ec("감자", 1.0)
        near = score_ec("감자", 2.0)
        danger = score_ec("감자", 3.29)
        assert ok == 100
        assert near == 100  # near 경계 이하 포함
        assert danger < 100

    def test_EC_위험값_없는_사과_근사감점(self):
        ok = score_ec("사과", 1.5)
        over = score_ec("사과", 3.0)
        assert ok == 100
        assert over < 100

    def test_유기물_근거없는_사과는_None(self):
        assert score_organic_matter("사과", 30) is None

    def test_유효인산_근거있는_배(self):
        assert score_available_phosphate("배", 250) == 100


# ═══════════════════════════════════════════════════════════════
# 4. score_crop 통합 - 재정규화 및 근거없음 처리
# ═══════════════════════════════════════════════════════════════

class TestScoreCropIntegration:

    def test_전체_변수_정상이면_100점_근접(self):
        readings = {
            "온도": 18, "강수": 900, "일조": 1200, "pH": 6.2,
            "EC": 1.5, "유기물": 30, "유효인산": 250,
        }
        result = score_crop("사과", readings)
        assert result["total_score"] == 100
        assert result["excluded_no_reference"] == ["유기물", "유효인산"]  # 사과는 근거 없음

    def test_근거없는_변수_제외후_나머지로_재정규화(self):
        readings = {"온도": 18, "강수": 900, "일조": 1200, "pH": 6.2, "EC": 1.5}
        result = score_crop("사과", readings)
        # 유기물/유효인산 자체가 usable_readings에 없어도 근거없음 목록엔 없어야 함
        # (애초에 값이 없어서 스킵된 것과 근거없음 제외는 다름 - 여기선 값 자체를 안 줬음)
        assert result["total_score"] == 100

    def test_알수없는_작물명은_예외(self):
        with pytest.raises(ValueError):
            score_crop("딸기", {"온도": 20})

    def test_가중치_재분배_후_합이_100퍼센트_기여(self):
        # 사과는 유기물/유효인산 근거가 원래 없으므로, 나머지 5개 변수 가중치로
        # 100% 재정규화되어야 한다 - 전부 만점이면 total_score도 정확히 100.
        readings = {"온도": 18, "강수": 900, "일조": 1200, "pH": 6.2, "EC": 1.5}
        result = score_crop("사과", readings)
        assert result["total_score"] == 100
