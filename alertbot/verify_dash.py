# -*- coding: utf-8 -*-
"""검증 현황판 — 진행 중인 모든 전방검증·관찰 가설을 한 페이지로 (09-28 사용자).

출력: /var/www/html/verify.html (nginx 정적 서빙, closebet.html 과 동일 방식)
크론: 09:15(픽·예측 채점 후) · 16:20(태그 채점 후) 하루 2회 갱신.

자동 수치: tag_picks / closebet_picks / predictions / attention
수동 목록(관찰 가설·확정 룰)은 이 파일의 상수 — 검증 상태가 바뀌면 여기를 갱신.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
WEB_DIR = "/var/www/html"
OUT_DIR = WEB_DIR if os.path.isdir(WEB_DIR) else os.path.join(HERE, "out")
COST = 0.20

# ── 수동 유지 목록 ───────────────────────────────────────────────
HYPOTHESES = [
    ("조합2 (개+외+기-) D+5~10 승률형", "소표본 유보 (ETF 오염 정정 후 n=114). 전방검증 기록의 부호로 소급 채점 가능"),
    ("기관 연속매수 5일+ × 5일 보유", "1/3/10년 방향 일관(+0.44~+3.7p), 통계 확증 미달(t≈1.4) — 종가베팅과 별개 스윙 후보"),
    ("10칸 점유 9~10 × 5일 보유", "지속 매집의 다른 렌즈 — 10년 +1.10p(n=28), 익일 기준은 무의미"),
    ("오일 장중창(09→15시) IC +0.15", "통념 방향 유효하나 EWY·QQQ 통제 다변량 미검증 — 신호점수 편입 보류"),
    ("신호점수 수급 ±1 (외인 전환/동반매도)", "⚠ 2026 국면 신호 (10년 엣지 소멸, 올해만 +0.71%p) — 외인 이탈·하락장 시 제거 1순위"),
    ("조합2 D+30 엣지 +4.7p", "n=110 우측 꼬리 — 잡음 추정, 채택 안 함"),
    ("태그 판정의 개인 근사 -(외인+기관)", "실측 97.1% 일치 — 실전 오판 사례 관찰 중"),
]
RULES = [
    ("태그 1 (개+ 외- 기+)", "익일 시가 매도 — 시가 +1.04%/승률 57% (정정 후)"),
    ("태그 3 (개인 -전환 · 외인 +전환)", "보유 D+5~10 — D+5 +1.96%/54%, 알파는 D+20에서 소진"),
    ("태그 5 (개- 외+ 기-)", "종가베팅 자제 — 익일 44%"),
    ("조합 4 (개+ 외- 기-)", "매수 금지 — D+5 42%, 무태그 기본 룰(시가매도)이 커버"),
    ("타이밍", "중립=14:30 즉시 / 강세·약세=15:20 대기 / 낮약세→저녁반전 시 19:30 2차(67%)"),
    ("신호점수", "EWY ±2/±3 · QQQ ±1 · 거래대금 ±2 · 이벤트밤 -1(금리 -2) · 수급 ±1(국면)"),
    ("실행 전략", "갭(태그1) 우선 배분 → 잔여 자본 태그3 5일 보유 (09-28 확정)"),
]
BT_REF = {  # 백테스트 기준치 (비용 0.2% 차감, ETF 오염 정정 후)
    "갭": "시가 +0.84% · 승률 57%", "손바뀜": "D+5 +1.74% · 54%",
    "회피": "시가 -0.2%대 · 44%", None: "시가 -0.04% · 50% (기저)",
}


def _load(path):
    out = []
    try:
        for line in open(path, encoding="utf-8"):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except FileNotFoundError:
        pass
    return out


def _stat(vals):
    v = [x - COST for x in vals if x is not None]
    if not v:
        return "—"
    wr = sum(x > 0 for x in v) / len(v) * 100
    return f"{sum(v)/len(v):+.2f}% · {wr:.0f}% <span class=n>(n={len(v)})</span>"


def tag_section():
    recs = _load(os.path.join(DATA, "tag_picks.jsonl"))
    if not recs:
        return "<p>기록 없음</p>"
    days = sorted({r["date"] for r in recs})
    goal = (datetime.strptime(days[0], "%Y%m%d") + timedelta(days=28)).strftime("%m/%d")
    rows = []
    for tag in ("갭", "손바뀜", "회피", None):
        rs = [r for r in recs if r.get("tag") == tag]
        label = {"갭": "1 개+외-기+ (익일 매도)", "손바뀜": "3 전환·전환 (보유)",
                 "회피": "5 개-외+기- (자제)", None: "무태그 (대조군)"}[tag]
        rows.append(f"<tr><td>{label}</td><td>{len(rs)}</td>"
                    f"<td>{_stat([r.get('r_o1') for r in rs])}</td>"
                    f"<td>{_stat([r.get('r_c1') for r in rs])}</td>"
                    f"<td>{_stat([r.get('r_c5') for r in rs])}</td>"
                    f"<td>{_stat([r.get('r_c10') for r in rs])}</td>"
                    f"<td class=n>{BT_REF[tag]}</td></tr>")
    return (f"<p>기록 {days[0][4:6]}/{days[0][6:]}~ · {len(days)}일 · {len(recs)}건 · "
            f"비용 {COST}% 차감 · <b>판독 목표 {goal}</b></p>"
            "<table><tr><th>태그</th><th>건</th><th>익일시가</th><th>익일종가</th>"
            "<th>D+5</th><th>D+10</th><th>백테스트 기준</th></tr>"
            + "".join(rows) + "</table>")


def picks_section():
    recs = _load(os.path.join(DATA, "closebet_picks.jsonl"))
    evs = [(e["date"], r) for e in recs if e.get("type") == "eval" for r in e["rows"]]
    n_pick = len({r["date"] for r in recs if r.get("type") == "pick"})
    if not evs:
        return f"<p>픽 기록 {n_pick}일 · 채점 없음</p>"
    rows = []
    for grp in ("bot", "mid", "top"):
        v = [r["r_on"] for d, r in evs if r.get("grp") == grp and r.get("r_on") is not None]
        if not v:
            continue
        wr = sum(x > 0 for x in v) / len(v) * 100
        rows.append(f"<tr><td>{grp} {'(가설: 과열 하위=승자)' if grp=='bot' else ''}</td>"
                    f"<td>{len(v)}</td><td>{sum(v)/len(v):+.2f}%</td><td>{wr:.0f}%</td></tr>")
    return (f"<p>픽 {n_pick}일 기록 (09/09~) · 종가→익일시가 채점 · A/B: bot vs top</p>"
            "<table><tr><th>그룹</th><th>n</th><th>평균</th><th>승률</th></tr>"
            + "".join(rows) + "</table>")


def pred_section():
    recs = [r for r in _load(os.path.join(DATA, "predictions.jsonl"))
            if r.get("type") == "eval"]
    if not recs:
        return "<p>채점 없음</p>"
    a = sum(bool(r.get("hit_abs")) for r in recs) / len(recs) * 100
    b = sum(bool(r.get("hit_rel")) for r in recs) / len(recs) * 100
    return (f"<p>미국 섹터 → 한국 관련주 예측 · 채점 {len(recs)}건 · "
            f"절대 적중 {a:.0f}% · 코스피 대비 {b:.0f}%</p>")


def attn_section():
    d = os.path.join(DATA, "attention")
    try:
        files = sorted(f for f in os.listdir(d) if f.endswith(".jsonl"))
    except OSError:
        files = []
    if not files:
        return "<p>수집 없음</p>"
    return (f"<p>인기검색 수집 {files[0][:8][4:6]}/{files[0][6:8]}~ · {len(files)}일치 · "
            f"4주 축적 후 관심도 팩터 백테스트 재실행 예정</p>")


def decision_sections():
    """비중·타이밍 룰 전방검증 카드 (decision_log, 09-29)."""
    recs = _load(os.path.join(DATA, "decision_log.jsonl"))
    byd = {}
    for r in recs:                                # 날짜별 대표 = 마감(1530) 우선
        if r.get("t1") is None:
            continue
        if r["date"] not in byd or r.get("slot") == "1530":
            byd[r["date"]] = r
    w_html = "<p>채점된 날 없음 (매일 16:10 자동 채점)</p>"
    if byd:
        rows, cum_rule, cum_full = [], 1.0, 1.0
        for d in sorted(byd):
            r = byd[d]
            cum_rule *= 1 + (r["t1"] * r.get("weight", 100) / 100) / 100
            cum_full *= 1 + r["t1"] / 100
        for lo, hi, lab in ((3, 99, "+3↑"), (1, 2, "+1~2"), (0, 0, "0"),
                            (-2, -1, "-1~-2"), (-99, -3, "-3↓")):
            g = [r["t1"] for r in byd.values() if lo <= (r.get("score") or 0) <= hi]
            if g:
                wr = sum(x > 0 for x in g) / len(g) * 100
                rows.append(f"<tr><td>{lab}</td><td>{len(g)}</td>"
                            f"<td>{sum(g)/len(g):+.2f}%</td><td>{wr:.0f}%</td></tr>")
        w_html = (f"<p>{len(byd)}일 채점 · 누적: 비중 룰 {(cum_rule-1)*100:+.1f}% vs "
                  f"고정 100% {(cum_full-1)*100:+.1f}% <span class=n>(백테스트 113일: "
                  f"룰 +60% · MDD -3.9% vs 고정 +92% · MDD -15.8%)</span></p>"
                  "<table><tr><th>신호</th><th>n</th><th>다음날 후보 평균</th><th>승률</th></tr>"
                  + "".join(rows) + "</table>")
    tm = [r for r in recs if r.get("r_1430") is not None and r.get("kr_flow") is not None]
    t_html = "<p>채점된 날 없음</p>"
    if tm:
        rule = [r["r_1430"] if abs(r["kr_flow"]) < 0.3 else r["r_1530"] for r in tm]
        a14 = [r["r_1430"] for r in tm]
        a15 = [r["r_1530"] for r in tm]
        t_html = (f"<p>1430 권고 {len(tm)}일 채점 (EWY, 진입→익일 09시) · "
                  f"룰 추종 {sum(rule)/len(rule):+.3f}% vs 항상14:30 {sum(a14)/len(a14):+.3f}%"
                  f" vs 항상15:30 {sum(a15)/len(a15):+.3f}% "
                  f"<span class=n>(백테스트 138일: +0.649 vs +0.574 vs +0.613%)</span></p>")
    return w_html, t_html


def build():
    now = datetime.now(KST)
    hy = "".join(f"<tr><td>{h}</td><td class=n>{s}</td></tr>" for h, s in HYPOTHESES)
    ru = "".join(f"<tr><td>{k}</td><td class=n>{v}</td></tr>" for k, v in RULES)
    w_html, t_html = decision_sections()
    html = f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>검증 현황판</title><style>
:root {{ --bg:#0f1115; --card:#171a21; --tx:#d7dce3; --sub:#8b93a1; --acc:#4da3ff; --line:#262b35 }}
body {{ background:var(--bg); color:var(--tx); font-family:'Malgun Gothic',sans-serif;
       margin:0; padding:16px; font-size:14px }}
h1 {{ font-size:18px; margin:4px 0 2px }} h2 {{ font-size:15px; color:var(--acc); margin:0 0 8px }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
         padding:14px 16px; margin:12px 0 }}
table {{ border-collapse:collapse; width:100%; margin-top:6px }}
td,th {{ border-bottom:1px solid var(--line); padding:5px 8px; text-align:left; vertical-align:top }}
th {{ color:var(--sub); font-weight:normal; font-size:12px }}
.n {{ color:var(--sub); font-size:12px }} p {{ margin:4px 0 }}
.warn {{ color:#ffb454 }}
@media (prefers-color-scheme: light) {{ :root {{ --bg:#f5f6f8; --card:#fff; --tx:#222;
  --sub:#667; --line:#e2e5ea }} }}
</style></head><body>
<h1>🔬 검증 현황판</h1>
<p class=n>갱신 {now:%m/%d %H:%M} KST · 하루 2회 자동(09:15·16:20) · 상세 근거는 HANDOFF.md</p>

<div class=card><h2>① 태그 전략 전방검증 <span class=n>(tag_track, 실전 잠정치 판정)</span></h2>
{tag_section()}</div>

<div class=card><h2>② 종가베팅 픽 A/B <span class=n>(closebet_picks)</span></h2>
{picks_section()}</div>

<div class=card><h2>③ 미국→한국 예측 적중률 <span class=n>(predictions)</span></h2>
{pred_section()}</div>

<div class=card><h2>④ 관심도 팩터 데이터 축적 <span class=n>(attention)</span></h2>
{attn_section()}</div>

<div class=card><h2>⑤ 비중 룰 추적 <span class=n>(신호점수 → 비중, decision_log)</span></h2>
{w_html}</div>

<div class=card><h2>⑥ 타이밍 룰 추적 <span class=n>(중립=14:30 빨리 / 그 외=15:20)</span></h2>
{t_html}</div>

<div class=card><h2>⑦ 관찰 등급 가설 <span class=n>(채택 보류 — 조건 충족 시 재검증)</span></h2>
<table>{hy}</table></div>

<div class=card><h2>⑧ 확정 룰 스냅샷</h2>
<table>{ru}</table></div>

<p class=n>종가베팅 대시보드: <a href="closebet.html" style="color:var(--acc)">closebet.html</a></p>
</body></html>"""
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "verify.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[{now:%m/%d %H:%M}] 검증 현황판 → {path}")


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    build()
