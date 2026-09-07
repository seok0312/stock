# -*- coding: utf-8 -*-
"""개인 로그 채널 알림 — 운영 리마인드·진단 결과 전용.

종가베팅(ALERTBOT_CHAT_ID)·업비트 상장(TELEGRAM_CHAT_ID) 채널은 다른 사람을
초대할 수 있는 공개 성격이라, 서버 정리 리마인드 같은 개인 메시지는
LOG_CHAT_ID(비공개 채널 또는 봇과의 1:1 DM)로만 보낸다.

사용:
  python3 remind.py "메시지"            # 한 건 발송
  echo "여러 줄" | python3 remind.py -   # stdin 발송

설정:
  .env 에 LOG_CHAT_ID=<chat id>. 채널이면 봇을 관리자로 추가한 뒤
  find_chat_id.py 로 id 를 얻는다. 1:1 DM 이면 봇에게 /start 를 보낸 직후
  find_chat_id.py 를 돌리면 개인 id 가 보인다.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import notify


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    argv = argv if argv is not None else sys.argv[1:]
    notify.load_env(os.path.join(HERE, ".env"),
                    os.path.abspath(os.path.join(HERE, "..", ".env")),
                    "/opt/upbit_bot/.env")
    chat = os.environ.get("LOG_CHAT_ID")
    if not chat:
        print("LOG_CHAT_ID 가 없습니다. .env 에 개인 로그 채널/DM id 를 설정하세요.\n"
              "  1) 텔레그램에서 비공개 채널 생성(예: log) 후 봇을 관리자로 추가\n"
              "  2) python3 find_chat_id.py 로 id 확인\n"
              "  3) .env 에 LOG_CHAT_ID=<id> 추가")
        return 1
    if not argv:
        print("사용: remind.py \"메시지\"  또는  remind.py -  (stdin)")
        return 1
    text = sys.stdin.read() if argv[0] == "-" else " ".join(argv)
    text = text.strip()
    if not text:
        return 1
    ok = notify.send(notify.esc(text), chat_id=chat)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
