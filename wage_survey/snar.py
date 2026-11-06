"""自社採用サイト（sonar ATS: *.snar.jp）の求人一覧から自店の求人を取り出す

一覧ページ（index.aspx?id=...）に全求人のタイトル・給与・職種・勤務地が並ぶため、
詳細ページ（ASP.NET のポストバック）にはアクセスしない。robots.txt は PoliteSession が確認する。
"""
from __future__ import annotations

import html as htmllib
import logging
import re

from .records import CLASS_OWN, JobRow, nfkc

log = logging.getLogger(__name__)

SITE_NAME = "自社採用サイト"
_BLOCK_RE = re.compile(r'<div id="rpt_GroupItem_customBg_\d+"')
_WAGE_RE = re.compile(r"時給\s*[:：]?\s*([\d,]+)\s*円(?:\s*[~〜～-]\s*([\d,]+)\s*円)?")


def _text(fragment: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmllib.unescape(s).replace("\xa0", " ")
    return "\n".join(line.strip() for line in s.splitlines() if line.strip())


def _dd_after(block: str, label: str) -> str:
    m = re.search(rf"<dt>\s*{label}\s*</dt>(.*?)(?:<dt>|</dl>)", block, re.S)
    return _text(m.group(1)) if m else ""


def parse_jobs(page_html: str) -> list[dict]:
    starts = [m.start() for m in _BLOCK_RE.finditer(page_html)] + [len(page_html)]
    jobs = []
    for a, b in zip(starts, starts[1:]):
        block = page_html[a:b]
        t = re.search(r'id="rpt_GroupItem_lkb_Title_\d+"[^>]*>(.*?)</a>', block, re.S)
        if not t:
            continue
        p = re.search(r'<div[^>]*class="contents"[^>]*>.*?<p>(.*?)</p>', block, re.S)
        body = _text(p.group(1)) if p else ""
        body = re.sub(r"［more］\s*$", "", body).strip()
        jobs.append({
            "title": _text(t.group(1)),
            "body": body,
            "occupation": _dd_after(block, "職種"),
            "location": _dd_after(block, "勤務地"),
        })
    return jobs


def parse_wage_text(text: str) -> tuple[float | None, float | None, str]:
    m = _WAGE_RE.search(nfkc(text))
    if not m:
        return None, None, ""
    lo = float(m.group(1).replace(",", ""))
    hi = float(m.group(2).replace(",", "")) if m.group(2) else None
    line = next((ln for ln in text.splitlines() if "時給" in ln), m.group(0))
    return lo, hi, nfkc(line).strip()


def own_rows(store: dict, session) -> list[JobRow]:
    """config の own_job_sites（type: snar）から、match に一致する自店求人を行にする"""
    out: list[JobRow] = []
    for src in store.get("own_job_sites") or []:
        if src.get("type") != "snar":
            continue
        url = src["url"]
        r = session.get(url)
        if r is None or r.status_code != 200:
            log.warning("自社採用サイトを取得できません: %s", url)
            continue
        r.encoding = r.encoding or "utf-8"
        matches = [nfkc(m) for m in src.get("match") or []]
        for i, j in enumerate(parse_jobs(r.text)):
            hay = nfkc(j["title"] + " " + j["location"])
            if not any(m in hay for m in matches):
                continue
            lo, hi, wtext = parse_wage_text(j["body"])
            out.append(JobRow(
                store_id=store["id"], classification=CLASS_OWN, site=SITE_NAME, job_id=f"own-{i + 1}",
                url=url, company=src.get("company", ""), shop=store["name"], occupation=j["title"],
                category=j["occupation"], employment="アルバイト・パート" if "アルバイト" in j["occupation"] + j["title"] else "",
                wage_type="時給" if lo else "", wage_min=lo, wage_max=hi, wage_text=wtext,
                hourly_min=lo, hourly_max=hi, address=store.get("address", ""), station="",
                lat=store["lat"], lng=store["lng"], location_method="店舗所在地", distance_km=0.0,
                shift="", work_content=j["body"], publication_start="", publication_end="",
                matched_by=f"自社採用サイト:{j['location']}",
            ))
    if not out and store.get("own_job_sites"):
        log.warning("自社採用サイトに自店の求人が見つかりません（match を確認）: %s", store["id"])
    return out
