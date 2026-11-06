"""マイナビバイト 検索結果ページの取得・解析

- robots.txt で `?pageNo=` が禁止されているため、各検索条件の1ページ目（最大30件）のみ取得する。
  その代わり「エリア × 職種（大分類/中分類）」に細かく分割して網羅性を上げる。
- 各ページの window.__NUXT__ を実行せずに解析し、求人の給与・仕事内容・所在地座標を取り出す。
- 掲載件数（searchCount）は条件ごとの正確な件数として ⑥ に使う。
"""
from __future__ import annotations

import gzip
import logging
import re
from dataclasses import dataclass, field

from .nuxt_state import parse_nuxt

log = logging.getLogger(__name__)

BASE = "https://baito.mynavi.jp"
SITE_NAME = "マイナビバイト"
PAGE_SIZE = 30


@dataclass
class SearchPage:
    url: str
    count: int | None
    jobs: list[dict]
    area_label: str = ""
    occupation_label: str = ""


@dataclass
class AreaOccupations:
    """sitemap から得たエリア配下の職種パス一覧"""

    ss: list[str] = field(default_factory=list)  # "ss-3"
    ssm: dict[str, list[str]] = field(default_factory=dict)  # "ss-3" -> ["ss-3/ssm-102", ...]


def _sitemap_locs(session, url: str) -> list[str]:
    r = session.get(url)
    if r is None or r.status_code != 200:
        log.warning("sitemap取得失敗: %s", url)
        return []
    body = r.content
    if url.endswith(".gz") or body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except OSError:
            pass
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body.decode("utf-8", "ignore"))


def load_occupation_paths(session, area_paths: list[str]) -> dict[str, AreaOccupations]:
    """town/city 用の ss sitemap から、各エリアに存在する職種パスを集める"""
    need_town = any("/town-" in p for p in area_paths)
    need_city = any("/town-" not in p for p in area_paths)
    locs: list[str] = []
    for kind, need in (("town", need_town), ("city", need_city)):
        if not need:
            continue
        for sm in _sitemap_locs(session, f"{BASE}/sitemap/{kind}_ss_index.xml"):
            locs += _sitemap_locs(session, sm)
    out: dict[str, AreaOccupations] = {p: AreaOccupations() for p in area_paths}
    for loc in locs:
        path = loc.replace(BASE, "")
        for ap in area_paths:
            prefix = "/" + ap.strip("/") + "/"
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix):].strip("/")
            parts = rest.split("/")
            if not parts or not parts[0].startswith("ss-"):
                continue
            if len(parts) == 1:
                out[ap].ss.append(parts[0])
            elif len(parts) == 2 and parts[1].startswith("ssm-"):
                out[ap].ssm.setdefault(parts[0], []).append(rest)
    for ao in out.values():
        ao.ss = sorted(set(ao.ss), key=lambda x: int(x.split("-")[1]))
        for k in ao.ssm:
            ao.ssm[k] = sorted(set(ao.ssm[k]), key=lambda x: int(x.rsplit("-", 1)[1]))
    return out


def search_url(area_path: str, occ_path: str = "") -> str:
    url = f"{BASE}/{area_path.strip('/')}/"
    if occ_path:
        url += occ_path.strip("/") + "/"
    return url


def _labels_from_state(ss: dict) -> tuple[str, str]:
    lab = ss.get("searchConditionLabel") or {}
    area = ""
    for a in lab.get("areaLabelList") or []:
        area = "".join(v for k, v in sorted(a.items()) if v and k != "area1stLabel")
    occ = ""
    for o in lab.get("occupationLabelList") or []:
        occ = " > ".join(v for k, v in sorted(o.items()) if v)
    return area, occ


def fetch_search(session, url: str) -> SearchPage | None:
    r = session.get(url)
    if r is None:
        return None
    if r.status_code != 200:
        log.info("HTTP %s: %s", r.status_code, url)
        return SearchPage(url=url, count=None, jobs=[])
    try:
        state = parse_nuxt(r.text)
    except Exception as e:  # 解析失敗はページ単位でスキップ
        log.warning("__NUXT__解析失敗 %s: %s", url, e)
        return SearchPage(url=url, count=None, jobs=[])
    ss = ((state or {}).get("state") or {}).get("search") or {}
    sl = ss.get("searchSolrList") or {}
    area, occ = _labels_from_state(ss)
    return SearchPage(
        url=url,
        count=sl.get("searchCount"),
        jobs=sl.get("jobList") or [],
        area_label=area,
        occupation_label=occ,
    )


def job_url(job: dict) -> str:
    cd = job.get("jobStockCd") or ""
    num = re.sub(r"\D", "", cd).lstrip("0")
    return f"{BASE}/cl-{job.get('clientCd', '')}/job-{num}/"
