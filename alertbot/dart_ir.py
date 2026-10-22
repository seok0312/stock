# -*- coding: utf-8 -*-
"""IR 일정 공시 감시 (10-22 사용자: 삼전·하이닉스 실적 일정의 공식 소스 확보).

배경: 실적 '확정 일정'을 주는 무료 자동화 소스가 사실상 없음 — investing(봇
차단 403), 네이버 캘린더(비공개), KIND(비정형), 회사 IR 페이지(403/동적),
DART 무키 RSS(폐지). 유일한 공식 루트 = OpenDART 공시검색 API(무료 키).

동작: 삼성전자·SK하이닉스의 최근 3주 공시 중 '기업설명회(IR) 개최' 류가 새로
뜨면 텔레그램으로 공시 링크를 알림 → events.KR_EARN_EXACT 갱신을 촉구.
(개최 일시 자동 파싱은 공시 본문 열람이 필요해 반자동 운용 — 10-22 설계.)

활성화: opendart.fss.or.kr 에서 무료 API 키 발급(이메일 인증, 2분) 후
/opt/alertbot/.env 에 `DART_API_KEY=...` 추가. 키 없으면 조용히 종료.
크론: 평일 09:35 KST (1일 1회면 충분 — IR 공시는 발표 1~2주 전에 뜬다).
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "data", "dart_ir.json")
CORP = {"00126380": "삼성전자", "00164779": "SK하이닉스"}


def main() -> int:
    sys.path.insert(0, HERE)
    import notify
    notify.load_env(os.path.join(HERE, ".env"))
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        print("[dart_ir] DART_API_KEY 없음 — 건너뜀 (opendart.fss.or.kr 키 발급 후 .env 추가)")
        return 0
    try:
        seen = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        seen = {}
    now = datetime.now(KST)
    new = []
    for cc, nm in CORP.items():
        try:
            j = requests.get("https://opendart.fss.or.kr/api/list.json",
                             params={"crtfc_key": key, "corp_code": cc,
                                     "bgn_de": (now - timedelta(days=21)).strftime("%Y%m%d"),
                                     "end_de": now.strftime("%Y%m%d"),
                                     "page_count": 100}, timeout=12).json()
        except Exception as e:
            print(f"[dart_ir] {nm} 조회 실패: {str(e)[:80]}")
            continue
        if j.get("status") not in ("000", None):
            print(f"[dart_ir] {nm} API 응답: {j.get('status')} {j.get('message')}")
            continue
        for r in j.get("list") or []:
            title = r.get("report_nm") or ""
            if "기업설명회" not in title and "IR" not in title.upper():
                continue
            rid = r.get("rcept_no")
            if not rid or rid in seen:
                continue
            seen[rid] = now.strftime("%m/%d")
            new.append(f"{nm} · {r.get('rcept_dt')} 접수 · {title}\n"
                       f"  https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rid}")
    if new:
        msg = ("📅 <b>IR 일정 공시 감지</b> — 발표일 확인 후 KR_EARN_EXACT 갱신 필요\n"
               + "\n".join(notify.esc(x) for x in new))
        notify.send(msg, allow_muted=True, dry_run="--dry-run" in sys.argv)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(dict(sorted(seen.items())[-200:]),
              open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"[dart_ir] 신규 IR 공시 {len(new)}건")
    return len(new)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
