"""forecast_risk_signal.py 단위테스트 - 과제 요구사항 원문
("향후 5일 예보 중 3일 이상 위험 -> 리스크등급 높음")을 정확히 구현했는지 검증.
"""

import pytest
from datetime import datetime, date, timedelta

from forecast_risk_signal import count_consecutive_risk_days, build_risk_signal, _josa_neun


def _make_daily(start_date, daily_values, direction="low_is_bad"):
    """일별 대표값 리스트 -> 시간별 레코드. low_is_bad면 새벽시간이 최저,
    high_is_bad면 낮시간이 최고가 되도록 구성."""
    records = []
    for i, v in enumerate(daily_values):
        d = start_date + timedelta(days=i)
        base = datetime.combine(d, datetime.min.time())
        for h in range(24):
            if direction == "low_is_bad":
                temp = v if h == 4 else v + 10
            else:
                temp = v if h == 14 else v - 10
            records.append((base + timedelta(hours=h), temp))
    return records


class TestCountConsecutiveRiskDays:

    def test_5일중_3일_위험이면_높음(self):
        today = date(2026, 3, 20)
        records = _make_daily(today, [-7.0, -7.5, 3.0, -8.0, 4.0])  # 3일 위험
        result = count_consecutive_risk_days(records, near=-6.85, danger=-8.37,
                                              direction="low_is_bad", today=today)
        assert result["risky_days"] == 3
        assert result["risk_grade"] == "높음"

    def test_1일만_위험이면_주의(self):
        today = date(2026, 3, 20)
        records = _make_daily(today, [-7.0, 3.0, 4.0, 5.0, 6.0])
        result = count_consecutive_risk_days(records, near=-6.85, danger=-8.37,
                                              direction="low_is_bad", today=today)
        assert result["risk_grade"] == "주의"

    def test_전혀_위험없으면_낮음(self):
        today = date(2026, 3, 20)
        records = _make_daily(today, [5.0, 6.0, 7.0, 8.0, 9.0])
        result = count_consecutive_risk_days(records, near=-6.85, danger=-8.37,
                                              direction="low_is_bad", today=today)
        assert result["risk_grade"] == "낮음"

    def test_예보가_5일보다_짧아도_있는_만큼만_판단(self):
        # 기상청 단기예보의 실제 제약(보통 3일치) 반영 확인
        today = date(2026, 3, 20)
        records = _make_daily(today, [-7.0, -7.5, -8.0])  # 3일치, 전부 위험
        result = count_consecutive_risk_days(records, near=-6.85, danger=-8.37,
                                              direction="low_is_bad", today=today)
        assert result["total_forecast_days"] == 3
        assert result["risky_days"] == 3
        assert result["risk_grade"] == "높음"

    def test_고온방향_high_is_bad_도_동작(self):
        today = date(2026, 7, 1)
        records = _make_daily(today, [37, 38, 20, 20, 20], direction="high_is_bad")
        result = count_consecutive_risk_days(records, near=33.0, danger=35.0,
                                              direction="high_is_bad", today=today)
        assert result["risky_days"] == 2
        assert result["risk_grade"] == "주의"

    def test_5일_경계_이후_날짜는_카운트_안함(self):
        today = date(2026, 3, 20)
        # 6일치 데이터를 주되, 6일째만 위험 - 5일 윈도우 밖이라 무시되어야 함
        values = [3.0, 3.0, 3.0, 3.0, 3.0, -10.0]
        records = _make_daily(today, values)
        result = count_consecutive_risk_days(records, near=-6.85, danger=-8.37,
                                              direction="low_is_bad", today=today)
        assert result["total_forecast_days"] == 5
        assert result["risky_days"] == 0  # 6일째(위험)는 윈도우 밖

    def test_데이터_없으면_판단불가(self):
        result = count_consecutive_risk_days([], near=-6.85, danger=-8.37, direction="low_is_bad")
        assert result["risk_grade"] == "판단불가"


class TestBuildRiskSignal:

    def test_메시지에_작물명과_조사가_올바르게_들어감(self):
        today = date(2026, 3, 20)
        records = _make_daily(today, [-7.0, -7.5, -8.0, 3.0, 4.0])
        result = build_risk_signal("오이", "야간기온", records, near=-6.85, danger=-8.37,
                                    direction="low_is_bad", today=today)
        assert "오이는" in result["reason"]  # "오이은(는)" 같은 오타 없이 정상 조사

    @pytest.mark.parametrize("word,expected_josa", [
        ("오이", "는"), ("사과", "는"), ("감자", "는"), ("상추", "는"), ("배", "는"),
        ("옥수수", "는"),  # 모음으로 끝나는 다른 예시도 확인
    ])
    def test_조사_선택_정확성(self, word, expected_josa):
        assert _josa_neun(word) == expected_josa
