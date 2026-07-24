"""
temperature_duration_rule.py의 _margin_to_score(냉해/일소 근접사례 -> 0~100 연속점수
변환 함수) 단위테스트. 이것도 scoring_engine의 것과 마찬가지로 "구간별 비선형 감점
함수"에 해당한다(다만 margin이라는 별도 좌표계를 쓰는 버전).
"""

import pytest

from temperature_duration_rule import (
    _margin_to_score,
    FROST_MARGIN_SAFE_BUFFER,
    check_heat_margin,
    check_spring_bloom_frost,
    score_apple_pear_temperature,
)
from datetime import datetime, timedelta


class TestMarginToScore:

    def test_안전한_margin은_100점(self):
        # margin=threshold-temp. -buffer 이하(충분히 안전)면 100점.
        assert _margin_to_score(-FROST_MARGIN_SAFE_BUFFER) == 100.0
        assert _margin_to_score(-10) == 100.0  # 훨씬 더 안전해도 100점 그대로(캡)

    def test_margin_0은_정확히_50점(self):
        # 정확히 한계온도 지점(margin=0) = 공식 기준선 자체 -> 중간점
        assert _margin_to_score(0) == pytest.approx(50.0)

    def test_위험한_margin은_0점(self):
        assert _margin_to_score(FROST_MARGIN_SAFE_BUFFER) == 0.0
        assert _margin_to_score(100) == 0.0  # 훨씬 위험해도 0점 그대로(캡)

    def test_근접사례가_안전한_경우보다_낮은_점수(self):
        """차등화 검증: margin이 0에 가까울수록(위험에 가까울수록) 점수가 낮아야 한다."""
        very_safe = _margin_to_score(-1.8)
        near_miss = _margin_to_score(-0.3)   # 아깝게 놓친 경우
        triggered = _margin_to_score(0.5)     # 실제로 넘은 경우
        assert very_safe > near_miss > triggered

    def test_단조_감소(self):
        """margin이 커질수록(더 위험해질수록) 점수가 단조 감소해야 한다."""
        margins = [-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0]
        scores = [_margin_to_score(m) for m in margins]
        assert scores == sorted(scores, reverse=True)


class TestHeatMarginDistinguishesSpikeFromRealHeatwave:
    """일소(폭염) 2일 조건 - 하루짜리 스파이크 vs 진짜 이틀 폭염 구분 검증."""

    def _make_daily(self, start, daily_maxes):
        records = []
        for i, m in enumerate(daily_maxes):
            d = start + timedelta(days=i)
            for h in range(24):
                records.append((d + timedelta(hours=h), m if h == 14 else m - 10))
        return records

    def test_하루만_스파이크면_안전(self):
        records = self._make_daily(datetime(2026, 7, 1), [34, 20])
        result = check_heat_margin(records)
        assert result["worst_near_miss"]["margin"] < 0  # 안전 방향

    def test_진짜_이틀_폭염은_위험(self):
        records = self._make_daily(datetime(2026, 7, 1), [34, 35])
        result = check_heat_margin(records)
        assert result["worst_near_miss"]["margin"] > 0  # 위험 방향

    def test_데이터_1일뿐이면_판단불가(self):
        records = self._make_daily(datetime(2026, 7, 1), [34])
        result = check_heat_margin(records)
        assert result["worst_near_miss"] is None


class TestScoreApplePearTemperatureIntegration:

    def test_정상적인_봄날은_100점(self):
        records = [(datetime(2026, 4, 15, h), 12.0) for h in range(24)]
        result = score_apple_pear_temperature(records, "사과", "거창")
        assert result["score"] == 100.0

    def test_frost_score와_heat_score_중_낮은쪽_채택(self):
        # 냉해는 안전(겨울이라 개화기 아님), 하지만 폭염 데이터를 섞으면
        # 최종 점수는 더 위험한 쪽(heat)을 따라가야 한다.
        cold_part = [(datetime(2026, 1, 15, h), -5.0) for h in range(24)]  # 개화기 아님, 무관
        hot_part = (
            [(datetime(2026, 7, 1, h), 34 if h == 14 else 20) for h in range(24)]
            + [(datetime(2026, 7, 2, h), 35 if h == 14 else 20) for h in range(24)]
        )
        result = score_apple_pear_temperature(cold_part + hot_part, "사과", "거창")
        assert result["score"] == result["heat_score"]
        assert result["frost_score"] == 100.0  # 겨울철 데이터는 냉해 판정 대상 아님
