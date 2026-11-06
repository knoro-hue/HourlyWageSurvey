"""ドスパラ公式サイトの店舗一覧から config/stores.yaml の店舗リストを作る

- 店名が「ドスパラ」で始まる店舗だけを対象（併設のデジタルドックは除外）
- 住所 → 座標は国土地理院 住所検索
- 店舗ごとに曜日（shard 0〜6）を割り当て、毎日1/7ずつ調査する
- 既存の店舗設定で手で変えた項目（半径・手入力時給など）は残す
"""
from __future__ import annotations

import html as htmllib
import logging
import re

import yaml

from .areas import pref_of

log = logging.getLogger(__name__)

SHOP_LIST_URL = "https://www.dospara.co.jp/shop/inc-shop-list.html"
REGION_LABELS = {
    "shop-hokkaido-tohoku": "北海道・東北", "shop-kanto": "関東", "shop-kanto-vicinity": "関東近辺",
    "shop-tokai-hokuriku": "中部", "shop-kansai": "関西", "shop-chugoku-shikoku": "中国・四国", "shop-kyushu": "九州・沖縄",
}
# 既存IDを維持する店舗（履歴を引き継ぐため）
ID_OVERRIDES = {"osaka-namba": "dospara-namba"}
KEEP_KEYS = ("enabled", "own_hourly_wage_manual", "indirect_radius_km", "direct_radius_km", "own_match_radius_km", "mynavi", "shard", "own_job_sites")
SHARDS = 7


def parse_shop_list(page_html: str) -> list[dict]:
    regions = [(m.start(), m.group(1)) for m in re.finditer(r'<div class="p-shop__store-list__area[^"]*"[^>]*id="(shop-[a-z\-]+)"', page_html)]
    shops = []
    for m in re.finditer(r'<div class="p-shop__store-list__card">', page_html):
        card = page_html[m.start(): m.start() + 6000]
        name = re.search(r"<h4>(.*?)</h4>", card, re.S)
        url = re.search(r'<a href="([^"]+)"', card)
        addr = re.search(r'class="postal-code">(.*?)</p>\s*<p>(.*?)</p>', card, re.S)
        if not (name and url and addr):
            continue
        region = ""
        for pos, rid in regions:
            if pos < m.start():
                region = REGION_LABELS.get(rid, rid)
        shops.append({
            "name": htmllib.unescape(re.sub(r"\s+", " ", name.group(1))).strip(),
            "url": url.group(1),
            "postal": htmllib.unescape(addr.group(1)).strip(),
            "address": htmllib.unescape(re.sub(r"\s+", " ", addr.group(2))).strip(),
            "region": region,
        })
    return shops


def own_match_keys(name: str) -> list[str]:
    key = re.sub(r"^ドスパラ", "", name).strip()
    keys = [key]
    if "・" in key:
        keys.append(key.split("・", 1)[1])
    return keys


def sync(session, geocoder, stores_path: str, snar_url: str, company: str) -> list[dict]:
    r = session.get(SHOP_LIST_URL)
    if r is None or r.status_code != 200:
        raise RuntimeError(f"店舗一覧を取得できません: {SHOP_LIST_URL}")
    r.encoding = "utf-8"
    shops = [s for s in parse_shop_list(r.text) if s["name"].startswith("ドスパラ")]
    log.info("ドスパラ店舗: %d 店", len(shops))
    with open(stores_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    old = {s["id"]: s for s in cfg.get("stores") or []}
    out = []
    for s in shops:
        slug = s["url"].rstrip("/").rsplit("/", 1)[-1].replace(".html", "")
        sid = ID_OVERRIDES.get(slug, "dospara-" + slug)
        pref_label, pref_slug = pref_of(s["address"])
        prev = old.get(sid, {})
        st = {
            "id": sid, "name": s["name"], "region": s["region"], "prefecture_label": pref_label,
            "address": s["address"], "postal": s["postal"], "shop_url": s["url"],
        }
        if prev.get("lat") and prev.get("address") == s["address"]:
            st["lat"], st["lng"] = prev["lat"], prev["lng"]
        else:
            pt = geocoder.geocode(s["address"])
            if pt:
                st["lat"], st["lng"] = round(pt[0], 6), round(pt[1], 6)
            else:
                log.warning("座標を取得できません: %s %s", s["name"], s["address"])
        st["own_job_sites"] = [{"type": "snar", "company": company, "url": snar_url, "match": own_match_keys(s["name"])}]
        for k in KEEP_KEYS:
            if k in prev:
                st[k] = prev[k]
        out.append(st)
    # 曜日割り当て（未割り当ての店舗のみ）。同じ都道府県はなるべく同じ曜日にして取得結果を共有する
    load = [0] * SHARDS
    for st in out:
        if "shard" in st:
            load[st["shard"] % SHARDS] += 1
    by_pref: dict[str, list[dict]] = {}
    for st in out:
        if "shard" not in st:
            by_pref.setdefault(st["prefecture_label"], []).append(st)
    for _, group in sorted(by_pref.items(), key=lambda kv: -len(kv[1])):
        i = min(range(SHARDS), key=lambda k: load[k])
        for st in group:
            st["shard"] = i
        load[i] += len(group)
    cfg["stores"] = out
    with open(stores_path, "w", encoding="utf-8") as f:
        f.write("# 調査対象店舗（python -m wage_survey.main --sync-stores で公式サイトから自動更新）\n")
        f.write("# shard: 調査グループ（0〜6、日曜にグループ順に実行）。手で変えた値は次回の自動更新でも維持されます\n")
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False, width=200)
    log.info("店舗設定を更新: %s（曜日別 %s）", stores_path, load)
    return out
