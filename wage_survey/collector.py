"""店舗ごとの収集処理（マイナビバイト）"""
from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field

from . import areas as areas_mod
from . import competitor_research, mynavi, snar
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
    areas: list[str] = field(default_factory=list)
    research: dict | None = None


def _specificity(occ_path: str) -> int:
    return occ_path.count("/") + (1 if occ_path else 0)


def collect_store(store: dict, settings: dict, session, geocoder, page_cache: dict | None = None, area_master: dict | None = None) -> StoreResult:
    """page_cache: 同じ実行内で店舗間の取得結果を共有する（同じURLは1回だけ取得）"""
    res = StoreResult(store=store)
    page_cache = {} if page_cache is None else page_cache
    cfg = store.get("mynavi") or {}
    indirect_areas: list[str] = cfg.get("indirect_areas") or []
    direct_areas: list[str] = cfg.get("direct_areas") or []
    direct_fallback: list[str] = cfg.get("direct_fallback_areas") or []
    if not indirect_areas and area_master:
        indirect_areas = areas_mod.select_areas(store, area_master, float(store.get("indirect_radius_km", 5)))
        direct_areas = areas_mod.select_areas(store, area_master, float(store.get("direct_radius_km", 10)))
        direct_fallback = []
        log.info("%s: 対象エリア 間接 %d / 直接 %d", store["id"], len(indirect_areas), len(direct_areas))
    if not indirect_areas:
        log.warning("%s: 対象エリアがありません（エリアマスタ未作成の可能性）", store["id"])
    res.areas = list(indirect_areas)
    direct_occ: list[str] = settings["mynavi"].get("direct_occupation_paths") or []
    drill_priority: list[str] = settings["mynavi"].get("drill_priority_ss") or []
    start_requests = session.count

    jobs: dict[str, dict] = {}  # jobStockCd -> {"job":..., "category":..., "spec":..., "url":...}

    def visit(purpose: str, area: str, occ: str = "") -> mynavi.SearchPage | None:
        url = mynavi.search_url(area, occ)
        if url in page_cache:
            page = page_cache[url]
        else:
            if not session.budget_left():
                res.budget_exhausted = True
                return None
            page = mynavi.fetch_search(session, url)
            if page is None:
                return None
            page_cache[url] = page
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
        if not session.budget_left() and mynavi.search_url(area, ssm) not in page_cache:
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

    res.rows += snar.own_rows(store, session)
    res.rows += load_manual_rows(store, settings, geocoder)
    rcfg = settings.get("competitor_research") or {}
    if rcfg.get("enabled"):
        own = next((r.hourly_min for r in res.rows if r.classification == CLASS_OWN and r.hourly_min), None) or store.get("own_hourly_wage_manual")
        known = [f"{r.company} {r.shop}（{r.wage_text}）" for r in res.rows if r.classification == CLASS_DIRECT]
        res.research = competitor_research.research_store(store, own, known, rcfg)
        if res.research:
            res.rows += research_rows(store, res.research, geocoder, r_dir)
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


def _locate(text: str, store: dict, geocoder):
    """Web 調査の「所在地または最寄駅」から座標を推定"""
    t = nfkc(text).strip()
    if not t or t == "記載なし":
        return None, ""
    pref = store.get("prefecture_label", "")
    import re as _re

    m = _re.search(r"([^\s、,/（(]+?)駅", t)
    if _re.search(r"[市区町村郡]", t):
        pt = geocoder.geocode(t if t.startswith(pref) or _re.match(r"^\S+?[都道府県]", t) else pref + t)
        if pt:
            return pt, "Web調査の所在地"
    if m:
        pt = geocoder.station(m.group(1), pref)
        if pt:
            return pt, "Web調査の最寄駅"
    return None, ""


def research_rows(store: dict, research: dict, geocoder, radius_km: float) -> list[JobRow]:
    out = []
    for i, p in enumerate(research.get("postings") or []):
        pt, how = _locate(p.get("address_or_station", ""), store, geocoder)
        dist = round(haversine_km(store["lat"], store["lng"], pt[0], pt[1]), 2) if pt else None
        p["distance_km"] = dist
        if dist is not None and dist > radius_km:
            p["out_of_range"] = True
            continue
        lo, hi = p.get("hourly_min"), p.get("hourly_max")
        work = "／".join(f"{k}:{p.get(v)}" for k, v in (("交通費", "transport"), ("昇給", "raise"), ("登用", "promotion"),
                         ("社割", "discount"), ("社保", "social_insurance"), ("研修", "training"), ("加算", "allowances"),
                         ("特徴", "features")) if p.get(v) and p.get(v) != "記載なし")
        if "マイナビバイト" in (p.get("source_site") or ""):
            continue  # 一覧の行はマイナビバイト自動取得分と重複するため追加しない（待遇比較表には残す）
        out.append(JobRow(
            store_id=store["id"], classification=CLASS_DIRECT, site=p.get("source_site") or "Web調査",
            job_id=f"web-{i + 1}", url=p.get("source_url") or "", company=nfkc(p.get("company")), shop=nfkc(p.get("store")),
            occupation="店舗スタッフ（Web調査）", category="(Web調査)", employment="アルバイト・パート",
            wage_type="時給" if lo else "", wage_min=float(lo) if lo else None, wage_max=float(hi) if hi else None,
            wage_text=nfkc(p.get("wage_text")), hourly_min=float(lo) if lo else None, hourly_max=float(hi) if hi else None,
            address=nfkc(p.get("address_or_station")), station="", lat=pt[0] if pt else None, lng=pt[1] if pt else None,
            location_method=how or "位置不明（Web調査）", distance_km=dist, shift="", work_content=work,
            publication_start=p.get("source_date") or "", publication_end="", matched_by="Web調査（Claude）",
        ))
    return out
