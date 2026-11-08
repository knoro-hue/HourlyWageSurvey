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

from . import areas as areas_mod
from . import stores_sync
from .collector import collect_store
from .dashboard import write_dashboard, write_index
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
    ap.add_argument("--docs", default="docs", help="GitHub Pages 用ダッシュボードの出力先")
    ap.add_argument("--repo", default="knoro-hue/HourlyWageSurvey", help="ダウンロードリンク用のリポジトリ名")
    ap.add_argument("--max-requests", type=int, default=None, help="店舗あたりの最大リクエスト数（上書き）")
    ap.add_argument("--date", default=None, help="レポート日付 YYYY-MM-DD（省略時は今日 JST）")
    ap.add_argument("--shard", default=None, help="この店舗グループ（0〜6）だけ調査。all で全店、today で今日の曜日番号")
    ap.add_argument("--sync-stores", action="store_true", help="公式サイトの店舗一覧から config/stores.yaml を更新して終了")
    ap.add_argument("--build-areas", action="store_true", help="マイナビバイトのエリアマスタを作成・更新して終了")
    ap.add_argument("--areas", default="data/mynavi_areas.json")
    ap.add_argument("--set-enabled", default=None, help="調査対象にする店舗ID（カンマ区切り）を保存して終了")
    ap.add_argument("--only", default=None, help="この店舗ID（カンマ区切り）に限って調査（--shard と併用可）")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = load_yaml(a.settings)
    geo_session = PoliteSession(delay_sec=1.0)
    geocoder = Geocoder(geo_session, settings.get("geocode_cache", "data/geocode_cache.json"))
    delay = float(settings.get("request_delay_sec", 4))

    if a.set_enabled is not None:
        ids = [x.strip() for x in a.set_enabled.split(",") if x.strip()]
        n, total = stores_sync.set_enabled(a.stores, ids)
        write_index(a.docs, a.stores)
        log.info("調査対象を保存: %d / %d 店", n, total)
        return 0

    if a.sync_stores:
        own = settings.get("own_job_site") or {}
        stores_sync.sync(PoliteSession(delay_sec=delay), geocoder, a.stores, own.get("url", ""), own.get("company", ""))
        geocoder.save()
        return 0

    defaults = settings.get("store_defaults") or {}
    stores = [{**defaults, **s} for s in (load_yaml(a.stores).get("stores") or [])]

    if a.build_areas:
        prefs = set()
        for s in stores:
            if s.get("enabled", True):
                slug = areas_mod.pref_of(s.get("address", ""))[1]
                prefs |= {slug, *areas_mod.NEIGHBORS.get(slug, [])}
        session = PoliteSession(delay_sec=delay, max_requests=a.max_requests or settings.get("max_requests_area_master", 1500))
        areas_mod.build_master(session, geocoder, sorted(p for p in prefs if p), a.areas)
        return 0

    if a.store:
        wanted = {x.strip() for v in a.store for x in v.split(",") if x.strip()}
        targets = [s for s in stores if s["id"] in wanted]
    else:
        targets = [s for s in stores if s.get("enabled", True)]
        if a.shard is not None:
            if a.shard != "all":
                shard = dt.datetime.now(JST).weekday() if a.shard == "today" else int(a.shard)
                targets = [s for s in targets if int(s.get("shard", 0)) % 7 == shard]
            log.info("対象店舗(%s): %s", a.shard, ", ".join(s["id"] for s in targets) or "なし")
    if a.only:
        only = {x.strip() for x in a.only.split(",") if x.strip()}
        targets = [s for s in (stores if not a.store else targets) if s["id"] in only and
                   (a.shard in (None, "all") or a.store or int(s.get("shard", 0)) % 7 == (dt.datetime.now(JST).weekday() if a.shard == "today" else int(a.shard)))]
        log.info("指定店舗に限定: %s", ", ".join(s["id"] for s in targets) or "なし")
    if not targets:
        log.warning("対象店舗がありません（--store / --shard / enabled を確認）")
        return 0
    run_date = a.date or dt.datetime.now(JST).strftime("%Y-%m-%d")
    max_req = a.max_requests or settings.get("max_requests_per_store", 450)
    area_master = areas_mod.load_master(a.areas)
    if area_master and areas_mod.fill_missing_coords(area_master, geocoder):
        areas_mod.save_master(area_master, a.areas)
        geocoder.save()
    session = PoliteSession(delay_sec=delay)
    page_cache: dict = {}
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
        session.max_requests = session.count + max_req
        try:
            res = collect_store(st, settings, session, geocoder, page_cache, area_master)
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
        if res.research:
            import json as _json

            with open(os.path.join(out_dir, "competitor_research.json"), "w", encoding="utf-8") as f:
                _json.dump(res.research, f, ensure_ascii=False, indent=1)
        write_excel(res, files["Excel（①〜⑥シート）"], run_date)
        write_csv(res, files["CSV（全求人）"])
        md = os.path.join(out_dir, "summary.md")
        write_markdown(res, md, run_date, files)
        append_history(res, os.path.join(a.out, st["id"], "history.csv"), run_date)
        write_dashboard(res, run_date, os.path.join(a.out, st["id"], "history.csv"), a.docs, os.environ.get("GITHUB_REPOSITORY", a.repo))
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
    write_index(a.docs)
    return 1 if failed and failed == len(targets) else 0


if __name__ == "__main__":
    sys.exit(main())
