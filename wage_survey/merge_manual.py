"""取り込み済みの調査結果に、data/manual の CSV（Indeed など）だけを反映し直す（マイナビバイトの再取得なし）

最新の調査日のレポート（CSV / Excel）と docs/<店舗>/data.json から求人一覧を復元し、
前回取り込んだ CSV 分を外してから、現在の data/manual の CSV を入れ直して全出力を作り直す。
"""
from __future__ import annotations

import csv
import json
import logging
import os

from .collector import PageLog, StoreResult, load_manual_rows
from .dashboard import BENEFIT_LABELS, write_dashboard
from .records import JobRow
from .report import ROW_COLUMNS, append_history, write_csv, write_excel, write_markdown

log = logging.getLogger(__name__)
LABEL_TO_BENEFIT = {v: k for k, v in BENEFIT_LABELS.items()}


def parse_benefit_text(text: str) -> dict | None:
    """ダッシュボード用の「交通費:あり／昇給:あり」形式を待遇の辞書に戻す（値の中の「／」も保つ）"""
    if not text:
        return None
    out: dict = {}
    last = None
    for seg in text.split("／"):
        lab, sep, val = seg.partition(":")
        if sep and lab in LABEL_TO_BENEFIT:
            last = LABEL_TO_BENEFIT[lab]
            out[last] = val
        elif last:
            out[last] += "／" + seg
    return out or None


def latest_run_date(reports_dir: str, sid: str) -> str | None:
    d = os.path.join(reports_dir, sid)
    if not os.path.isdir(d):
        return None
    dates = sorted(n for n in os.listdir(d) if len(n) == 10 and n[4] == "-" and
                   os.path.exists(os.path.join(d, n, f"{sid}_{n}_jobs.csv")))
    return dates[-1] if dates else None


def _num(x):
    try:
        return float(x) if x not in (None, "") else None
    except ValueError:
        return None


def load_saved_result(store: dict, run_date: str, reports_dir: str = "reports", docs_dir: str = "docs") -> StoreResult:
    """保存済みの調査結果を StoreResult に戻す（CSV取込の行は除く）"""
    sid = store["id"]
    base = os.path.join(reports_dir, sid, run_date)
    label_to_key = {h: k for k, h in ROW_COLUMNS}
    benefits: dict = {}
    data_path = os.path.join(docs_dir, sid, "data.json")
    if os.path.exists(data_path):
        with open(data_path, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("run_date") == run_date:
            ix = {c: i for i, c in enumerate(d.get("columns") or [])}
            if "benefits" in ix and "url" in ix:
                for r in d.get("rows", []):
                    b = parse_benefit_text(r[ix["benefits"]])
                    if b:
                        benefits[(r[ix["url"]], r[ix["class"]])] = b
    rows = []
    with open(os.path.join(base, f"{sid}_{run_date}_jobs.csv"), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            d = {label_to_key[k]: v for k, v in r.items() if k in label_to_key}
            if str(d.get("matched_by", "")).endswith("（CSV取込）"):
                continue  # 前回取り込んだ分は入れ直す
            wt = d.get("wage_type", "")
            lo, hi = _num(d.get("wage_min")), _num(d.get("wage_max"))
            rows.append(JobRow(
                store_id=sid, classification=d["classification"], site=d["site"], job_id="", url=d["url"],
                company=d["company"], shop=d["shop"], occupation=d["occupation"], category=d["category"],
                employment=d["employment"], wage_type=wt, wage_min=lo, wage_max=hi, wage_text=d["wage_text"],
                hourly_min=lo if wt == "時給" else None, hourly_max=hi if wt == "時給" else None,
                address=d["address"], station=d["station"], lat=None, lng=None, location_method=d["location_method"],
                distance_km=_num(d["distance_km"]), shift="", work_content=r.get("仕事内容（業務範囲）", ""),
                publication_start=d["publication_start"], publication_end=d["publication_end"],
                matched_by=d["matched_by"], benefits=benefits.get((d["url"], d["classification"])),
            ))
    res = StoreResult(store=store, rows=rows)
    xlsx = os.path.join(base, f"{sid}_{run_date}.xlsx")
    if os.path.exists(xlsx):
        from openpyxl import load_workbook

        wb = load_workbook(xlsx, read_only=True)
        if "⑥求人数_エリア職種別" in wb.sheetnames:
            for i, r in enumerate(wb["⑥求人数_エリア職種別"].iter_rows(values_only=True)):
                if i and r and r[0]:
                    res.pages.append(PageLog(r[0], r[5], r[1], r[2], r[3], r[4]))
        wb.close()
    rp = os.path.join(base, "competitor_research.json")
    if os.path.exists(rp):
        with open(rp, encoding="utf-8") as f:
            res.research = json.load(f)
    return res


def merge_store(store: dict, settings: dict, geocoder, reports_dir: str = "reports", docs_dir: str = "docs",
                repo: str = "", write_latest=None) -> tuple[int, int] | None:
    """1店舗分を作り直す。戻り値は（取り込んだ件数, 全件数）。調査結果が無い店舗は None"""
    sid = store["id"]
    run_date = latest_run_date(reports_dir, sid)
    if not run_date:
        return None
    res = load_saved_result(store, run_date, reports_dir, docs_dir)
    manual = load_manual_rows(store, settings, geocoder)
    res.rows += manual
    out_dir = os.path.join(reports_dir, sid, run_date)
    files = {
        "Excel（①〜⑥シート）": os.path.join(out_dir, f"{sid}_{run_date}.xlsx"),
        "CSV（全求人）": os.path.join(out_dir, f"{sid}_{run_date}_jobs.csv"),
    }
    write_excel(res, files["Excel（①〜⑥シート）"], run_date)
    write_csv(res, files["CSV（全求人）"])
    md = os.path.join(out_dir, "summary.md")
    write_markdown(res, md, run_date, files)
    append_history(res, os.path.join(reports_dir, sid, "history.csv"), run_date)
    write_dashboard(res, run_date, os.path.join(reports_dir, sid, "history.csv"), docs_dir, repo)
    if write_latest:
        write_latest(sid, run_date, md, files)
    log.info("%s %s: CSV取込 %d 件（全 %d 件）", sid, run_date, len(manual), len(res.rows))
    return len(manual), len(res.rows)
