"""GitHub Pages 用のダッシュボード（docs/）を出力する

docs/index.html             店舗一覧
docs/<店舗ID>/index.html    店舗ダッシュボード（テンプレートをコピー）
docs/<店舗ID>/data.json     ダッシュボードのデータ（毎回上書き）
"""
from __future__ import annotations

import csv
import html
import json
import os
import shutil
from collections import defaultdict

from .collector import CLASS_OWN_OTHER, StoreResult
from .records import CLASS_DIRECT, CLASS_INDIRECT, CLASS_OWN
from .report import BENEFIT_COLUMNS, benefit_rates, benefit_rows, hourly_values, is_dispatch, stats, without_dispatch

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
COLUMNS = ["class", "distance_km", "company", "shop", "occupation", "category", "wage_type",
           "wage_text", "hourly", "station", "location_basis", "site", "url", "work_content", "benefits",
           "posted_date", "employment", "dispatch"]
BENEFIT_LABELS = {"transport": "交通費", "raise": "昇給", "promotion": "社員登用", "discount": "社員割引",
                  "social_insurance": "社会保険", "training": "研修", "allowances": "加算・手当", "features": "その他"}


def _benefit_text(b: dict | None) -> str:
    if not b:
        return ""
    return "／".join(f"{BENEFIT_LABELS[k]}:{v}" for k, v in b.items() if k in BENEFIT_LABELS and v and v != "記載なし")


def _short_category(c: str) -> str:
    return (c or "(分類なし)").split(" > ")[-1]


def build_data(res: StoreResult, run_date: str, history_path: str) -> dict:
    st = res.store
    by = defaultdict(list)
    for r in res.rows:
        by[r.classification].append(r)
    summary, summary_nd = {}, {}
    for key, cls in (("own", CLASS_OWN), ("direct", CLASS_DIRECT), ("indirect", CLASS_INDIRECT)):
        s = stats(hourly_values(by.get(cls, [])))
        summary[key] = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in s.items()}
        s = stats(hourly_values(without_dispatch(by.get(cls, []))))
        summary_nd[key] = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in s.items()}
    if not summary["own"]["n"] and st.get("own_hourly_wage_manual"):
        v = float(st["own_hourly_wage_manual"])
        summary["own"] = {"n": 1, "min": v, "p25": v, "median": v, "mean": v, "p75": v, "max": v, "manual": True}
        summary_nd["own"] = summary["own"]
    rows = []
    for r in sorted(res.rows, key=lambda r: (r.distance_km if r.distance_km is not None else 99)):
        rows.append([
            r.classification, r.distance_km, r.company, r.shop, r.occupation, _short_category(r.category),
            r.wage_type, r.wage_text, r.hourly_min, r.station, r.location_method, r.site, r.url,
            " ".join((r.work_content or "").split())[:160], _benefit_text(r.benefits), r.publication_start,
            r.employment, 1 if is_dispatch(r) else 0,
        ])
    areas = [{"area": p.area.replace("大阪市", ""), "count": p.count} for p in res.pages if p.purpose == "エリア全体" and p.count is not None]
    history = []
    if os.path.exists(history_path):
        with open(history_path, encoding="utf-8") as f:
            history = list(csv.DictReader(f))
    return {
        "store": {k: st.get(k) for k in ("id", "name", "address", "lat", "lng", "indirect_radius_km", "direct_radius_km")},
        "run_date": run_date,
        "summary": summary,
        "summary_nd": summary_nd,
        "columns": COLUMNS,
        "rows": rows,
        "areas": areas,
        "benefits": [{k: p.get(k) for k, _ in BENEFIT_COLUMNS} for p in benefit_rows(res)],
        "benefit_rates": benefit_rates(res.rows),
        "benefit_rates_nd": benefit_rates(res.rows, exclude_dispatch=True),
        "research": {k: (res.research or {}).get(k) for k in ("researched_at", "model", "notes")} if res.research else None,
        "history": history,
        "notes": {
            "unlocated": res.unlocated,
            "requests": res.requests,
            "budget_exhausted": res.budget_exhausted,
            "own_other": len(by.get(CLASS_OWN_OTHER, [])),
        },
        "files": {
            "excel": f"reports/{st['id']}/{run_date}/{st['id']}_{run_date}.xlsx",
            "csv": f"reports/{st['id']}/{run_date}/{st['id']}_{run_date}_jobs.csv",
            "summary": f"reports/{st['id']}/latest.md",
        },
    }


def write_dashboard(res: StoreResult, run_date: str, history_path: str, docs_dir: str = "docs", repo: str = ""):
    sid = res.store["id"]
    out = os.path.join(docs_dir, sid)
    os.makedirs(out, exist_ok=True)
    data = build_data(res, run_date, history_path)
    data["repo"] = repo
    with open(os.path.join(out, "data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    shutil.copyfile(os.path.join(TEMPLATE_DIR, "dashboard.html"), os.path.join(out, "index.html"))
    os.makedirs(os.path.join(docs_dir, "assets"), exist_ok=True)
    shutil.copyfile(os.path.join(TEMPLATE_DIR, "bg.svg"), os.path.join(docs_dir, "assets", "bg.svg"))
    open(os.path.join(docs_dir, ".nojekyll"), "w").close()
    write_index(docs_dir)


_REDIRECT = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="0; url={to}"><link rel="canonical" href="{to}"><title>移動しました</title></head>
<body><p><a href="{to}">時給調査ダッシュボードはこちら</a></p></body></html>
"""


def write_legacy_redirects(docs_dir: str, ids: list[str]):
    """公開元が docs フォルダの場合、旧URL（…/docs/…）からの転送ページを置く"""
    base = os.path.join(docs_dir, "docs")
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, "index.html"), "w", encoding="utf-8") as f:
        f.write(_REDIRECT.format(to="../"))
    for sid in ids:
        d = os.path.join(base, sid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
            f.write(_REDIRECT.format(to=f"../../{sid}/"))


def write_index(docs_dir: str = "docs", stores_path: str = "config/stores.yaml"):
    """メイン画面（全店一覧）用の docs/stores.json と docs/index.html を作る"""
    import yaml

    cfg_stores = []
    if os.path.exists(stores_path):
        with open(stores_path, encoding="utf-8") as f:
            cfg_stores = [s for s in (yaml.safe_load(f) or {}).get("stores") or [] if s.get("enabled", True)]
    entries = {}
    for s in cfg_stores:
        entries[s["id"]] = {
            "id": s["id"], "name": s["name"], "region": s.get("region", ""), "pref": s.get("prefecture_label", ""),
            "address": s.get("address", ""),
            "shard": s.get("shard"), "run_date": None, "own": None, "direct": None, "indirect": None,
            "direct_n": 0, "indirect_n": 0, "postings": None, "jobs": 0,
        }
    for sid in sorted(os.listdir(docs_dir)):
        p = os.path.join(docs_dir, sid, "data.json")
        if not os.path.isfile(p):
            continue
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        # 画面の改修を全店に反映（店舗ページの HTML は全店共通）
        shutil.copyfile(os.path.join(TEMPLATE_DIR, "dashboard.html"), os.path.join(docs_dir, sid, "index.html"))
        s = d["summary"]
        e = entries.setdefault(sid, {"id": sid, "name": d["store"]["name"], "region": "", "pref": "", "shard": None})
        snd = d.get("summary_nd") or s
        e.update({
            "own_nd": snd["own"].get("median") if snd["own"].get("n") else None,
            "direct_nd": snd["direct"].get("median") if snd["direct"].get("n") else None,
            "indirect_nd": snd["indirect"].get("median") if snd["indirect"].get("n") else None,
            "direct_n_nd": snd["direct"].get("n", 0), "indirect_n_nd": snd["indirect"].get("n", 0),
            "run_date": d["run_date"],
            "own": s["own"].get("median") if s["own"].get("n") else None,
            "direct": s["direct"].get("median") if s["direct"].get("n") else None,
            "indirect": s["indirect"].get("median") if s["indirect"].get("n") else None,
            "direct_n": s["direct"].get("n", 0), "indirect_n": s["indirect"].get("n", 0),
            "postings": sum(a.get("count") or 0 for a in d.get("areas", [])), "jobs": len(d.get("rows", [])),
        })
    with open(os.path.join(docs_dir, "stores.json"), "w", encoding="utf-8") as f:
        json.dump(list(entries.values()), f, ensure_ascii=False)
    shutil.copyfile(os.path.join(TEMPLATE_DIR, "index.html"), os.path.join(docs_dir, "index.html"))
    write_legacy_redirects(docs_dir, [e["id"] for e in entries.values()])
    tools = os.path.join(docs_dir, "tools")
    os.makedirs(tools, exist_ok=True)
    shutil.copyfile(os.path.join(TEMPLATE_DIR, "indeed.html"), os.path.join(tools, "indeed.html"))
    shutil.copyfile(os.path.join(TEMPLATE_DIR, "indeed_collector.js"), os.path.join(tools, "indeed_collector.js"))
    write_export(docs_dir, list(entries.values()))


EXPORT_README = """# ドスパラ全店 時給調査データ（AI・表計算ソフト向け）

このZIPは、ドスパラ各店舗の周辺アルバイト求人を自動収集・集計したデータです。
ChatGPT・Claude・Gemini などのAIや Excel にそのまま読み込んで分析できます。
作成日: {created}／対象店舗: {n_stores}店（調査済み {n_done}店）／求人: {n_jobs}件

## ファイル
- stores.csv   … 店舗ごとの集計（1行=1店舗）
- jobs.csv     … 求人一覧（1行=1店舗×1求人。同じ求人が近くの複数店舗に出ることがある）
- benefits.csv … ①自店と③直接競合の待遇比較（交通費・昇給・登用・社割・社保・研修・手当）
- README.md    … この説明

## 区分の定義
- ①自店      … ドスパラの求人（自社採用サイト tw-saiyou.snar.jp の掲載値）
- ③直接競合  … 家電量販店・パソコン専門店・PCパーツ店・中古PC/スマホ店の求人（店舗から10km以内）
- ②間接競合  … それ以外の全職種の求人（店舗から5km以内）
- 自社他店    … 近くにある別のドスパラ店舗の求人

## stores.csv の列
store_id, store_name, region(地域), prefecture, run_date(調査日),
own_hourly(①自店の募集時給・円), indirect_median(②周辺の時給中央値・円), direct_median(③直接競合の時給中央値・円),
gap_vs_indirect(①−②・円。マイナスは相場より低い), gap_vs_direct(①−③・円),
indirect_n / direct_n(時給求人の件数), area_postings(周辺エリアのマイナビバイト掲載件数), report_url(店舗レポート),
*_excl_dispatch(派遣社員・紹介予定派遣の求人を除いた中央値と件数)

## jobs.csv の列
store_id, store_name, class(区分), distance_km(店舗からの直線距離・km), company(企業), shop(店舗・勤務地名),
occupation(職種名), category(職種分類), wage_type(時給/日給/月給), wage_text(給与表記),
hourly(時給の下限・円。時給以外は空), station(最寄駅), location_basis(距離の根拠),
site(求人サイト), url(求人URL), work_content(仕事内容の抜粋・最大160字), benefits(求人に明記された待遇), posted_date(掲載開始日),
employment(雇用形態), dispatch(1=派遣・紹介予定派遣の求人)

## 注意
- 時給は求人の「下限額」。日給・月給は時給に換算していない。中央値は同じ企業・職種・時給の求人を1件として計算。
- ②③はマイナビバイトの掲載求人。サイトの規則により、エリア×職種ごとに先頭30件までの取得（件数そのものは area_postings が正確）。
- Indeed・バイトル・タウンワークの求人は含まない。
- 「記載なし」や空欄は、制度が無いことを意味しない（求人票に書かれていないだけ）。
- 派遣会社の求人は、本社住所ではなく勤務地の表記や最寄駅から位置を推定している（location_basis 参照）。

## AIへの質問例
- 「stores.csv で gap_vs_indirect が小さい（相場より低い）店舗を10店挙げ、地域ごとの傾向を説明して」
- 「jobs.csv から店舗ID dospara-namba の③直接競合だけを抜き出し、時給と待遇を比較表にして」
- 「benefits.csv を使い、自店の求人票に足りない待遇の記載を店舗ごとに指摘して」
"""


def write_export(docs_dir: str, entries: list[dict]):
    """全店の最新データを docs/data/ に CSV と ZIP で出力（他のAIや Excel で再利用するため）"""
    import datetime as dt
    import io
    import zipfile

    out_dir = os.path.join(docs_dir, "data")
    os.makedirs(out_dir, exist_ok=True)
    base_url = "https://knoro-hue.github.io/HourlyWageSurvey/"
    stores_csv = io.StringIO()
    w = csv.writer(stores_csv)
    w.writerow(["store_id", "store_name", "region", "prefecture", "run_date", "own_hourly", "indirect_median", "direct_median",
                "gap_vs_indirect", "gap_vs_direct", "indirect_n", "direct_n", "area_postings", "report_url",
                "indirect_median_excl_dispatch", "direct_median_excl_dispatch", "indirect_n_excl_dispatch", "direct_n_excl_dispatch"])
    for e in entries:
        gi = e["own"] - e["indirect"] if e.get("own") is not None and e.get("indirect") is not None else None
        gd = e["own"] - e["direct"] if e.get("own") is not None and e.get("direct") is not None else None
        w.writerow([e["id"], e["name"], e.get("region", ""), e.get("pref", ""), e.get("run_date") or "", e.get("own"),
                    e.get("indirect"), e.get("direct"), gi, gd, e.get("indirect_n", 0), e.get("direct_n", 0),
                    e.get("postings"), base_url + e["id"] + "/" if e.get("run_date") else "",
                    e.get("indirect_nd"), e.get("direct_nd"), e.get("indirect_n_nd", 0), e.get("direct_n_nd", 0)])
    jobs_csv, ben_csv = io.StringIO(), io.StringIO()
    jw, bw = csv.writer(jobs_csv), csv.writer(ben_csv)
    jw.writerow(["store_id", "store_name"] + COLUMNS)
    bcols = [k for k, _ in BENEFIT_COLUMNS]
    bw.writerow(["store_id", "store_name"] + bcols)
    n_jobs = 0
    for e in entries:
        p = os.path.join(docs_dir, e["id"], "data.json")
        if not os.path.isfile(p):
            continue
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        cols = d["columns"]
        for row in d.get("rows", []):
            rec = dict(zip(cols, row))
            jw.writerow([e["id"], e["name"]] + [rec.get(c, "") for c in COLUMNS])
            n_jobs += 1
        for b in d.get("benefits", []):
            bw.writerow([e["id"], e["name"]] + [b.get(k, "") for k in bcols])
    readme = EXPORT_README.format(created=dt.date.today().isoformat(), n_stores=len(entries),
                                  n_done=sum(1 for e in entries if e.get("run_date")), n_jobs=n_jobs)
    bom = "\ufeff"  # Excel で文字化けしないよう BOM 付き UTF-8
    with open(os.path.join(out_dir, "stores.csv"), "w", encoding="utf-8", newline="") as f:
        f.write(bom + stores_csv.getvalue())
    with open(os.path.join(out_dir, "benefits.csv"), "w", encoding="utf-8", newline="") as f:
        f.write(bom + ben_csv.getvalue())
    with open(os.path.join(out_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(readme)
    with zipfile.ZipFile(os.path.join(out_dir, "dospara_wage_survey.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.md", readme)
        z.writestr("stores.csv", bom + stores_csv.getvalue())
        z.writestr("jobs.csv", bom + jobs_csv.getvalue())
        z.writestr("benefits.csv", bom + ben_csv.getvalue())


def rebuild_from_reports(store: dict, run_date: str, reports_dir: str = "reports", docs_dir: str = "docs", repo: str = ""):
    """保存済みの CSV / Excel からダッシュボードだけを作り直す（再取得なし）"""
    from openpyxl import load_workbook

    from .collector import PageLog
    from .records import JobRow
    from .report import ROW_COLUMNS

    sid = store["id"]
    base = os.path.join(reports_dir, sid, run_date)
    label_to_key = {h: k for k, h in ROW_COLUMNS}
    rows = []
    with open(os.path.join(base, f"{sid}_{run_date}_jobs.csv"), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            d = {label_to_key[k]: v for k, v in r.items() if k in label_to_key}
            num = lambda x: float(x) if x not in (None, "") else None  # noqa: E731
            wt = d.get("wage_type", "")
            rows.append(JobRow(
                store_id=sid, classification=d["classification"], site=d["site"], job_id="", url=d["url"],
                company=d["company"], shop=d["shop"], occupation=d["occupation"], category=d["category"],
                employment=d["employment"], wage_type=wt, wage_min=num(d["wage_min"]), wage_max=num(d["wage_max"]),
                wage_text=d["wage_text"], hourly_min=num(d["wage_min"]) if wt == "時給" else None,
                hourly_max=num(d["wage_max"]) if wt == "時給" else None, address=d["address"], station=d["station"],
                lat=None, lng=None, location_method=d["location_method"], distance_km=num(d["distance_km"]), shift="",
                work_content=r.get("仕事内容（業務範囲）", ""), publication_start=d["publication_start"],
                publication_end=d["publication_end"], matched_by=d["matched_by"],
            ))
    res = StoreResult(store=store, rows=rows)
    wb = load_workbook(os.path.join(base, f"{sid}_{run_date}.xlsx"), read_only=True)
    for i, r in enumerate(wb["⑥求人数_エリア職種別"].iter_rows(values_only=True)):
        if i:
            res.pages.append(PageLog(r[0], r[5], r[1], r[2], r[3], r[4]))
    write_dashboard(res, run_date, os.path.join(reports_dir, sid, "history.csv"), docs_dir, repo)


if __name__ == "__main__":  # python -m wage_survey.dashboard <店舗ID> <YYYY-MM-DD>
    import sys

    import yaml

    with open("config/stores.yaml", encoding="utf-8") as f:
        stores = {s["id"]: s for s in yaml.safe_load(f)["stores"]}
    rebuild_from_reports(stores[sys.argv[1]], sys.argv[2], repo=os.environ.get("GITHUB_REPOSITORY", "knoro-hue/HourlyWageSurvey"))
