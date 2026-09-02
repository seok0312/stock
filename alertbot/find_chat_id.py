# -*- coding: utf-8 -*-
"""텔레그램 chat_id 찾기 도우미 (읽기 전용 — 메시지를 보내지 않는다).

사용법:
  1) 텔레그램에서 새 채널(또는 그룹)을 만든다
  2) 그 채널에 봇을 '관리자'로 추가한다  ← 채널은 반드시 관리자여야 봇이 글을 쓸 수 있다
  3) 그 채널에 아무 메시지나 하나 남긴다 (봇이 채널을 인식하도록)
  4) py -3.11 find_chat_id.py

주의: getUpdates 는 웹훅이 설정돼 있으면 빈 결과를 준다.
      또 이미 다른 프로세스가 getUpdates 로 소비한 업데이트는 다시 안 나온다.
      결과가 비면 채널에 메시지를 새로 남기고 다시 실행할 것.
"""
from __future__ import annotations

import os
import sys

import requests

from notify import load_env

HERE = os.path.dirname(os.path.abspath(__file__))

ENV_PATHS = (
    os.path.join(HERE, ".env"),
    os.path.join(HERE, "..", ".env"),
    os.path.join(HERE, "..", "..", "upbit_bot", ".env"),   # 로컬: 텔레그램 토큰이 여기 있다
    "/opt/upbit_bot/.env",                                  # 서버
)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    load_env(*ENV_PATHS)
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not tok:
        print("TELEGRAM_BOT_TOKEN 이 없습니다."); return 1

    me = requests.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=15).json()
    if not me.get("ok"):
        print("봇 토큰이 유효하지 않습니다:", me); return 1
    b = me["result"]
    print(f"봇: @{b.get('username')} ({b.get('first_name')})\n")

    wh = requests.get(f"https://api.telegram.org/bot{tok}/getWebhookInfo", timeout=15).json()
    url = (wh.get("result") or {}).get("url") or ""
    if url:
        print(f"⚠ 웹훅이 설정돼 있어 getUpdates 가 비어 나옵니다: {url}")
        print("  → 아래 '수동 확인' 방법을 쓰세요.\n")

    r = requests.get(f"https://api.telegram.org/bot{tok}/getUpdates",
                     params={"limit": 100}, timeout=20).json()
    seen = {}
    for u in r.get("result", []):
        for key in ("message", "channel_post", "edited_channel_post", "my_chat_member"):
            m = u.get(key)
            if not m:
                continue
            c = m.get("chat") or {}
            if c.get("id") is not None:
                seen[c["id"]] = (c.get("type"), c.get("title") or c.get("username")
                                 or c.get("first_name") or "")
    if seen:
        print("발견된 대화방:")
        for cid, (typ, title) in seen.items():
            print(f"   chat_id = {cid}\n      종류 {typ} · 이름 {title}")
    else:
        print("발견된 대화방이 없습니다.")
        print("  채널/그룹에 메시지를 하나 남긴 뒤 다시 실행하거나, 아래 수동 방법을 쓰세요.")

    print("\n[수동 확인]")
    print("  · 공개 채널이면  chat_id 대신  @채널유저명  을 그대로 써도 된다")
    print("  · 비공개 채널은 웹 텔레그램(web.telegram.org)에서 채널을 열면 URL이")
    print("    .../#-1001234567890 형태 → 그 숫자가 chat_id (앞의 -100 포함)")
    print("\n[설정]  서버 /opt/upbit_bot/.env 에 아래 한 줄 추가")
    print("  ALERTBOT_CHAT_ID=<위에서 찾은 값>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
