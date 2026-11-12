"""出力: Excel（①〜⑥のシート）/ CSV / Markdownサマリー / 時系列履歴"""
from __future__ import annotations

import csv
import os
import statistics
from collections import defaultdict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .collector import CLASS_OWN_OTHER, StoreResult
from .records import CLASS_DIRECT, CLASS_INDIRECT, CLASS_OWN, JobRow

CLASS_ORDER = {CLASS_OWN: 0, CLASS_OWN_OTHER: 1, CLASS_DIRECT: 2, CLASS_INDIRECT: 3}


def hourly_values(rows: list[JobRow]) -> list[float]:
    """集計用の時給。同じ企業・同じ職種名・同じ時給の求人（派遣会社の大量掲載など）は1件として数える"""
    seen, out = set(), []
    for r in rows:
        if not r.hourly_min:
            continue
        key = (r.company, r.occupation, r.hourly_min)
        if key in seen:
            continue
        seen.add(key)
        out.append(r.hourly_min)
    return out


def is_dispatch(r) -> bool:
    """派遣（紹介予定派遣を含む）の求人か。マイナビバイトの雇用形態「派遣社員」「紹介予定派遣」で判定"""
    return "派遣" in (getattr(r, "employment", "") or "")


def without_dispatch(rows):
    return [r for r in rows if not is_dispatch(r)]


def stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    v = sorted(vals)
    q = statistics.quantiles(v, n=4) if len(v) >= 2 else [v[0], v[0], v[0]]
    return {"n": len(v), "min": v[0], "p25": q[0], "median": statistics.median(v), "mean": statistics.fmean(v), "p75": q[2], "max": v[-1]}


def _yen(x) -> str:
    return "-" if x is None else f"{x:,.0f}円"


def by_class(rows: list[JobRow]) -> dict[str, list[JobRow]]:
    d = defaultdict(list)
    for r in rows:
        d[r.classification].append(r)
    return d


def company_counts(rows: list[JobRow]) -> list[dict]:
    g: dict[tuple, list[JobRow]] = defaultdict(list)
    for r in rows:
        g[(r.classification, r.company, r.site)].append(r)
    out = []
    for (cls, comp, site), rs in g.items():
        hv = hourly_values(rs)
        out.append({
            "分類": cls, "企業": comp, "求人サイト": site, "求人数(取得分)": len(rs),
            "店舗/勤務地例": " / ".join(dict.fromkeys(r.shop for r in rs))[:120],
            "最短距離km": min((r.distance_km for r in rs if r.distance_km is not None), default=None),
            "時給下限_最小": min(hv) if hv else None, "時給下限_最大": max(hv) if hv else None,
        })
    out.sort(key=lambda x: (CLASS_ORDER.get(x["分類"], 9), -x["求人数(取得分)"], x["最短距離km"] or 99))
    return out


# ---------------- Excel ----------------
ROW_COLUMNS = [
    ("classification", "分類"), ("distance_km", "距離km"), ("company", "企業"), ("shop", "店舗・勤務地名"),
    ("occupation", "職種名"), ("category", "職種分類"), ("employment", "雇用形態"), ("wage_type", "給与種別"),
    ("wage_min", "給与下限"), ("wage_max", "給与上限"), ("wage_text", "給与表記"), ("address", "所在地"),
    ("station", "最寄駅"), ("location_method", "位置の根拠"), ("site", "求人サイト"), ("url", "求人URL"),
    ("publication_start", "掲載開始"), ("publication_end", "掲載終了"), ("matched_by", "分類根拠"),
]
HEAD_FILL = PatternFill("solid", fgColor="1F4E78")
HEAD_FONT = Font(color="FFFFFF", bold=True)


def _sheet(wb, title: str, header: list[str], data: list[list], widths: dict[int, int] | None = None, wrap_cols=()):
    ws = wb.create_sheet(title)
    ws.append(header)
    for c in ws[1]:
        c.fill, c.font = HEAD_FILL, HEAD_FONT
    for row in data:
        ws.append(row)
    ws.freeze_panes = "A2"
    if data:
        ws.auto_filter.ref = ws.dimensions
    for i, h in enumerate(header, 1):
        w = (widths or {}).get(i) or min(max(len(str(h)) * 2 + 2, 10), 40)
        ws.column_dimensions[get_column_letter(i)].width = w
    for col in wrap_cols:
        for cell in ws[get_column_letter(col)][1:]:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    return ws


def _row_values(r: JobRow) -> list:
    d = r.to_dict()
    return [d[k] for k, _ in ROW_COLUMNS]


BENEFIT_COLUMNS = [
    ("class", "区分"), ("company", "企業"), ("store", "店舗"), ("address_or_station", "所在地・最寄駅"), ("distance_km", "距離km"),
    ("wage_text", "時給"), ("transport", "交通費"), ("raise", "昇給"), ("promotion", "社員登用"), ("discount", "社員割引"),
    ("social_insurance", "社会保険"), ("training", "研修"), ("allowances", "加算・手当"), ("features", "その他の特徴"),
    ("source_site", "情報源"), ("source_url", "URL"), ("source_date", "掲載・更新日"),
]


def benefit_rows(res) -> list[dict]:
    """①自店と③直接競合の待遇比較の行（求人データ由来＋Web調査があればその分）"""
    out = []
    for r in res.rows:
        if r.classification not in (CLASS_OWN, CLASS_DIRECT) or r.distance_km is None and r.classification != CLASS_OWN:
            continue
        b = r.benefits or {}
        if not b:
            continue
        out.append({
            "class": r.classification, "company": r.company, "store": r.shop, "address_or_station": r.station or r.address,
            "distance_km": r.distance_km, "wage_text": r.wage_text, **{k: b.get(k, "記載なし") for k in
            ("transport", "raise", "promotion", "discount", "social_insurance", "training", "allowances", "features")},
            "source_site": r.site, "source_url": r.url, "source_date": r.publication_start, "dispatch": is_dispatch(r),
        })
    for p in (res.research or {}).get("postings") or []:
        if not p.get("out_of_range"):
            out.append({"class": CLASS_DIRECT, **p})
    return sorted(out, key=lambda p: (p["class"] != CLASS_OWN, p.get("distance_km") is None, p.get("distance_km") or 0))


BENEFIT_TAGS = [("transport", "交通費支給"), ("raise", "昇給あり"), ("promotion", "社員登用あり"), ("discount", "社員割引あり"),
                ("social_insurance", "社会保険完備"), ("training", "研修制度あり")]


def benefit_rates(rows, exclude_dispatch: bool = False) -> list[dict]:
    """②③それぞれで、各待遇を明記している求人の割合"""
    if exclude_dispatch:
        rows = without_dispatch(rows)
    out = []
    for cls in (CLASS_DIRECT, CLASS_INDIRECT):
        rs = [r for r in rows if r.classification == cls and r.benefits]
        if not rs:
            continue
        for k, label in BENEFIT_TAGS:
            n = sum(1 for r in rs if (r.benefits.get(k) or "記載なし") != "記載なし")
            out.append({"class": cls, "item": label, "n": n, "total": len(rs), "rate": round(n / len(rs), 3)})
    return out


def write_excel(res: StoreResult, path: str, run_date: str):
    rows = sorted(res.rows, key=lambda r: (CLASS_ORDER.get(r.classification, 9), r.distance_km if r.distance_km is not None else 99))
    cls = by_class(rows)
    wb = Workbook()
    ws = wb.active
    ws.title = "サマリー"
    st = res.store
    ws.append([f"時給調査レポート：{st['name']}", ""])
    ws["A1"].font = Font(size=14, bold=True)
    ws.append(["調査日", run_date])
    ws.append(["所在地", st.get("address", "")])
    ws.append(["座標", f"{st['lat']}, {st['lng']}"])
    ws.append(["間接競合の半径", f"{st.get('indirect_radius_km', 5)} km"])
    ws.append(["直接競合の半径", f"{st.get('direct_radius_km', 5)} km"])
    ws.append([])
    ws.append(["区分", "件数(時給)", "最小", "25%", "中央値", "平均", "75%", "最大"])
    for c in ws[ws.max_row]:
        c.fill, c.font = HEAD_FILL, HEAD_FONT
    for k in (CLASS_OWN, CLASS_DIRECT, CLASS_INDIRECT):
        s = stats(hourly_values(cls.get(k, [])))
        ws.append([k, s["n"]] + [round(s[x]) if s["n"] else None for x in ("min", "p25", "median", "mean", "p75", "max")])
    for k in (CLASS_DIRECT, CLASS_INDIRECT):
        s = stats(hourly_values(without_dispatch(cls.get(k, []))))
        ws.append([f"{k}（派遣を除く）", s["n"]] + [round(s[x]) if s["n"] else None for x in ("min", "p25", "median", "mean", "p75", "max")])
    ws.append([])
    ws.append(["※時給は求人の「下限額」で集計（日給・月給は勤務時間不明のため除外）。同じ企業・職種・時給の求人は1件として集計。"])
    ws.append(["※「派遣を除く」は雇用形態が派遣社員・紹介予定派遣の求人を除いた集計。各シートの「雇用形態」列で絞り込めます。"])
    ws.append(["※マイナビバイトは robots.txt で2ページ目以降の取得が禁止されているため、各検索条件の先頭30件のみ取得。件数は ⑥ のエリア×職種別シートが正確な掲載数。"])
    ws.append([f"※位置不明で除外: {res.unlocated} 件 / リクエスト数: {res.requests}" + (" / 予算上限で一部未取得" if res.budget_exhausted else "")])
    ws.column_dimensions["A"].width = 24
    for c in "BCDEFGH":
        ws.column_dimensions[c].width = 14

    header = [h for _, h in ROW_COLUMNS]
    w = {1: 12, 2: 8, 3: 28, 4: 34, 5: 26, 6: 26, 12: 36, 13: 30, 16: 40}
    _sheet(wb, "①自店", header, [_row_values(r) for r in cls.get(CLASS_OWN, []) + cls.get(CLASS_OWN_OTHER, [])], w)
    _sheet(wb, "②間接競合", header, [_row_values(r) for r in cls.get(CLASS_INDIRECT, [])], w)
    _sheet(wb, "③直接競合", header, [_row_values(r) for r in cls.get(CLASS_DIRECT, [])], w)
    _sheet(
        wb, "①③待遇比較", [h for _, h in BENEFIT_COLUMNS],
        [[p.get(k) for k, _ in BENEFIT_COLUMNS] for p in benefit_rows(res)],
        {1: 12, 2: 20, 3: 26, 4: 24, 6: 26, 7: 22, 8: 22, 9: 18, 10: 18, 11: 18, 12: 26, 13: 26, 14: 30, 16: 40}, wrap_cols=(6, 7, 8, 9, 10, 11, 12, 13, 14),
    )
    _sheet(wb, "④一覧(距離順)", header, [_row_values(r) for r in sorted(rows, key=lambda r: r.distance_km or 0)], w)
    _sheet(
        wb, "⑤業務範囲",
        ["分類", "距離km", "企業", "店舗・勤務地名", "職種名", "職種分類", "給与表記", "仕事内容（業務範囲）", "勤務時間・シフト", "求人URL"],
        [[r.classification, r.distance_km, r.company, r.shop, r.occupation, r.category, r.wage_text, r.work_content, r.shift, r.url] for r in rows],
        {1: 12, 2: 8, 3: 26, 4: 30, 5: 24, 6: 22, 7: 18, 8: 80, 9: 40, 10: 40}, wrap_cols=(8, 9),
    )
    cc = company_counts(rows)
    _sheet(wb, "⑥求人数_企業別", list(cc[0].keys()) if cc else ["分類"], [list(x.values()) for x in cc], {2: 30, 5: 50})
    _sheet(
        wb, "⑥求人数_エリア職種別", ["取得目的", "エリア", "職種", "掲載件数", "取得件数", "検索URL"],
        [[p.purpose, p.area, p.occupation or "(全職種)", p.count, p.fetched_jobs, p.url] for p in res.pages],
        {2: 18, 3: 40, 6: 60},
    )
    wb.save(path)


def write_csv(res: StoreResult, path: str):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([h for _, h in ROW_COLUMNS] + ["仕事内容（業務範囲）"])
        for r in sorted(res.rows, key=lambda r: (CLASS_ORDER.get(r.classification, 9), r.distance_km or 0)):
            w.writerow(_row_values(r) + [r.work_content])


def _md_table(header: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if v is None else str(v).replace("|", "／").replace("\n", " ") for v in r) + " |")
    return "\n".join(out)


def _short(s: str, n: int = 60) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def write_markdown(res: StoreResult, path: str, run_date: str, files: dict[str, str]):
    st = res.store
    cls = by_class(res.rows)
    s_own, s_dir, s_ind = (stats(hourly_values(cls.get(k, []))) for k in (CLASS_OWN, CLASS_DIRECT, CLASS_INDIRECT))
    own_wage = s_own.get("median") if s_own["n"] else st.get("own_hourly_wage_manual")
    lines = [f"# 時給調査：{st['name']}（{run_date}）", "",
             "📊 **ダッシュボード：https://knoro-hue.github.io/HourlyWageSurvey/**", ""]
    lines.append(f"所在地：{st.get('address', '')}　／　間接競合 半径{st.get('indirect_radius_km', 5)}km・直接競合 半径{st.get('direct_radius_km', 5)}km")
    lines.append("")
    lines.append("## 結論")
    lines.append("")

    def gap(s):
        if not own_wage or not s["n"]:
            return "-"
        d = own_wage - s["median"]
        return f"{d:+,.0f}円"

    lines.append(_md_table(
        ["区分", "件数(時給)", "中央値", "平均", "最小〜最大", "自店との差(自店−中央値)"],
        [
            ["①自店", s_own["n"] or ("手入力" if own_wage else 0), _yen(own_wage), _yen(s_own.get("mean")), f"{_yen(s_own.get('min'))}〜{_yen(s_own.get('max'))}", "-"],
            ["③直接競合", s_dir["n"], _yen(s_dir.get("median")), _yen(s_dir.get("mean")), f"{_yen(s_dir.get('min'))}〜{_yen(s_dir.get('max'))}", gap(s_dir)],
            ["②間接競合", s_ind["n"], _yen(s_ind.get("median")), _yen(s_ind.get("mean")), f"{_yen(s_ind.get('min'))}〜{_yen(s_ind.get('max'))}", gap(s_ind)],
            *[[f"{k}（派遣を除く）", x["n"], _yen(x.get("median")), _yen(x.get("mean")), f"{_yen(x.get('min'))}〜{_yen(x.get('max'))}", gap(x)]
              for k, x in (("③直接競合", stats(hourly_values(without_dispatch(cls.get(CLASS_DIRECT, []))))),
                           ("②間接競合", stats(hourly_values(without_dispatch(cls.get(CLASS_INDIRECT, []))))))],
        ],
    ))
    if not s_own["n"] and not own_wage:
        lines += ["", "> ①自店の求人は自動取得対象サイトで見つかりませんでした。社内の時給を使う場合は `config/stores.yaml` の `own_hourly_wage_manual` を設定してください。"]
    lines += ["", "## ③直接競合（距離順）", ""]
    d = sorted(cls.get(CLASS_DIRECT, []), key=lambda r: r.distance_km or 0)
    lines.append(_md_table(["距離km", "企業", "店舗", "職種", "給与", "業務範囲(抜粋)", "サイト"],
                           [[r.distance_km, _short(r.company, 20), _short(r.shop, 26), _short(r.occupation, 18), r.wage_text, _short(r.work_content, 50), f"[{r.site}]({r.url})" if r.url else r.site] for r in d]) if d else "該当なし")
    lines += ["", "## ②間接競合：職種分類別の時給", ""]
    g = defaultdict(list)
    for r in cls.get(CLASS_INDIRECT, []):
        g[(r.category or "(分類なし)").split(" > ")[-1]].append(r)
    tb = []
    for k, rs in g.items():
        s = stats(hourly_values(rs))
        tb.append([k, len(rs), s["n"], _yen(s.get("median")), _yen(s.get("max"))])
    tb.sort(key=lambda x: -x[1])
    lines.append(_md_table(["職種分類", "求人数", "うち時給", "時給中央値", "時給最大"], tb) if tb else "該当なし")
    lines += ["", "## ②間接競合：近い順 上位20件", ""]
    near = sorted(cls.get(CLASS_INDIRECT, []), key=lambda r: r.distance_km or 0)[:20]
    lines.append(_md_table(["距離km", "企業/店舗", "職種", "給与", "業務範囲(抜粋)"],
                           [[r.distance_km, _short(f"{r.shop or r.company}", 30), _short(r.occupation, 18), r.wage_text, _short(r.work_content, 50)] for r in near]) if near else "該当なし")
    lines += ["", "## ⑥求人数（企業別・上位15）", ""]
    cc = company_counts(res.rows)[:15]
    lines.append(_md_table(["分類", "企業", "求人サイト", "求人数", "最短km", "時給下限レンジ"],
                           [[c["分類"], _short(c["企業"], 26), c["求人サイト"], c["求人数(取得分)"], c["最短距離km"], f"{_yen(c['時給下限_最小'])}〜{_yen(c['時給下限_最大'])}"] for c in cc]) if cc else "該当なし")
    lines += ["", "## ⑥掲載件数（エリア全体）", ""]
    tops = [p for p in res.pages if p.purpose == "エリア全体"]
    lines.append(_md_table(["エリア", "マイナビバイト掲載件数"], [[p.area, p.count] for p in tops]) if tops else "-")
    lines += ["", "## ファイル", ""]
    for k, v in files.items():
        lines.append(f"- {k}: [{os.path.basename(v)}]({os.path.basename(v)})")
    lines += ["", "## 注記", "",
              "- 時給は各求人の下限額で集計。日給・月給の求人は時給集計から除外（一覧には掲載）。",
              "- マイナビバイトは robots.txt に従い各検索条件の1ページ目（最大30件）のみ取得。エリア×職種に分割して網羅性を確保。",
              "- 派遣・紹介会社の求人は本社住所ではなく「勤務地」表記・最寄駅から位置を推定（⑤/④シートの「位置の根拠」参照）。",
              f"- 位置不明で除外：{res.unlocated}件　／　リクエスト数：{res.requests}" + ("　／　リクエスト上限に達したため一部未取得" if res.budget_exhausted else ""),
              ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def append_history(res: StoreResult, path: str, run_date: str):
    cls = by_class(res.rows)
    s = {k: stats(hourly_values(cls.get(k, []))) for k in (CLASS_OWN, CLASS_DIRECT, CLASS_INDIRECT)}
    own = s[CLASS_OWN].get("median") or res.store.get("own_hourly_wage_manual")
    row = {
        "date": run_date, "store_id": res.store["id"], "own_hourly": own,
        "direct_n": s[CLASS_DIRECT]["n"], "direct_median": s[CLASS_DIRECT].get("median"), "direct_max": s[CLASS_DIRECT].get("max"),
        "indirect_n": s[CLASS_INDIRECT]["n"], "indirect_median": s[CLASS_INDIRECT].get("median"), "indirect_p75": s[CLASS_INDIRECT].get("p75"),
        "area_total_postings": sum(p.count or 0 for p in res.pages if p.purpose == "エリア全体"),
    }
    nd = {k: stats(hourly_values(without_dispatch(cls.get(k, [])))) for k in (CLASS_DIRECT, CLASS_INDIRECT)}
    row.update({"direct_median_nd": nd[CLASS_DIRECT].get("median"), "indirect_median_nd": nd[CLASS_INDIRECT].get("median")})
    exists = os.path.exists(path)
    keep = []
    if exists:  # 同日再実行は上書き
        with open(path, encoding="utf-8") as f:
            keep = [r for r in csv.DictReader(f) if r.get("date") != run_date]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        w.writeheader()
        for r in keep:
            w.writerow({k: r.get(k) for k in row})
        w.writerow(row)
