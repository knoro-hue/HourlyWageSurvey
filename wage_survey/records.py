"""求人データの正規化: 給与・位置（距離）・分類（自店/直接競合/間接競合）"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass

from .geo import haversine_km

CLASS_OWN = "①自店"
CLASS_DIRECT = "③直接競合"
CLASS_INDIRECT = "②間接競合"


@dataclass
class JobRow:
    store_id: str
    classification: str
    site: str
    job_id: str
    url: str
    company: str
    shop: str
    occupation: str
    category: str
    employment: str
    wage_type: str
    wage_min: float | None
    wage_max: float | None
    wage_text: str
    hourly_min: float | None
    hourly_max: float | None
    address: str
    station: str
    lat: float | None
    lng: float | None
    location_method: str
    distance_km: float | None
    shift: str
    work_content: str
    publication_start: str
    publication_end: str
    matched_by: str = ""
    benefits: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def nfkc(s) -> str:
    return unicodedata.normalize("NFKC", str(s or ""))


_NUM = r"(\d[\d,]*)"


def parse_wage(wage: dict | None) -> tuple[str, float | None, float | None, str]:
    """(給与種別, 下限, 上限, 原文)"""
    wage = wage or {}
    label = wage.get("label") or ""
    text = nfkc(wage.get("specialWageAmount") or "")
    amt = wage.get("wageAmount")
    lo = float(amt) if isinstance(amt, (int, float)) else None
    hi = None
    m = re.search(rf"{label or '時給'}\s*{_NUM}\s*円?\s*[~〜～\-]\s*{_NUM}", text) if text else None
    if m:
        lo = lo or float(m.group(1).replace(",", ""))
        hi = float(m.group(2).replace(",", ""))
    return label, lo, hi, text or (f"{label}{int(lo)}円" if lo else "")


_WORKPLACE_PATTERNS = [
    re.compile(r"勤務(?:地|先)\s*[:：]?\s*[［\[【「(（]\s*([^］\]】」)）]+)"),
    re.compile(r"勤務(?:地|先)\s*[:：]\s*([^\s/／)）］\]]+)"),
    re.compile(r"«\s*勤務地\s*»\s*\n?\s*([^\n]+)"),
    re.compile(r"【\s*勤務地\s*】\s*\n?\s*([^\n]+)"),
    re.compile(r"＜\s*勤務地\s*＞\s*\n?\s*([^\n]+)"),
]
_HQ_HINT = re.compile(r"本社|事業所所在地|登録会場|面接地|勤務地は(?:各|複数)")


def extract_workplace(*texts: str) -> str:
    for t in texts:
        t = t or ""
        for p in _WORKPLACE_PATTERNS:
            m = p.search(t)
            if m:
                v = re.sub(r"(周辺|エリア|ほか|他|など|※.*)$", "", m.group(1).strip())
                if re.search(r"[市区町村郡]", v):
                    return v
    return ""


def resolve_location(shop: dict, job: dict, geocoder, pref_label: str) -> tuple[float | None, float | None, str, str]:
    """(lat, lng, 方法, 住所) を返す。派遣会社等で本社住所が入っている場合は勤務地表記を優先"""
    sik = (shop.get("locationSikugun") or {}).get("label") or ""
    pref = (shop.get("locationPrefecture") or {}).get("label") or ""
    addr = f"{pref}{sik}{nfkc(shop.get('locationAddress1'))}"
    note = shop.get("locationNote") or ""
    shop_name = shop.get("clientShopName") or ""
    work = extract_workplace(shop_name, note)
    hq_like = bool(work) or bool(_HQ_HINT.search(note))

    if work:
        q = work if work.startswith(pref_label) else (pref_label + work if not re.match(r"^\S+?[都道府県]", work) else work)
        pt = geocoder.geocode(q)
        if pt:
            return pt[0], pt[1], "勤務地表記から推定", work
    lat, lng = shop.get("locationPinLatitude") or shop.get("locationMapLatitude"), shop.get(
        "locationPinLongitude"
    ) or shop.get("locationMapLongitude")
    if lat and lng and not hq_like:
        return float(lat), float(lng), "掲載座標", addr
    if addr and not hq_like:
        pt = geocoder.geocode(addr)
        if pt:
            return pt[0], pt[1], "住所ジオコーディング", addr
    # 最寄駅 → エリア（区）で推定
    for a in shop.get("accessList") or []:
        st = ((a.get("routeStation") or {}).get("label")) or ""
        if st:
            pt = geocoder.station(st, pref_label)
            if pt:
                return pt[0], pt[1], "最寄駅から推定", f"{st}駅周辺"
    a2 = (shop.get("area2nd") or {}).get("label") or ""
    a3 = (shop.get("area3rd") or {}).get("label") or ""
    if a2 or a3:
        pt = geocoder.geocode(f"{pref_label}{a2}{a3}")
        if pt:
            return pt[0], pt[1], "エリア中心から推定", f"{a2}{a3}"
    return None, None, "不明", addr


NA = "記載なし"
BENEFIT_KEYS = ("transport", "raise", "promotion", "discount", "social_insurance", "training", "allowances", "features")
_EXTRA_TAGS = ("賞与・報奨金・慰労金あり", "食事補助あり", "まかない(食事)あり", "入社祝い金あり", "日払い／週払い", "給与前払い可",
               "制服貸与", "社員食堂あり", "資格取得支援", "車通勤可", "寮・社宅あり", "託児所あり")


def mynavi_benefits(job: dict, wage_text: str) -> dict:
    """マイナビバイトの「こだわり条件」と交通費欄から待遇を取り出す（無料・追加アクセスなし）"""
    tags = {nfkc(k.get("label")) for k in job.get("kodawariList") or [] if k.get("label")}
    has = lambda t: nfkc(t) in tags  # noqa: E731
    travel = ((job.get("travelExpensesSupply") or {}).get("label")) or ("支給" if has("交通費支給") else NA)
    allow = []
    m = re.search(r"[（(]([^）)]*(?:時以降|深夜|土日|祝|手当|UP|アップ|加算)[^）)]*)[）)]", nfkc(wage_text))
    if m:
        allow.append(m.group(1))
    return {
        "transport": travel,
        "raise": "あり" if has("昇給あり") else NA,
        "promotion": "あり" if has("社員登用あり") else NA,
        "discount": "あり" if has("社員割引あり") else NA,
        "social_insurance": "完備" if has("社会保険完備") else NA,
        "training": "研修制度あり" if has("研修制度あり") else NA,
        "allowances": "、".join(allow) or NA,
        "features": "、".join(t for t in _EXTRA_TAGS if has(t)) or NA,
    }


def text_benefits(text: str) -> dict:
    """求人本文（自社採用サイトなど）から待遇の記載を拾う"""
    t = nfkc(text)

    def find(*words):
        for w in words:
            i = t.find(w)
            if i >= 0:
                end = t.find("\n", i)
                frag = t[i: end if end > 0 else len(t)].strip(" ・-")
                return frag[:60]
        return NA

    return {
        "transport": find("交通費"), "raise": find("昇給"), "promotion": find("社員登用", "正社員登用", "社員へ"),
        "discount": find("社員割引", "社割", "割引"), "social_insurance": find("社会保険", "社保"),
        "training": find("研修"), "allowances": find("手当", "深夜", "加算"), "features": NA,
    }


def to_hourly(wage_type: str, lo: float | None, hi: float | None) -> tuple[float | None, float | None]:
    """時給のみ採用（日給・月給は勤務時間が不明なため換算しない）"""
    if wage_type == "時給":
        return lo, hi
    return None, None


def stations(shop: dict) -> str:
    out = []
    for a in shop.get("accessList") or []:
        st = ((a.get("routeStation") or {}).get("label")) or ""
        how = ((a.get("trafficFacilitiesDiv") or {}).get("label")) or ""
        t = a.get("timeRequired")
        if st:
            out.append(f"{st}駅 {how}{t}分" if t else f"{st}駅")
    return " / ".join(dict.fromkeys(out))


def _contains_any(text: str, words: list[str]) -> str:
    t = nfkc(text).lower()
    for w in words:
        if nfkc(w).lower() in t:
            return w
    return ""


def _brand_match(job: dict, shop: dict, brands: list[str]) -> str:
    """企業名に含む、または店舗名がブランド名で始まる場合のみ一致（「西松屋 エディオン京橋店」等の館内テナントを除外）"""
    w = _contains_any(job.get("clientName") or "", brands)
    if w:
        return w
    shop_name = re.sub(r"^[\s　]+", "", nfkc(shop.get("clientShopName"))).lower()
    for b in brands:
        if shop_name.startswith(nfkc(b).lower()):
            return b
    return ""


def classify(job: dict, shop: dict, store_cfg: dict, settings: dict, category: str) -> tuple[str, str]:
    """(分類, 根拠)"""
    names = " ".join([job.get("clientName") or "", shop.get("clientShopName") or ""])
    w = _brand_match(job, shop, store_cfg.get("own_keywords") or [])
    if w:
        return CLASS_OWN, f"自社キーワード:{w}"
    direct = settings.get("direct_competitor", {})
    w = _brand_match(job, shop, direct.get("brand_keywords") or [])
    if w:
        return CLASS_DIRECT, f"競合ブランド:{w}"
    head = " ".join([job.get("recruitmentOccupationName") or "", job.get("recruitmentAppealPoint") or "", names])
    w = _contains_any(head, direct.get("title_keywords") or [])
    if w:
        return CLASS_DIRECT, f"職種名:{w}"
    for c in direct.get("categories") or []:
        if c and c in category:
            return CLASS_DIRECT, f"職種分類:{c}"
    body = job.get("jobOfferContent") or ""
    w = _contains_any(body, direct.get("content_keywords") or [])
    if w:
        return CLASS_DIRECT, f"仕事内容:{w}"
    return CLASS_INDIRECT, ""


def build_rows(job: dict, category: str, store_cfg: dict, settings: dict, geocoder, site: str, url: str) -> list[JobRow]:
    lat0, lng0 = store_cfg["lat"], store_cfg["lng"]
    pref_label = store_cfg.get("prefecture_label", "")
    wt, lo, hi, wtext = parse_wage(job.get("wage"))
    hlo, hhi = to_hourly(wt, lo, hi)
    rows = []
    best: JobRow | None = None
    for shop in job.get("shopList") or [{}]:
        lat, lng, how, addr = resolve_location(shop, job, geocoder, pref_label)
        dist = round(haversine_km(lat0, lng0, lat, lng), 2) if lat is not None else None
        cls, why = classify(job, shop, store_cfg, settings, category)
        row = JobRow(
            store_id=store_cfg["id"],
            classification=cls,
            site=site,
            job_id=job.get("jobStockCd") or "",
            url=url,
            company=job.get("clientName") or "",
            shop=shop.get("clientShopName") or "",
            occupation=nfkc(job.get("recruitmentOccupationName")),
            category=category,
            employment=((job.get("employeeFigure") or {}).get("label")) or "",
            wage_type=wt,
            wage_min=lo,
            wage_max=hi,
            wage_text=wtext,
            hourly_min=hlo,
            hourly_max=hhi,
            address=addr,
            station=stations(shop),
            lat=round(lat, 6) if lat is not None else None,
            lng=round(lng, 6) if lng is not None else None,
            location_method=how,
            distance_km=dist,
            shift=nfkc((job.get("shift") or {}).get("note"))[:400],
            work_content=nfkc(job.get("jobOfferContent")),
            publication_start=(job.get("publicationStartDate") or "")[:10],
            publication_end=(job.get("publicationEndDate") or "")[:10],
            matched_by=why,
            benefits=mynavi_benefits(job, wtext),
        )
        if best is None or (dist is not None and (best.distance_km is None or dist < best.distance_km)):
            best = row
    if best:
        rows.append(best)  # 複数勤務地の求人は店舗に最も近い勤務地で代表させる
    return rows
