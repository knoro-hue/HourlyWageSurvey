"""マイナビバイトのエリアマスタ（市区町村・区の名称と代表座標）と、店舗ごとの対象エリア自動選択

data/mynavi_areas.json:
  { "<area path>": {"pref": "osaka", "label": "大阪市浪速区", "lat": .., "lng": .., "count": 623,
                    "kind": "town" | "city", "has_towns": bool, "updated": "YYYY-MM-DD"} }

マスタは sitemap（city/town）に載っているエリアの1ページ目を一度だけ取得して作る（名称と件数）。
代表座標は国土地理院の住所検索で「都道府県＋市区町村名」を引いた点（概ね役所付近）。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re

from . import mynavi
from .geo import haversine_km

log = logging.getLogger(__name__)

PREF_SLUGS = {
    "北海道": "hokkaido", "青森県": "aomori", "岩手県": "iwate", "宮城県": "miyagi", "秋田県": "akita",
    "山形県": "yamagata", "福島県": "fukushima", "茨城県": "ibaraki", "栃木県": "tochigi", "群馬県": "gunma",
    "埼玉県": "saitama", "千葉県": "chiba", "東京都": "tokyo", "神奈川県": "kanagawa", "新潟県": "niigata",
    "富山県": "toyama", "石川県": "ishikawa", "福井県": "fukui", "山梨県": "yamanashi", "長野県": "nagano",
    "岐阜県": "gifu", "静岡県": "shizuoka", "愛知県": "aichi", "三重県": "mie", "滋賀県": "shiga",
    "京都府": "kyoto", "大阪府": "osaka", "兵庫県": "hyogo", "奈良県": "nara", "和歌山県": "wakayama",
    "鳥取県": "tottori", "島根県": "shimane", "岡山県": "okayama", "広島県": "hiroshima", "山口県": "yamaguchi",
    "徳島県": "tokushima", "香川県": "kagawa", "愛媛県": "ehime", "高知県": "kochi", "福岡県": "fukuoka",
    "佐賀県": "saga", "長崎県": "nagasaki", "熊本県": "kumamoto", "大分県": "oita", "宮崎県": "miyazaki",
    "鹿児島県": "kagoshima", "沖縄県": "okinawa",
}
SLUG_TO_PREF = {v: k for k, v in PREF_SLUGS.items()}

# 隣接都府県（県境近くの店舗のため、マスタ作成時に一緒に取得する）
NEIGHBORS = {
    "tokyo": ["kanagawa", "saitama", "chiba"], "kanagawa": ["tokyo"], "saitama": ["tokyo"], "chiba": ["tokyo"],
    "osaka": ["hyogo", "kyoto", "nara"], "kyoto": ["osaka", "shiga"], "hyogo": ["osaka"],
    "aichi": ["gifu", "mie"], "fukuoka": ["saga"], "saga": ["fukuoka"], "gunma": ["saitama"],
}


_LABEL_PREFIX = re.compile(r"^(23区全体|東京都下（23区外）|.{1,6}?エリア)")


def clean_label(label: str) -> str:
    """マイナビバイトのエリア名から地域グループ名を除く（例: 23区全体千代田区 → 千代田区）"""
    return _LABEL_PREFIX.sub("", label or "")


def fill_missing_coords(master: dict, geocoder) -> int:
    """代表座標が無いエリアを、整えた名称で引き直す"""
    n = 0
    for a in master.values():
        if a.get("lat") is not None or not a.get("label"):
            continue
        name = clean_label(a["label"])
        if not name:
            continue
        pt = geocoder.geocode(SLUG_TO_PREF.get(a["pref"], "") + name)
        if pt:
            a["lat"], a["lng"] = pt
            n += 1
    return n


def pref_of(address: str) -> tuple[str, str]:
    for label, slug in PREF_SLUGS.items():
        if address.startswith(label):
            return label, slug
    return "", ""


def load_master(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_master(master: dict, path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(master, f, ensure_ascii=False, indent=0, sort_keys=True)


def sitemap_area_paths(session) -> list[tuple[str, str]]:
    """[(path, kind)]  path 例: osaka/city-262 , osaka/city-262/town-144"""
    out = []
    for kind in ("city", "town"):
        for sm in mynavi._sitemap_locs(session, f"{mynavi.BASE}/sitemap/{kind}_index.xml"):
            for loc in mynavi._sitemap_locs(session, sm):
                p = loc.replace(mynavi.BASE, "").strip("/")
                if re.fullmatch(r"[a-z]+/city-\d+(/town-\d+)?", p):
                    out.append((p, "town" if "/town-" in p else "city"))
    return sorted(set(out))


def build_master(session, geocoder, prefs: list[str], path: str, max_age_days: int = 90) -> dict:
    """prefs（スラッグ）のエリアを取得してマスタを更新する。新しいエリアと古いエリアだけ取り直す"""
    master = load_master(path)
    today = dt.date.today()
    paths = [(p, k) for p, k in sitemap_area_paths(session) if p.split("/")[0] in prefs]
    town_parents = {p.rsplit("/", 1)[0] for p, k in paths if k == "town"}
    todo = []
    for p, k in paths:
        cur = master.get(p)
        if cur and cur.get("updated") and (today - dt.date.fromisoformat(cur["updated"])).days < max_age_days and cur.get("lat"):
            continue
        todo.append((p, k))
    log.info("エリアマスタ: 対象 %d / 取得 %d", len(paths), len(todo))
    for i, (p, k) in enumerate(todo):
        if not session.budget_left():
            log.warning("リクエスト上限のため中断（残り %d）。次回続きから取得します", len(todo) - i)
            break
        page = mynavi.fetch_search(session, mynavi.search_url(p))
        if page is None or not page.area_label:
            continue
        pref_label = SLUG_TO_PREF.get(p.split("/")[0], "")
        pt = geocoder.geocode(pref_label + clean_label(page.area_label))
        master[p] = {
            "pref": p.split("/")[0], "label": page.area_label, "count": page.count, "kind": k,
            "has_towns": p in town_parents, "lat": pt[0] if pt else None, "lng": pt[1] if pt else None,
            "updated": today.isoformat(),
        }
        if i % 50 == 49:
            save_master(master, path)
            geocoder.save()
    for p in town_parents:
        if p in master:
            master[p]["has_towns"] = True
    log.info("代表座標の補完: %d 件", fill_missing_coords(master, geocoder))
    save_master(master, path)
    geocoder.save()
    return master


def select_areas(store: dict, master: dict, radius_km: float, town_buffer_km: float = 2.0, city_buffer_km: float = 3.0) -> list[str]:
    """店舗から radius_km 圏に掛かりそうなエリアを選ぶ（区があれば市ではなく区単位）"""
    addr = store.get("address", "")
    _, slug = pref_of(addr)
    pref_label = SLUG_TO_PREF.get(slug, "")
    addr_wo_pref = addr[len(pref_label):] if pref_label else addr
    picked = []
    for p, a in master.items():
        if a.get("has_towns"):
            continue
        lab = clean_label(a.get("label", ""))
        own_area = bool(lab) and a["pref"] == slug and addr_wo_pref.startswith(lab)
        if own_area:
            picked.append((0.0, p))
            continue
        if a.get("lat") is None:
            continue
        d = haversine_km(store["lat"], store["lng"], a["lat"], a["lng"])
        buf = town_buffer_km if a.get("kind") == "town" else city_buffer_km
        if d <= radius_km + buf:
            picked.append((d, p))
    return [p for _, p in sorted(picked)]
