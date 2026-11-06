"""位置情報: 国土地理院 住所検索API によるジオコーディング（キャッシュ付き）と距離計算"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import unicodedata
from urllib.parse import quote

log = logging.getLogger(__name__)

GSI_URL = "https://msearch.gsi.go.jp/address-search/AddressSearch?q="
STATION_URL = "https://express.heartrails.com/api/json?method=getStations&name="
_PREF_RE = re.compile(r"^(東京都|北海道|(?:京都|大阪)府|.{2,3}県)")
_MUNI_RE = re.compile(r"^(.+?市.+?区|.+?郡.+?[町村]|.+?[市区町村])")


def _strip_pref(s: str) -> str:
    return _PREF_RE.sub("", s, count=1)


def is_specific_match(query: str, title: str) -> bool:
    """GSI は一致しない語を都道府県の代表点で返すことがあるため、市区町村まで一致した結果だけ採用する"""
    q = _strip_pref(unicodedata.normalize("NFKC", query))
    t = _strip_pref(unicodedata.normalize("NFKC", title or ""))
    if not t:
        return False
    if q and t.startswith(q):  # 郡など、より詳しい住所が返る場合
        return True
    m = _MUNI_RE.match(t)
    return bool(m) and m.group(1) in q


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def normalize_address(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[‐－―−]", "-", s)
    s = re.sub(r"(?<=\d)ー(?=\d)", "-", s)
    s = re.sub(r"\s+", "", s)
    # 番地までを残し、ビル名・階数は除去（ジオコーディング精度向上）
    m = _ADDR_CORE.match(s)
    return m.group(0) if m else s


_ADDR_CORE = re.compile(r"^.*?\d+(?:丁目\d*)?(?:(?:-|番地?|号|の)\d+)*(?:番地?|号)?")


class Geocoder:
    def __init__(self, session, cache_path: str):
        self.session = session
        self.cache_path = cache_path
        self.cache: dict[str, list[float] | None] = {}
        if os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as f:
                self.cache = json.load(f)
        self._dirty = False

    def geocode(self, query: str) -> tuple[float, float] | None:
        q = normalize_address(query)
        if not q:
            return None
        if q in self.cache:
            v = self.cache[q]
            return (v[0], v[1]) if v else None
        r = self.session.get(GSI_URL + quote(q), check_robots=False, delay=1.0)
        result = None
        if r is not None and r.status_code == 200:
            try:
                data = r.json()
                for item in data or []:
                    title = (item.get("properties") or {}).get("title", "")
                    if is_specific_match(q, title):
                        lng, lat = item["geometry"]["coordinates"]
                        result = [float(lat), float(lng)]
                        break
            except (ValueError, KeyError, IndexError, TypeError):
                pass
        self.cache[q] = result
        self._dirty = True
        if result is None:
            log.info("ジオコーディング失敗: %s", q)
        return tuple(result) if result else None

    def station(self, name: str, prefecture: str = "") -> tuple[float, float] | None:
        """駅名 → 座標（HeartRails Express API）。同名駅は都道府県で絞り込む"""
        raw = unicodedata.normalize("NFKC", name or "").strip()
        raw = re.sub(r"駅$", "", raw)
        if not raw:
            return None
        cands = list(dict.fromkeys([raw, re.sub(r"^(JR|地下鉄|阪急|阪神|京阪|近鉄|南海|大阪メトロ)", "", raw)]))
        for nm in cands:
            key = f"station:{prefecture}:{nm}"
            if key in self.cache:
                v = self.cache[key]
                if v:
                    return v[0], v[1]
                continue
            r = self.session.get(STATION_URL + quote(nm), check_robots=False, delay=1.0)
            result = None
            if r is not None and r.status_code == 200:
                try:
                    sts = (r.json().get("response") or {}).get("station") or []
                    sts = [x for x in sts if not prefecture or x.get("prefecture") == prefecture] or ([] if prefecture else sts)
                    if sts:
                        result = [float(sts[0]["y"]), float(sts[0]["x"])]
                except (ValueError, KeyError, TypeError, AttributeError):
                    pass
            self.cache[key] = result
            self._dirty = True
            if result:
                return result[0], result[1]
        log.info("駅の座標取得失敗: %s (%s)", name, prefecture)
        return None

    def save(self):
        if self._dirty:
            os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=0, sort_keys=True)
            self._dirty = False
