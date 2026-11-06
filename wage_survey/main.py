"""時給調査 CLI

使い方:
  python -m wage_survey.main                 # config/stores.yaml の enabled な全店舗
  python -m wage_survey.main --store dospara-namba
  python -m wage_survey.main --store dospara-namba --max-requests 50   # お試し
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import shutil
import sys

import yaml

from .collector import collect_store
from .geo import Geocoder
from .http import PoliteSession
from .report import append_history, write_csv, write_excel, write_markdown

log = logging.getLogger("wage_survey")
JST = dt.timezone(dt.timedelta(hours=9))


def load_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="採用向け 時給調査")
    ap.add_argument("--stores", default="config/stores.yaml")
    ap.add_argument("--settings", default="config/settings.yaml")
    ap.add_argument("--store", action="append", help="店舗ID（複数指定可）。省略時は enabled な全店舗")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--max-requests", type=int, default=None, help="店舗あたりの最大リクエスト数（上書き）")
    ap.add_argument("--date", default=None, help="レポート日付 YYYY-MM-DD（省略時は今日 JST）")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = load_yaml(a.settings)
    stores = load_yaml(a.stores).get("stores") or []
    targets = [s for s in stores if (s["id"] in a.store if a.store else s.get("enabled", True))]
    if not targets:
        log.error("対象店舗がありません（--store の指定や enabled を確認）")
        return 2
    run_date = a.date or dt.datetime.now(JST).strftime("%Y-%m-%d")
    max_req = a.max_requests or settings.get("max_requests_per_store", 450)

    geo_session = PoliteSession(delay_sec=1.0)
    geocoder = Geocoder(geo_session, settings.get("geocode_cache", "data/geocode_cache.json"))
    failed = 0
    for st in targets:
        log.info("=== %s (%s) ===", st["name"], st["id"])
        if not (st.get("lat") and st.get("lng")):
            pt = geocoder.geocode(st["address"])
            if not pt:
                log.error("店舗住所をジオコーディングできません: %s", st["address"])
                failed += 1
                continue
            st["lat"], st["lng"] = pt
        session = PoliteSession(delay_sec=float(settings.get("request_delay_sec", 4)), max_requests=max_req)
        try:
            res = collect_store(st, settings, session, geocoder)
        except Exception:
            log.exception("収集に失敗: %s", st["id"])
            failed += 1
            continue
        finally:
            geocoder.save()
        out_dir = os.path.join(a.out, st["id"], run_date)
        os.makedirs(out_dir, exist_ok=True)
        files = {
            "Excel（①〜⑥シート）": os.path.join(out_dir, f"{st['id']}_{run_date}.xlsx"),
            "CSV（全求人）": os.path.join(out_dir, f"{st['id']}_{run_date}_jobs.csv"),
        }
        write_excel(res, files["Excel（①〜⑥シート）"], run_date)
        write_csv(res, files["CSV（全求人）"])
        md = os.path.join(out_dir, "summary.md")
        write_markdown(res, md, run_date, files)
        append_history(res, os.path.join(a.out, st["id"], "history.csv"), run_date)
        # 最新版へのショートカット
        latest = os.path.join(a.out, st["id"], "latest.md")
        with open(md, encoding="utf-8") as f:
            body = f.read()
        rel = run_date + "/"
        for v in files.values():
            b = os.path.basename(v)
            body = body.replace(f"]({b})", f"]({rel}{b})")
        with open(latest, "w", encoding="utf-8") as f:
            f.write(body)
        log.info("出力: %s（%d 件, リクエスト %d）", out_dir, len(res.rows), res.requests)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                f.write(body + "\n\n")
    geocoder.save()
    return 1 if failed and failed == len(targets) else 0


if __name__ == "__main__":
    sys.exit(main())
