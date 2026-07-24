# -*- coding: utf-8 -*-
"""귀농·농업 정책 CSV → policies.json 변환기.

CropAdvisor 프로필의 인적사항으로 브라우저에서 정책을 매칭하기 위해,
자유텍스트 CSV를 매칭 가능한 구조로 미리 가공한다.

실행:  python build_policies.py
입력:  귀농_농업_정책.csv  (같은 폴더)
출력:  policies.json       (같은 폴더, 정적 서버가 제공)
"""
import os, re, csv, json

BASE = os.path.dirname(os.path.abspath(__file__))
# 최종본(final)을 우선 소스로 사용, 없으면 기본 CSV로 폴백
_FINAL = os.path.join(BASE, "귀농_농업_정책.final.csv")
CSV_PATH = _FINAL if os.path.exists(_FINAL) else os.path.join(BASE, "귀농_농업_정책.csv")
OUT_PATH = os.path.join(BASE, "policies.json")

def trim(s, n):
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s[:n]

# 나이 조건 파싱 (만 N세 미만/이하/이상/초과, 범위)
def parse_age(text):
    lo = hi = None
    m = re.search(r"만\s?(\d{1,3})\s?세\s?(?:이상|초과|부터)", text)
    if m: lo = int(m.group(1))
    m = re.search(r"만\s?(\d{1,3})\s?세\s?(?:미만|이하|까지)", text)
    if m: hi = int(m.group(1))
    m = re.search(r"만\s?(\d{1,3})\s?세?\s?[~∼\-]\s?만?\s?(\d{1,3})\s?세", text)
    if m:
        lo = int(m.group(1)); hi = int(m.group(2))
    return lo, hi

def region_of(org, orgtype):
    org = (org or "").strip()
    if orgtype in ("중앙행정기관", "공공기관", "지방출자_출연기관", "지방공기업"):
        return {"scope": "national", "province": "", "city": ""}
    parts = org.split()
    if orgtype == "광역시도":
        return {"scope": "metro", "province": parts[0] if parts else org, "city": ""}
    # 시군구
    return {"scope": "local",
            "province": parts[0] if len(parts) >= 1 else "",
            "city": parts[1] if len(parts) >= 2 else ""}

def build():
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    out = []
    for r in rows:
        user = r.get("사용자구분", "")
        # 개인/가구 대상만 (순수 법인/시설/단체·소상공인 제외)
        if not ("개인" in user or "가구" in user):
            continue

        name = r.get("서비스명", "").strip()
        summary = r.get("서비스목적요약", "")
        criteria = r.get("선정기준", "")
        support = r.get("지원내용", "")
        blob = name + " " + summary + " " + criteria + " " + support

        lo, hi = parse_age(blob)
        tags = {
            "youth": ("청년" in blob) or ("후계농" in blob) or ("청년창업농" in blob),
            "senior": ("은퇴" in blob) or ("고령" in blob),
            "gwinong": ("귀농" in blob) or ("귀촌" in blob),
            "woman": ("여성" in blob),
            "disabled": ("장애" in blob),
            "income": ("소득" in blob) or ("중위소득" in blob) or ("기초생활" in blob),
            "edu": ("교육" in blob and "이수" in blob),
            "land": ("농지" in blob) or ("임야" in blob),
        }
        kws = [k for k in re.split(r"[,\s]+", r.get("_매칭키워드", "")) if k]

        out.append({
            "id": r.get("서비스ID", "").strip() or name,
            "name": name,
            "field": r.get("서비스분야", "").strip(),
            "org": r.get("소관기관명", "").strip(),
            "region": region_of(r.get("소관기관명", ""), r.get("소관기관유형", "").strip()),
            "user": user,
            "summary": trim(summary, 220),
            "support": trim(support, 320),
            "criteria": trim(criteria, 700),
            "deadline": trim(r.get("신청기한", ""), 80),
            "method": trim(r.get("신청방법", ""), 120),
            "phone": trim(r.get("전화문의", ""), 60),
            "url": r.get("상세조회URL", "").strip(),
            "ageMin": lo, "ageMax": hi,
            "tags": [k for k, v in tags.items() if v],
            "kw": kws,
        })

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"count": len(out), "policies": out}, f, ensure_ascii=False)
    size = os.path.getsize(OUT_PATH)
    print(f"policies.json 생성 완료: {len(out)}건 / {size/1024:.0f} KB")
    # 간단 통계
    from collections import Counter
    sc = Counter(p["region"]["scope"] for p in out)
    print("scope:", dict(sc))

if __name__ == "__main__":
    build()
