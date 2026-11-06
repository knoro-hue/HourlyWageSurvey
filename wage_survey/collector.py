"""店舗ごとの収集処理（マイナビバイト）"""
from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field

from . import mynavi
from .geo import haversine_km
from .records import CLASS_DIRECT, CLASS_INDIRECT, CLASS_OWN, JobRow, build_rows, nfkc

log = logging.getLogger(__name__)

CLASS_OWN_OTHER = "自社他店"


@dataclass
class PageLog:
    purpose: str
    url: str
    area: str
    occupation: str
    count: int | None
    fetched_jobs: int


@dataclass
class StoreResult:
    store: dict
    rows: list[JobRow] = field(default_factory=list)
    pages: list[PageLog] = field(default_factory=list)
    unlocated: int = 0
    requests: int = 0
    budget_exhausted: bool = False


def _specificity(occ_path: str) -> int:
    return occ_path.count("/") + (1 if occ_path else 0)


def collect_store(store: dict, settings: dict, session, geocoder) -> StoreResult:
    res = StoreResult(store=store)
    cfg = store.get("mynavi") or {}
    indirect_areas: list[str] = cfg.get("indirect_areas") or []
    direct_areas: list[str] = cfg.get("direct_areas") or []
    direct_fallback: list[str] = cfg.get("direct_fallback_areas") or []
    direct_occ: list[str] = settings["mynavi"].get("direct_occupation_paths") or []
    drill_priority: list[str] = settings["mynavi"].get("drill_priority_ss") or []
    start_requests = session.count

    jobs: dict[str, dict] = {}  # jobStockCd -> {"job":..., "category":..., "spec":..., "url":...}

    def visit(purpose: str, area: str, occ: str = "") -> mynavi.SearchPage | None:
        if not session.budget_left():
            res.budget_exhausted = True
            return None
        url = mynavi.search_url(area, occ)
        page = mynavi.fetch_search(session, url)
        if page is None:
            return None
        res.pages.append(PageLog(purpose, url, page.area_label or area, page.occupation_label or occ, page.count, len(page.jobs)))
        spec = _specificity(occ)
        for j in page.jobs:
            cd = j.get("jobStockCd")
            if not cd:
                continue
            cur = jobs.get(cd)
            if cur is None or spec > cur["spec"]:
                jobs[cd] = {"job": j, "category": page.occupation_label, "spec": spec}
        return page

    # 1) 間接競合エリア: エリア全体 → 職種大分類 → (30件超なら) 中分類
    occ_map = mynavi.load_occupation_paths(session, indirect_areas) if indirect_areas else {}
    drill_queue: list[tuple[int, str, str]] = []
    for area in indirect_areas:
        visit("エリア全体", area)
        for ss in occ_map.get(area, mynavi.AreaOccupations()).ss:
            page = visit("職種大分類", area, ss)
            if page and page.count and page.count > mynavi.PAGE_SIZE:
                prio = drill_priority.index(ss) if ss in drill_priority else len(drill_priority)
                for ssm in occ_map[area].ssm.get(ss, []):
                    drill_queue.append((prio, area, ssm))

    # 2) 直接競合（家電量販店など）: 広域で検索し、30件超なら区単位に分割
    for occ in direct_occ:
        for area in direct_areas:
            page = visit("直接競合", area, occ)
            if page and page.count and page.count > mynavi.PAGE_SIZE:
                for fa in direct_fallback:
                    visit("直接競合(分割)", fa, occ)

    # 3) 中分類へのドリルダウン（優先度順・予算内）
    drill_queue.sort(key=lambda x: x[0])
    for _, area, ssm in drill_queue:
        if not session.budget_left():
            res.budget_exhausted = True
            break
        visit("職種中分類", area, ssm)

    # 4) 行データ化・距離で絞り込み
    r_ind = float(store.get("indirect_radius_km", 5))
    r_dir = float(store.get("direct_radius_km", r_ind))
    r_own = float(store.get("own_match_radius_km", 0.3))
    for cd, v in jobs.items():
        for row in build_rows(v["job"], v["category"], store, settings, geocoder, mynavi.SITE_NAME, mynavi.job_url(v["job"])):
            if row.distance_km is None:
                res.unlocated += 1
                continue
            if row.classification == CLASS_OWN:
                if row.distance_km > r_own:
                    row.classification = CLASS_OWN_OTHER
                    if row.distance_km > r_dir:
                        continue
            elif row.classification == CLASS_DIRECT and row.distance_km > r_dir:
                continue
            elif row.classification == CLASS_INDIRECT and row.distance_km > r_ind:
                continue
            res.rows.append(row)

    res.rows += load_manual_rows(store, settings, geocoder)
    res.requests = session.count - start_requests
    return res


MANUAL_COLUMNS = [
    "store_id", "classification", "site", "company", "shop", "occupation", "employment",
    "wage_type", "wage_min", "wage_max", "address", "work_content", "url", "checked_date",
]


def _f(x):
    try:
        return float(str(x).replace(",", "")) if str(x).strip() else None
    except ValueError:
        return None


def load_manual_rows(store: dict, settings: dict, geocoder) -> list[JobRow]:
    """Indeed・バイトル等、自動取得しない媒体の求人を手入力CSVから取り込む"""
    path = settings.get("manual_jobs_csv", "data/manual_jobs.csv")
    if not os.path.exists(path):
        return []
    cls_map = {"own": CLASS_OWN, "direct": CLASS_DIRECT, "indirect": CLASS_INDIRECT,
               "①": CLASS_OWN, "②": CLASS_INDIRECT, "③": CLASS_DIRECT}
    out = []
    with open(path, encoding="utf-8-sig") as f:
        for i, r in enumerate(csv.DictReader(f)):
            if (r.get("store_id") or "").strip() != store["id"]:
                continue
            c = (r.get("classification") or "").strip()
            cls = next((v for k, v in cls_map.items() if c.startswith(k)), CLASS_INDIRECT)
            addr = nfkc(r.get("address"))
            pt = geocoder.geocode(addr) if addr else None
            if cls == CLASS_OWN and not pt:
                pt = (store["lat"], store["lng"])
            dist = round(haversine_km(store["lat"], store["lng"], pt[0], pt[1]), 2) if pt else None
            wt = (r.get("wage_type") or "時給").strip()
            lo, hi = _f(r.get("wage_min")), _f(r.get("wage_max"))
            out.append(JobRow(
                store_id=store["id"], classification=cls, site=(r.get("site") or "手入力").strip(),
                job_id=f"manual-{i + 1}", url=(r.get("url") or "").strip(), company=nfkc(r.get("company")),
                shop=nfkc(r.get("shop")), occupation=nfkc(r.get("occupation")), category="(手入力)",
                employment=nfkc(r.get("employment")) or "アルバイト・パート", wage_type=wt, wage_min=lo, wage_max=hi,
                wage_text=f"{wt}{int(lo) if lo else ''}{'～' + str(int(hi)) if hi else ''}円",
                hourly_min=lo if wt == "時給" else None, hourly_max=hi if wt == "時給" else None,
                address=addr, station="", lat=pt[0] if pt else None, lng=pt[1] if pt else None,
                location_method="手入力住所" if addr else "店舗所在地", distance_km=dist, shift="",
                work_content=nfkc(r.get("work_content")), publication_start=(r.get("checked_date") or "").strip(),
                publication_end="", matched_by="手入力",
            ))
    return out
