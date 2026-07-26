# -*- coding: utf-8 -*-
"""chat_server의 대화 루프를 가짜 Anthropic 스트림으로 검증한다.

API 키 없이 돌아간다 - 검증 대상은 모델 품질이 아니라 배선이다:
  · 도구 호출(tool_use) → 실제 도구 실행 → 결과를 하나의 user 메시지로 재투입
  · 텍스트 델타가 SSE delta 프레임으로 나가는지
  · 거절(refusal) 시 content를 읽지 않고 error 프레임으로 끝나는지
  · 화면 맥락이 role:"system" 메시지로 마지막에 붙는지

실행: python backend/test_chat_server.py
"""
import io
import sys
import json



sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))

import chat_server as cs  # noqa: E402

# 가짜 usage(120/1800/90)가 실제 비용 로그에 섞이면 비용 분석이 망가진다.
cs.USAGE_LOG = __import__("os").path.join(
    __import__("tempfile").gettempdir(), "chat_usage_test.jsonl")

CTX = {
    "today": "2026-07-26", "region": "충청북도 충주시 주덕읍", "province": "충청북도",
    "activeTab": "favorites", "focusCrop": "사과",
    "plans": [{"crop": "사과", "todayTasks": ["물주기", "병해충 방제"]}],
}


class Blk:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Msg:
    def __init__(self, stop_reason, content):
        self.stop_reason = stop_reason
        self.content = content
        self.usage = Blk(input_tokens=120, cache_creation_input_tokens=0,
                         cache_read_input_tokens=1800, output_tokens=90)


class FakeStream:
    """SDK 스트림과 같은 모양: with 문 + 이벤트 이터레이션 + get_final_message()."""

    def __init__(self, chunks, final):
        self._chunks, self._final = chunks, final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for c in self._chunks:
            yield Blk(type="content_block_delta", delta=Blk(type="text_delta", text=c))

    def get_final_message(self):
        return self._final


def fake_stream_factory(scripted, seen):
    """scripted: [(내보낼 텍스트 조각들, 최종 Msg), ...] 를 순서대로 재생."""
    def stream(**kwargs):
        # messages 리스트는 루프가 계속 append하는 같은 객체다. 호출 시점 상태를
        # 보려면 얕은 복사를 떠 둬야 한다(안 그러면 전부 마지막 상태로 보인다).
        snap = dict(kwargs)
        snap["messages"] = list(kwargs["messages"])
        seen.append(snap)
        chunks, final = scripted.pop(0)
        return FakeStream(chunks, final)
    return stream


def run(scripted, message="8월엔 뭐 해요?"):
    frames = []
    seen = []
    orig = cs.client.beta.messages.stream
    cs.client.beta.messages.stream = fake_stream_factory(scripted, seen)
    try:
        cs.chat_turn(message, [], CTX, "test", 1,
                     lambda ev, data: frames.append((ev, data)))
    finally:
        cs.client.beta.messages.stream = orig
    return frames, seen


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + detail) if detail else ""))
    return bool(cond)


ok = True
print("[1] 도구 호출 1회 → 최종 답변")
tool_call = Msg("tool_use", [Blk(type="tool_use", id="tu_1", name="get_crop_schedule",
                                input={"crop": "사과", "month": 8})])
final = Msg("end_turn", [Blk(type="text", text="8월엔 물주기와 일소 대비를 해요.")])
frames, seen = run([([], tool_call), (["8월엔 ", "물주기와 일소 대비를 해요."], final)])
kinds = [f[0] for f in frames]
ok &= check("tool 프레임이 delta보다 먼저", kinds.index("tool") < kinds.index("delta"), str(kinds))
ok &= check("tool 이름 전달", frames[kinds.index("tool")][1]["name"] == "get_crop_schedule")
ok &= check("delta 텍스트 이어붙임",
            "".join(d["text"] for e, d in frames if e == "delta") == "8월엔 물주기와 일소 대비를 해요.")
ok &= check("done 프레임으로 종료", kinds[-1] == "done", str(frames[-1][1]["stop_reason"]))
ok &= check("모델 호출 2회", len(seen) == 2, "실제 %d회" % len(seen))

m2 = seen[1]["messages"]
ok &= check("2회차 마지막이 tool_result user 메시지",
            m2[-1]["role"] == "user" and m2[-1]["content"][0]["type"] == "tool_result")
ok &= check("tool_use_id 일치", m2[-1]["content"][0]["tool_use_id"] == "tu_1")
body = json.loads(m2[-1]["content"][0]["content"])
ok &= check("도구가 실제로 실행돼 8월 작업 반환",
            "재배유형" in body and body["기준월"] == "8월")

print("\n[2] 화면 맥락 주입 위치")
m1 = seen[0]["messages"]
ok &= check("마지막이 role=system(화면 맥락)", m1[-1]["role"] == "system")
ok &= check("그 앞이 사용자 질문", m1[-2]["role"] == "user" and m1[-2]["content"] == "8월엔 뭐 해요?")
ok &= check("맥락에 선택 지역 포함", "충주시 주덕읍" in m1[-1]["content"])
ok &= check("최상위 system은 캐시 지정",
            seen[0]["system"][0]["cache_control"] == {"type": "ephemeral"})
ok &= check("effort=low", seen[0]["output_config"]["effort"] == "low")
ok &= check("thinking 파라미터 미지정(기본 adaptive 유지)", "thinking" not in seen[0])
ok &= check("max_tokens=2500", seen[0]["max_tokens"] == 2500)

print("\n[3] 거절(refusal) 처리")
frames, _ = run([([], Msg("refusal", []))])
ok &= check("error/refusal 프레임", frames[-1][0] == "error" and frames[-1][1]["code"] == "refusal")

print("\n[4] 도구 루프 상한")
loop = [([], Msg("tool_use", [Blk(type="tool_use", id="t%d" % i, name="get_crop_schedule",
                                 input={"crop": "감자"})])) for i in range(cs.MAX_TOOL_ROUNDS)]
frames, seen = run(loop)
ok &= check("상한 도달 시 tool_loop 에러",
            frames[-1][0] == "error" and frames[-1][1]["code"] == "tool_loop")
ok &= check("호출 횟수가 상한과 동일", len(seen) == cs.MAX_TOOL_ROUNDS)

print("\n[5] 이력 자르기 (최근 %d턴)" % cs.HISTORY_TURNS)
long_hist = []
for i in range(20):
    long_hist += [{"role": "user", "content": "q%d" % i},
                  {"role": "assistant", "content": "a%d" % i}]
cut = cs._clean_history(long_hist)
ok &= check("12개로 절단", len(cut) == cs.HISTORY_TURNS * 2, "실제 %d" % len(cut))
ok &= check("user로 시작", cut[0]["role"] == "user")
ok &= check("assistant로 시작하는 이력 보정",
            cs._clean_history([{"role": "assistant", "content": "a"},
                               {"role": "user", "content": "b"}])[0]["role"] == "user")

print("\n[6] 사용량 제한")
cs._session_turns.clear(); cs._ip_day.clear()
for _ in range(cs.SESSION_TURN_LIMIT):
    cs.check_limits("s1", "1.1.1.1")
code, _msg = cs.check_limits("s1", "1.1.1.1")
ok &= check("세션 턴 상한 작동", code == "turn_limit")
cs._session_turns.clear(); cs._ip_day.clear()

print("\n[7] 체크리스트 조작")
CHECK_CTX = dict(CTX, checklist=[
    {"id": 1, "date": "2026-07-26", "key": "물주기", "text": "물주기 - 토성에 맞춰", "status": "시작 전"},
    {"id": 2, "date": "2026-07-26", "key": "싹틔우기", "text": "감자 싹 틔우기", "status": "시작 전"},
])


def run_ctx(scripted, ctx, message="싹 틔우기 다 했어"):
    frames, seen = [], []
    orig = cs.client.beta.messages.stream
    cs.client.beta.messages.stream = fake_stream_factory(scripted, seen)
    try:
        cs.chat_turn(message, [], ctx, "test", 1, lambda ev, d: frames.append((ev, d)))
    finally:
        cs.client.beta.messages.stream = orig
    return frames, seen


call = Msg("tool_use", [Blk(type="tool_use", id="c1", name="set_checklist_status",
                           input={"item_id": 2, "status": "완료"})])
done = Msg("end_turn", [Blk(type="text", text="감자 싹 틔우기를 완료로 표시했어요.")])
frames, seen = run_ctx([([], call), (["감자 싹 틔우기를 완료로 표시했어요."], done)], CHECK_CTX)
acts = [d for e, d in frames if e == "action"]
ok &= check("action 프레임 1개", len(acts) == 1, str([e for e, _ in frames]))
if acts:
    a = acts[0]
    ok &= check("항목 키 정확", a.get("item_key") == "싹틔우기", str(a))
    ok &= check("날짜 정확", a.get("date") == "2026-07-26")
    ok &= check("상태 코드 done", a.get("status") == "done")
tr = json.loads(seen[1]["messages"][-1]["content"][0]["content"])
ok &= check("_action은 모델에 노출 안 됨", "_action" not in tr, str(tr)[:80])
ok &= check("도구 결과에 바뀐 항목 포함", tr.get("항목") == "감자 싹 틔우기")

print("  -- 목록에 없는 번호")
bad = Msg("tool_use", [Blk(type="tool_use", id="c2", name="set_checklist_status",
                          input={"item_id": 9, "status": "완료"})])
frames, seen = run_ctx([([], bad), (["그 항목은 없어요."], Msg("end_turn", []))], CHECK_CTX)
ok &= check("action 프레임 없음", not [d for e, d in frames if e == "action"])
tr = json.loads(seen[1]["messages"][-1]["content"][0]["content"])
ok &= check("가능한 항목을 되돌려줌", "가능한항목" in tr and len(tr["가능한항목"]) == 2)

print("  -- 체크리스트가 아예 없을 때")
frames, seen = run_ctx([([], call), (["체크리스트가 없어요."], Msg("end_turn", []))], CTX)
ok &= check("action 프레임 없음", not [d for e, d in frames if e == "action"])
tr = json.loads(seen[1]["messages"][-1]["content"][0]["content"])
ok &= check("안내 메시지 반환", "변경실패" in tr)

print("  -- 화면 맥락에 번호 매겨 노출")
ctx_text = cs.render_context(CHECK_CTX)
ok &= check("맥락에 번호+상태 표기",
            "2. [시작 전] (2026-07-26) 감자 싹 틔우기" in ctx_text, ctx_text.splitlines()[-1])

print("\n결과:", "전부 통과" if ok else "실패 있음")
sys.exit(0 if ok else 1)
