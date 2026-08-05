# -*- coding: utf-8 -*-
"""POST /api/chat — 귀농 상담 챗봇 (backend/chat_server.py의 chat_turn 재사용).

로컬 서버(8003)와 다른 점 하나:
  로컬은 SSE 프레임을 직접 chunked 인코딩해서 흘려보낸다. Vercel 함수 런타임은 전송
  인코딩을 자체적으로 처리하므로 여기서 chunk 헤더를 붙이면 응답이 깨진다. 대신
  Content-Length 를 빼고 프레임마다 flush 해서 **생기는 대로** 내려보낸다.

  ⚠️ 예전에는 프레임을 전부 모아 마지막에 한 번에 보냈다. 답변이 다 만들어질 때까지
     화면에 아무것도 뜨지 않아서, 사용자에게는 "챗봇이 한참 뒤에야 답한다"로 보였다.
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(ROOT, "backend"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def load_chat_server():
    """chat_server는 모듈 레벨에서 anthropic.Anthropic()을 만든다 - 키가 없으면
    import 단계에서 터진다. 그래서 키를 확인한 뒤에야 지연 import한다."""
    import chat_server as cs  # noqa: PLC0415
    return cs


class handler(BaseHTTPRequestHandler):
    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        return self._json(200, {
            "ok": True,
            "api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "note": "POST /api/chat 으로 호출하세요",
        })

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except (ValueError, UnicodeDecodeError) as e:
            return self._json(400, {"error": f"본문 파싱 실패: {e}"})

        message = (req.get("message") or "").strip()
        if not message:
            return self._json(400, {"error": "message가 비어 있어요."})
        if len(message) > 2000:
            return self._json(400, {"error": "질문이 너무 길어요(2000자 이내)."})
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return self._json(500, {"error": "ANTHROPIC_API_KEY 환경변수가 없습니다."})
        try:
            cs = load_chat_server()
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"error": f"챗봇 모듈 로드 실패: {e}"})

        session = req.get("session_id") or "anon"
        # 서버리스에서는 실제 클라이언트 IP가 x-forwarded-for 첫 항목에 온다.
        fwd = self.headers.get("x-forwarded-for") or ""
        ip = fwd.split(",")[0].strip() or "unknown"

        # ⚠️ 인메모리 제한이므로 함수 인스턴스마다 따로 센다. DB.md §4.8의
        #    bump_rate_limit RPC로 옮기는 것이 다음 단계다.
        code, info = cs.check_limits(session, ip)
        if code:
            return self._json(429, {"error": info, "code": code})
        turn = info

        # ── 프레임을 만드는 대로 흘려보낸다 ─────────────────────────────────
        # 예전에는 프레임을 전부 모아 마지막에 한 번에 보냈다. 그래서 답변이 다 만들어질
        # 때까지 화면에 **아무것도 뜨지 않았다** - 30초짜리 답이면 30초 동안 빈 화면이다.
        # 로컬(8003)은 첫 토큰이 2~3초에 도착하는데 배포만 그렇지 않았던 이유가 이것이다.
        #
        # Content-Length 를 붙이지 않고 쓰는 대로 flush 하면 런타임이 전송 인코딩을
        # 알아서 처리한다. ⚠️ 직접 chunk 헤더(크기 + CRLF)를 써 넣으면 응답이 깨진다 -
        # 그건 런타임의 몫이다.
        started = {"v": False}

        def start_stream():
            if started["v"]:
                return
            started["v"] = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

        def emit(event, data):
            start_stream()
            frame = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            try:
                self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                # 사용자가 창을 닫았다. 남은 프레임은 버리고 조용히 끝낸다.
                pass

        emit("meta", {"model": cs.MODEL, "session_turn": turn})
        try:
            cs.chat_turn(message, req.get("history"), req.get("context"), session, turn, emit)
        except Exception as e:  # noqa: BLE001
            emit("error", {"code": "server", "message": f"서버 오류: {e}"})
        start_stream()   # 프레임이 하나도 없었어도 응답은 열어 준다

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass
