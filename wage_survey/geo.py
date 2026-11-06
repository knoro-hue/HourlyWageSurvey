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
                if data:
                    lng, lat = data[0]["geometry"]["coordinates"]
                    result = [float(lat), float(lng)]
            except (ValueError, KeyError, IndexError, TypeError):
                pass
        self.cache[q] = result
        self._dirty = True
        if result is None:
            log.info("ジオコーディング失敗: %s", q)
        return tuple(result) if result else None

    def save(self):
        if self._dirty:
            os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=0, sort_keys=True)
            self._dirty = False
