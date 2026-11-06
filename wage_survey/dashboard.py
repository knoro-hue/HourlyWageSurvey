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
from .report import hourly_values, stats

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
COLUMNS = ["class", "distance_km", "company", "shop", "occupation", "category", "wage_type",
           "wage_text", "hourly", "station", "location_basis", "site", "url", "work_content"]


def _short_category(c: str) -> str:
    return (c or "(分類なし)").split(" > ")[-1]


def build_data(res: StoreResult, run_date: str, history_path: str) -> dict:
    st = res.store
    by = defaultdict(list)
    for r in res.rows:
        by[r.classification].append(r)
    summary = {}
    for key, cls in (("own", CLASS_OWN), ("direct", CLASS_DIRECT), ("indirect", CLASS_INDIRECT)):
        s = stats(hourly_values(by.get(cls, [])))
        summary[key] = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in s.items()}
    if not summary["own"]["n"] and st.get("own_hourly_wage_manual"):
        v = float(st["own_hourly_wage_manual"])
        summary["own"] = {"n": 1, "min": v, "p25": v, "median": v, "mean": v, "p75": v, "max": v, "manual": True}
    rows = []
    for r in sorted(res.rows, key=lambda r: (r.distance_km if r.distance_km is not None else 99)):
        rows.append([
            r.classification, r.distance_km, r.company, r.shop, r.occupation, _short_category(r.category),
            r.wage_type, r.wage_text, r.hourly_min, r.station, r.location_method, r.site, r.url,
            " ".join((r.work_content or "").split())[:160],
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
        "columns": COLUMNS,
        "rows": rows,
        "areas": areas,
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
            "shard": s.get("shard"), "run_date": None, "own": None, "direct": None, "indirect": None,
            "direct_n": 0, "indirect_n": 0, "postings": None, "jobs": 0,
        }
    for sid in sorted(os.listdir(docs_dir)):
        p = os.path.join(docs_dir, sid, "data.json")
        if not os.path.isfile(p):
            continue
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        s = d["summary"]
        e = entries.setdefault(sid, {"id": sid, "name": d["store"]["name"], "region": "", "pref": "", "shard": None})
        e.update({
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
