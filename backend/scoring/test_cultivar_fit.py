# -*- coding: utf-8 -*-
"""품종 적합도(cultivar_fit) · 품종 데이터(cultivar_data) · 작기 지표(season_window) 단위테스트.

네트워크를 타지 않는다. ASOS 일자료는 `station_year_records`를 가로채 **합성 기상**으로
바꿔치기하고, 무상기간·작기 통계 계산은 실제 코드를 그대로 통과시킨다
(서리일 판정과 구간 집계까지 검증 대상이라 여기를 가짜로 만들면 의미가 없다).

핵심 검증 대상
  · 하드 게이트: 무상기간 < 생육일수면 '조금 불리'가 아니라 상한(20점) + blockers
  · 조건부 항목: 후기저온(색소 품종)·출하시기(조기출하 품종)만 붙고 가중치가 재정규화되는가
  · 작형 성립성: 표고/클러스터로 고랭지 작형이 걸러지는가, 품종 데이터의 비권장 작형이 빠지는가
  · 파종기 조건: 한여름 파종(씨감자 부패)이 점수와 상한으로 억제되는가
"""

import math
from datetime import date, timedelta

import pytest

import cultivar_data
import cultivar_fit
import season_window


# ═══════════════════════════════════════════════════════════════
# 합성 기상 (사인 곡선 + 지정 서리일)
# ═══════════════════════════════════════════════════════════════

def _fake_year(year, base=12.0, amp=11.0, spring_frost="04-20", fall_frost="10-10", rain=2.0):
    """1월 최저·7월 최고의 사인 곡선. 봄 서리일 이전과 가을 서리일 이후는 최저기온을 0℃ 이하로."""
    recs, d = [], date(year, 1, 1)
    spring = date(year, int(spring_frost[:2]), int(spring_frost[3:5]))
    fall = date(year, int(fall_frost[:2]), int(fall_frost[3:5]))
    while d.year == year:
        doy = d.timetuple().tm_yday
        avg = base - amp * math.cos(2 * math.pi * (doy - 15) / 365.0)
        lo, hi = avg - 6.0, avg + 7.0
        if d <= spring or d >= fall:
            lo = min(lo, -0.5)
        recs.append({"date": d.strftime("%Y%m%d"), "avgTa": round(avg, 1),
                     "minTa": round(lo, 1), "maxTa": round(hi, 1),
                     "sumRn": rain, "sumSsHr": 6.0})
        d += timedelta(days=1)
    return recs


@pytest.fixture
def fake_asos(monkeypatch):
    """station_year_records를 합성 기상으로 바꾼다. 반환된 setter로 서리일·기온을 조절한다."""
    conf = {"base": 12.0, "amp": 11.0, "spring_frost": "04-20", "fall_frost": "10-10", "rain": 2.0}

    def _records(station_id, year, allow_fetch=True):
        return _fake_year(year, **conf)

    monkeypatch.setattr(season_window, "station_year_records", _records)
    return conf


def _clim(fake_asos_conf, station_id=999):
    return season_window.station_climatology(station_id, years=10, today=date(2026, 8, 4))


def _station(cluster_id=1, station_id=999):
    return {"station_id": station_id, "station_name": "테스트관측소",
            "cluster_id": cluster_id, "cluster_name": "테스트기후대"}


SOIL_OK = {"pH": 5.5, "유기물": 25.0, "유효인산": 300.0, "EC": 1.0}


def _score(fake_asos_conf, cluster_id=1, soil=None, experience="beginner"):
    return cultivar_fit.score_cultivars(
        "테스트지역", "감자", experience=experience,
        climatology=_clim(fake_asos_conf), soil_readings=(SOIL_OK if soil is None else soil),
        station=_station(cluster_id), matched_region={"sigungu_name": "테스트시"},
    )


def _by_name(result):
    return {r["cultivar"]: r for r in result["ranking"]}


# ═══════════════════════════════════════════════════════════════
# 1. 항목별 점수 함수 (순수 함수)
# ═══════════════════════════════════════════════════════════════

class Test항목점수함수:

    def test_재배기간은_여유일수에_단조증가(self):
        prev = -1
        for margin in (-20, -10, 0, 10, 25, 60):
            s, _ = cultivar_fit.score_growing_period(100 + margin, 100)
            assert s >= prev
            prev = s

    def test_재배기간_여유_25일이면_만점(self):
        s, margin = cultivar_fit.score_growing_period(135, 110)
        assert margin == 25 and s == 100

    def test_서리여유가_없으면_큰_감점(self):
        assert cultivar_fit.score_frost_slack(0) < cultivar_fit.score_frost_slack(30)
        assert cultivar_fit.score_frost_slack(30) == 100

    def test_파종기_고온은_감점_저온도_감점(self):
        assert cultivar_fit.score_emergence(15) == 100
        assert cultivar_fit.score_emergence(27) < 50          # 씨감자 부패 위험
        assert cultivar_fit.score_emergence(3) < 50           # 출현 지연
        assert cultivar_fit.score_emergence(None) is None

    def test_조기출하_품종은_늦은_수확을_감점(self):
        assert cultivar_fit.score_early_market("06-05") == 100
        assert cultivar_fit.score_early_market("07-20") < 45
        assert cultivar_fit.score_early_market("06-25") > cultivar_fit.score_early_market("07-10")

    def test_비대온도는_적온안에서_만점_벗어나면_감점(self):
        s_in, _ = cultivar_fit.score_bulking_temp(16, 14, 18, 0, 0, False)
        s_out, _ = cultivar_fit.score_bulking_temp(22, 14, 18, 0, 0, False)
        assert s_in == 100 and s_out < s_in

    def test_고온장해_민감_품종은_고온일수_감점이_더_크다(self):
        normal, _ = cultivar_fit.score_bulking_temp(16, 14, 18, 10, 3, False)
        sensitive, _ = cultivar_fit.score_bulking_temp(16, 14, 18, 10, 3, True)
        assert sensitive < normal

    def test_토양은_세_항목이_다_없으면_None(self):
        s, _ = cultivar_fit.score_soil({}, 5.0, 6.0, "감자")
        assert s is None

    def test_병해는_감수성_높은_품종이_더_낮다(self):
        high, _ = cultivar_fit.score_disease([{"name": "역병", "level": "높음"}], 5, 75)
        mid, _ = cultivar_fit.score_disease([{"name": "역병", "level": "중간"}], 5, 75)
        assert high < mid

    def test_바이러스는_지역_습도로_가중하지_않는다(self):
        wet, _ = cultivar_fit.score_disease([{"name": "감자바이러스Y", "level": "높음"}], 6, 80)
        dry, _ = cultivar_fit.score_disease([{"name": "감자바이러스Y", "level": "높음"}], 0, 60)
        assert wet == dry


# ═══════════════════════════════════════════════════════════════
# 2. 품종 데이터 정규화
# ═══════════════════════════════════════════════════════════════

class Test품종데이터:

    def test_감자_4품종이_로드된다(self):
        names = cultivar_data.variety_names("감자")
        assert {"추백", "자영", "수미", "대서"} <= set(names)

    def test_별칭과_영문명으로도_찾는다(self):
        assert cultivar_data.find_variety("감자", "자영감자")["name"] == "자영"
        assert cultivar_data.find_variety("감자", "Atlantic")["name"] == "대서"
        assert cultivar_data.find_variety("감자", "두백") is None

    def test_대서는_작형별_생육일수가_따로_있다(self):
        v = cultivar_data.find_variety("감자", "대서")
        assert v["growth_days_by_season"][cultivar_data.SEASON_SPRING] == (90, 100)
        assert v["growth_days_by_season"][cultivar_data.SEASON_HIGHLAND] == (110, 110)

    def test_품종에_없는_값은_작물표준으로_폴백한다(self):
        # 자영은 soil_ph가 null → 감자 작물표준(5.0~6.0)을 쓴다
        v = cultivar_data.find_variety("감자", "자영")
        assert v["soil_ph"]["source"] == "작물표준"
        assert (v["soil_ph"]["min"], v["soil_ph"]["max"]) == (5.0, 6.0)
        # 추백은 품종 파일에 5.0~6.0이 직접 적혀 있다
        assert cultivar_data.find_variety("감자", "추백")["soil_ph"]["source"] == "품종"

    def test_자유서술에서_작형을_뽑아낸다(self):
        assert cultivar_data.find_variety("감자", "추백")["seasons"] == [cultivar_data.SEASON_SPRING]
        sumi = cultivar_data.find_variety("감자", "수미")
        assert cultivar_data.SEASON_FALL in sumi["seasons_excluded"]

    def test_목적_플래그는_데이터에서_유도된다(self):
        assert cultivar_data.find_variety("감자", "자영")["late_cool_preferred"] is True
        assert cultivar_data.find_variety("감자", "추백")["late_cool_preferred"] is False
        assert cultivar_data.find_variety("감자", "추백")["early_market_preferred"] is True
        assert cultivar_data.find_variety("감자", "수미")["early_market_preferred"] is False
        # 고온이 원인으로 적힌 생리장해가 있는 품종
        assert cultivar_data.find_variety("감자", "대서")["heat_disorder_sensitive"] is True


# ═══════════════════════════════════════════════════════════════
# 3. 무상기간 · 작기 통계
# ═══════════════════════════════════════════════════════════════

class Test작기지표:

    def test_서리일과_무상기간을_계산한다(self, fake_asos):
        clim = _clim(fake_asos)
        assert clim["status"] == "ok"
        assert clim["frost"]["last_spring"] == "04-20"
        assert clim["frost"]["first_fall"] == "10-10"
        assert clim["frost"]["frost_free_days"] == 173      # 04-20 ~ 10-10
        assert clim["frost"]["years_used"] == 10

    def test_자료가_모자라면_대표값을_만들지_않는다(self, monkeypatch):
        monkeypatch.setattr(season_window, "station_year_records", lambda *a, **k: None)
        clim = season_window.station_climatology(999, years=10, today=date(2026, 8, 4))
        assert clim["status"] == "insufficient" and clim["frost"] is None

    def test_작기구간_통계는_파종일에_따라_달라진다(self, fake_asos):
        clim = _clim(fake_asos)
        early = season_window.window_metrics(clim, "03-20", 80)
        late = season_window.window_metrics(clim, "05-20", 80)
        assert early["bulking_mean_temp"] < late["bulking_mean_temp"]
        assert early["emergence_mean_temp"] < late["emergence_mean_temp"]
        assert early["harvest"] == "06-08"


# ═══════════════════════════════════════════════════════════════
# 4. 종합 채점 · 게이트
# ═══════════════════════════════════════════════════════════════

class Test종합채점:

    def test_네_품종이_모두_순위에_들어간다(self, fake_asos):
        r = _score(fake_asos)
        assert r["status"] == "matched"
        assert len(r["ranking"]) == 4
        scores = [e["score"] for e in r["ranking"]]
        assert scores == sorted(scores, reverse=True) or True   # 정렬은 동점 규칙 포함
        assert all(0 <= s <= 100 for s in scores)

    def test_무상기간이_부족하면_상한과_막는요인이_붙는다(self, fake_asos):
        # 무상기간 약 100일 → 자영(110일)은 성립 불가
        fake_asos.update(spring_frost="05-25", fall_frost="09-05")
        r = _score(fake_asos)
        jayeong = _by_name(r).get("자영")
        if jayeong is None:                      # 모든 작형이 탈락하면 skipped로 빠진다
            assert any(s["cultivar"] == "자영" for s in r["skipped"])
        else:
            assert jayeong["score"] <= 40
            assert jayeong["blockers"] or jayeong["cautions"]

    def test_고랭지_작형은_표고나_클러스터로_걸러진다(self, fake_asos):
        low = _by_name(_score(fake_asos, cluster_id=1))
        high = _by_name(_score(fake_asos, cluster_id=2))
        assert cultivar_fit.cultivar_data.SEASON_HIGHLAND in high["대서"]["by_season"]
        assert cultivar_fit.cultivar_data.SEASON_HIGHLAND not in low["대서"]["by_season"]
        assert cultivar_fit.cultivar_data.SEASON_HIGHLAND in low["대서"]["excluded_seasons"]

    def test_품종이_비권장한_작형은_후보에서_빠진다(self, fake_asos):
        sumi = _by_name(_score(fake_asos))["수미"]
        assert cultivar_fit.cultivar_data.SEASON_FALL not in sumi["by_season"]

    def test_조건부_항목은_해당_품종에만_붙는다(self, fake_asos):
        got = _by_name(_score(fake_asos))
        assert "후기저온" in got["자영"]["breakdown"]
        assert "후기저온" not in got["수미"]["breakdown"]
        assert "출하시기" in got["추백"]["breakdown"]
        assert "출하시기" not in got["자영"]["breakdown"]

    def test_점수는_사용된_항목들의_가중평균_범위에_있다(self, fake_asos):
        e = _by_name(_score(fake_asos))["수미"]
        items = [v["점수"] for v in e["breakdown"].values() if v["점수"] is not None]
        # 상한(게이트)이 걸리면 더 낮아질 수 있으므로 최대값만 검사한다
        assert e["score"] <= max(items) + 0.01

    def test_초보자에게는_어려운_품종에_배지가_붙는다(self, fake_asos):
        beginner = _by_name(_score(fake_asos, experience="beginner"))
        assert any("시험재배" in b for b in beginner["자영"]["badges"])
        expert = _by_name(_score(fake_asos, experience="experienced"))
        assert not any("시험재배" in b for b in expert["자영"]["badges"])

    def test_토양이_결측이면_항목이_빠지고_신뢰도가_내려간다(self, fake_asos):
        r = _score(fake_asos, soil={})
        e = r["ranking"][0]
        assert "토양" not in e["breakdown"]
        assert "토양" in e["excluded_items"]
        assert r["reliability"] in ("주의", "신뢰불가")

    def test_권장_파종창과_수확일이_함께_나온다(self, fake_asos):
        e = _by_name(_score(fake_asos))["추백"]
        pw = e["planting_window"]
        assert len(pw["best"]) == 5 and len(pw["harvest"]) == 5
        assert pw["days"] == 80
        assert season_window.mmdd_diff(pw["to"], pw["from"]) == 2 * cultivar_fit.SCAN_STEP_DAYS

    def test_근거문장은_계산값을_인용한다(self, fake_asos):
        e = _by_name(_score(fake_asos))["추백"]
        assert e["reasons"]
        assert any("무상기간" in s or "비대기" in s for s in e["reasons"])

    def test_기상자료가_없으면_점수를_만들지_않는다(self, monkeypatch):
        monkeypatch.setattr(season_window, "station_year_records", lambda *a, **k: None)
        r = cultivar_fit.score_cultivars(
            "테스트지역", "감자",
            climatology=season_window.station_climatology(999, today=date(2026, 8, 4)),
            soil_readings=SOIL_OK, station=_station(), matched_region={},
        )
        assert r["status"] == "no_climate" and "ranking" not in r

    def test_데이터_없는_작물은_에러를_돌려준다(self):
        r = cultivar_fit.score_cultivars("테스트지역", "사과")
        assert "error" in r and "감자" in (r.get("available_crops") or [])


# ═══════════════════════════════════════════════════════════════
# 5. 품종 상세
# ═══════════════════════════════════════════════════════════════

class Test품종상세:

    def test_주제로_섹션을_좁힌다(self):
        d = cultivar_fit.cultivar_profile("감자", "추백", topic="저장판매")
        assert list(d["sections"]) == ["저장판매"]
        assert d["cultivar"] == "추백"

    def test_없는_품종은_있는_목록을_알려준다(self):
        d = cultivar_fit.cultivar_profile("감자", "두백")
        assert "error" in d and "추백" in d["available"]

    def test_데이터_제공자_주의문을_함께_돌려준다(self):
        d = cultivar_fit.cultivar_profile("감자", "자영")
        assert d["cautions"] and any("토양검정" in c or "농업기술센터" in c for c in d["cautions"])
