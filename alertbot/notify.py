# -*- coding: utf-8 -*-
"""텔레그램 발송 — 4096자 분할, HTML 이스케이프.

길이 규칙(실측 확인):
  · 한도는 '문자 수'가 아니라 보이는 텍스트의 UTF-16 코드유닛 4096개.
  · <b>, <a href="..."> 같은 태그와 URL은 한도에 포함되지 않는다.
  · 이모지(BMP 밖)는 1글자가 2유닛. 📈 4096개는 통과, 4097개는 거부됨.
  · 원시 '<' 는 <code> 안에서도 파싱 에러 → 반드시 이스케이프.
"""
from __future__ import annotations

import html
import os
import re

import requests

TG_API = "https://api.telegram.org/bot{token}/sendMessage"
LIMIT = 4000                      # 4096에 여유
_TAG = re.compile(r"<[^>]+>")


def load_env(*paths):
    for p in paths:
        if p and os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())


def esc(s) -> str:
    """본문 값 이스케이프. '<'는 필수, '&'도 엔티티 오인 방지를 위해 변환."""
    return html.escape(str(s), quote=False)


def visible_len(text: str) -> int:
    """텔레그램이 세는 길이 = 태그 제외 텍스트의 UTF-16 코드유닛 수."""
    return len(_TAG.sub("", text).encode("utf-16-le")) // 2


def _chunks(text: str, limit: int = LIMIT):
    out, buf, buf_len = [], "", 0
    for line in text.split("\n"):
        ln = visible_len(line)
        while ln > limit:
            out.append(line[:limit])
            line = line[limit:]
            ln = visible_len(line)
        if buf and buf_len + ln + 1 > limit:
            out.append(buf)
            buf, buf_len = line, ln
        else:
            buf = (buf + "\n" + line) if buf else line
            buf_len = buf_len + ln + 1 if buf_len else ln
    if buf:
        out.append(buf)
    return out


def send(text: str, token: str | None = None, chat_id: str | None = None,
         dry_run: bool = False) -> bool:
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    # 전용 채널을 쓰고 싶으면 ALERTBOT_CHAT_ID 를 지정. 없으면 기존 채널로.
    chat_id = (chat_id or os.environ.get("ALERTBOT_CHAT_ID")
               or os.environ.get("TELEGRAM_CHAT_ID", ""))
    parts = _chunks(text)
    if dry_run or not token or not chat_id:
        tag = "[DRY-RUN]" if dry_run else "[미설정 - 콘솔 출력]"
        print(f"{tag} {len(parts)}개 메시지")
        for i, p in enumerate(parts, 1):
            print(f"\n----- 메시지 {i}/{len(parts)} "
                  f"(원문 {len(p)}자 / 보이는길이 {visible_len(p)}) -----\n{p}")
        return True
    ok = True
    for p in parts:
        try:
            r = requests.post(TG_API.format(token=token),
                              json={"chat_id": chat_id, "text": p,
                                    "parse_mode": "HTML",
                                    "disable_web_page_preview": True},
                              timeout=15)
            if r.status_code != 200:
                print(f"  텔레그램 실패 {r.status_code}: {r.text[:200]}")
                ok = False
        except Exception as e:
            print(f"  텔레그램 예외: {e}")
            ok = False
    return ok
