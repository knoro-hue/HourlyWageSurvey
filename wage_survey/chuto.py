"""中途採用（正社員・契約社員）の年収相場: data/doda/*.csv を集計して docs/chuto/ を出力する

CSV は担当者がローカルの Claude アプリで doda の画面を読み取って作り、GitHub の画面からアップロードする。
このモジュールは doda にアクセスしない（ネット接続は勤務地のジオコーディングのみ）。

使い方:
  python -m wage_survey.chuto              # 集計して docs/chuto/ を更新
  python -m wage_survey.chuto --today 2026-11-12
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import json
import logging
import os
import re
import shutil
import statistics
import sys
from dataclasses import asdict, dataclass

import yaml

from .geo import Geocoder, haversine_km
from .records import CLASS_DIRECT, CLASS_OWN, classify, nfkc

log = logging.getLogger(__name__)
JST = dt.timezone(dt.timedelta(hours=9))
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates", "chuto")
COLUMNS = ["store_id", "search_area", "site", "company", "job_title", "employment", "salary_min_man", "salary_max_man",
           "monthly_min_yen", "bonus", "annual_holidays", "overtime_avg_h", "transfer", "housing", "requirements",
           "benefits", "other_conditions", "work_location", "job_category", "source_level", "url", "checked_date"]

# ---- 待遇の正規化：文章を決まった選択肢（タグ）にそろえて比較できるようにする ----
# 指示文で決めた選択肢の値も、以前の CSV の文章（transfer_note・other_benefits・tags など）も同じ規則で読む。
# タグが付かない＝「記載なし」（求人一覧の画面だけでは分からないことが多い）。推測では付けない。
TRANSFER_CHOICES = ["転勤なし", "転居なし", "選択可", "転勤あり"]  # 転居なし＝転居を伴う転勤なし（近隣の異動はあり得る）
ATTR_VOCAB: dict[str, list[tuple[str, str]]] = {
    "housing": [
        ("社宅・寮", r"社宅|社員寮|独身寮|(?<![a-zA-Z])寮(?!費)|借り?上げ"),
        ("住宅手当", r"住宅手当|住居手当|家賃補助|家賃手当|住宅補助|住宅支援|住まい手当"),
        ("引越し補助", r"引っ?越し?(?:補助|サポート|手当|費用)|転居(?:費用|補助|手当)|赴任手当"),
    ],
    "requirements": [
        ("未経験可", r"未経験(?:歓迎|可|OK|者歓迎)|経験不問|職種未経験|業種未経験"),
        ("学歴不問", r"学歴不問|学歴・経験不問"),
        ("高卒以上", r"高卒以上|高校卒業以上"),
        ("大卒以上", r"大卒以上|大学卒業以上|四大卒"),
        ("運転免許", r"免許"),
        ("販売・接客経験", r"(?:販売|接客)[^。|｜/／]{0,12}(?<!未)経験(?!不問)"),
        ("社会人経験", r"社会人経験|就業経験"),
        ("管理職経験", r"(?:店長|管理職|マネジメント|マネージャー)(?:の|として|等の|などの)?(?:実務)?経験(?!不問)"),
        ("PCスキル", r"PCスキル|パソコン(?:スキル|操作)|PC操作|Excel|エクセル"),
    ],
    "benefits": [
        ("賞与", r"賞与|ボーナス"),
        ("昇給", r"昇給"),
        ("交通費", r"交通費|通勤手当"),
        ("退職金", r"退職金|退職手当|退職年金|確定拠出|確定給付|企業年金"),
        ("家族手当", r"家族手当|扶養手当"),
        ("資格支援", r"資格(?:取得|手当|支援|奨励)"),
        ("社員割引", r"社員割引|社割|従業員割引|従業員向けクーポン"),
        ("持株会", r"持株"),
        ("育児支援", r"保育|育児|育休|産休|時短勤務"),
        ("研修制度", r"研修"),
    ],
    "other": [
        ("完全週休2日", r"完全週休2日|完全週休二日"),
        ("土日休み", r"土日"),
        ("車通勤可", r"マイカー|自動車通勤|車通勤"),
        ("リモート可", r"リモート|在宅勤務"),
        ("副業可", r"副業"),
        ("上場企業", r"上場企業"),
    ],
}
ATTR_LABELS = {"transfer": "転勤", "housing": "社宅・住宅補助", "requirements": "応募条件・必要資格",
               "benefits": "福利厚生", "other": "その他"}
# どの列の文章からタグを拾うか（新しい選択肢の列＋以前の CSV の文章の列）
_ATTR_FIELDS = {
    "housing": ["housing", "company_housing_note", "rent_subsidy_note", "other_benefits", "work_location", "job_title"],
    "requirements": ["requirements", "required_qualifications", "required_experience", "education_requirement", "tags", "job_title"],
    "benefits": ["benefits", "other_benefits", "commute_allowance", "salary_text"],
    "other": ["other_conditions", "holiday_text", "other_benefits", "tags", "work_location", "job_title"],
}
_YES = {"有", "あり", "○", "yes", "true", "1"}
GROUP_DIRECT = "③直接競合"
GROUP_OTHER = "その他"
GROUP_OWN = "自社"
_MUNI = re.compile(r"^(.+?市.+?区|.+?郡.+?[町村]|.+?[市区町村])")
_PREF = re.compile(r"^(東京都|北海道|(?:京都|大阪)府|.{2,3}県)")


@dataclass
class ChutoJob:
    store_id: str
    site: str
    company: str
    job_title: str
    employment: str
    salary_min_man: float | None
    salary_max_man: float | None
    monthly_min_yen: float | None
    work_location: str
    job_category: str
    url: str
    checked_date: str
    search_area: str = ""
    source_level: str = ""      # search＝検索結果の一覧だけ / detail＝求人詳細も確認
    transfer: str = ""          # TRANSFER_CHOICES のいずれか。空＝記載なし
    housing: list = None
    requirements: list = None
    benefits: list = None
    other: list = None
    annual_holidays: float | None = None
    overtime_h: float | None = None
    source_file: str = ""
    distance_km: float | None = None
    location_method: str = ""
    group: str = ""
    matched_by: str = ""


def _num(v) -> float | None:
    s = nfkc(v).replace(",", "").replace("万円", "").replace("万", "").replace("円", "").strip()
    if not s:
        return None
    try:
        x = float(s)
    except ValueError:
        return None
    return x if x > 0 else None


def _date(v) -> str:
    s = nfkc(v).strip().replace("/", "-")
    try:
        return dt.date.fromisoformat(s).isoformat()
    except ValueError:
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
        if m:
            try:
                return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
            except ValueError:
                return ""
    return ""


def _tags(text: str, vocab: list[tuple[str, str]]) -> list[str]:
    t = nfkc(text)
    return [name for name, pat in vocab if re.search(pat, t, re.I)]


def parse_transfer(r: dict) -> str:
    col = nfkc(r.get("transfer")).strip()
    texts = " ".join(nfkc(r.get(k)) for k in ("transfer", "transfer_note", "tags", "work_location", "job_title"))
    no_move = re.search(r"転居を?伴う(?:転勤|異動)(?:は|が)?(?:なし|無し|ありません|ない)|転居なし", texts)
    has = col in ("有", "あり", "転勤あり") or re.search(r"転勤(?:の可能性)?(?:あり|有り|がある|の可能性)|全国転勤", texts)
    none = col in ("無", "なし", "転勤なし") or re.search(r"転勤(?:は|が)?(?:なし|無し|ありません|ない)", texts)
    if col in TRANSFER_CHOICES:
        return col
    if has and (none or no_move) and not col:
        return "選択可"
    if col in ("有", "あり"):
        return "転勤あり"
    if no_move:
        return "転居なし"
    if none:
        return "転勤なし"
    if has:
        return "転勤あり"
    return ""


def parse_attributes(r: dict) -> dict:
    """1行（列名→値）から比較用の待遇タグを作る"""
    out = {"transfer": parse_transfer(r)}
    for key, fields in _ATTR_FIELDS.items():
        text = " | ".join(nfkc(r.get(f)) for f in fields if r.get(f))
        out[key] = _tags(text, ATTR_VOCAB[key])
    if nfkc(r.get("company_housing")).strip() in _YES and "社宅・寮" not in out["housing"]:
        out["housing"].insert(0, "社宅・寮")
    if nfkc(r.get("rent_subsidy")).strip() in _YES and "住宅手当" not in out["housing"]:
        out["housing"].append("住宅手当")
    if nfkc(r.get("bonus")).strip() in _YES and "賞与" not in out["benefits"]:
        out["benefits"].insert(0, "賞与")
    if nfkc(r.get("inexperienced_ok")).strip() in _YES and "未経験可" not in out["requirements"]:
        out["requirements"].insert(0, "未経験可")
    for key in ("housing", "requirements", "benefits", "other"):  # 選択肢の順にそろえる
        order = [n for n, _ in ATTR_VOCAB[key]]
        out[key] = sorted(set(out[key]), key=order.index)
    hol = _num(r.get("annual_holidays"))
    if hol is None:
        m = re.search(r"年間休日\D{0,4}(\d{2,3})", nfkc(r.get("holiday_text")))
        hol = float(m.group(1)) if m else None
    out["annual_holidays"] = hol if hol and 50 <= hol <= 200 else None
    ot = _num(r.get("overtime_avg_h"))
    out["overtime_h"] = ot if ot is not None and ot <= 100 else None
    return out


def read_csv_file(path: str) -> list[ChutoJob]:
    """UTF-8（BOM 可）。ヘッダの空白や大文字小文字の揺れは吸収。必須列 store_id が空の行は捨てる"""
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        for raw in rd:
            r = {nfkc(k).strip().lower(): (v or "") for k, v in raw.items() if k}
            sid = nfkc(r.get("store_id")).strip()
            if not sid:
                continue
            lo, hi = _num(r.get("salary_min_man")), _num(r.get("salary_max_man"))
            # 万円の欄に円で書かれた値（例: 3500000）は万円に直す
            lo = lo / 10000 if lo and lo >= 10000 else lo
            hi = hi / 10000 if hi and hi >= 10000 else hi
            out.append(ChutoJob(
                store_id=sid,
                site=nfkc(r.get("site")).strip() or "doda",
                company=nfkc(r.get("company")).strip(),
                job_title=nfkc(r.get("job_title")).strip(),
                employment=nfkc(r.get("employment")).strip(),
                salary_min_man=lo,
                salary_max_man=hi,
                monthly_min_yen=_num(r.get("monthly_min_yen")),
                work_location=nfkc(r.get("work_location")).strip(),
                job_category=nfkc(r.get("job_category")).strip(),
                url=nfkc(r.get("url")).strip(),
                checked_date=_date(r.get("checked_date")),
                search_area=nfkc(r.get("search_area")).strip(),
                source_level=nfkc(r.get("source_level")).strip().lower(),
                source_file=os.path.basename(path),
                **parse_attributes(r),
            ))
    return out


def load_csvs(csv_dir: str) -> list[ChutoJob]:
    rows = []
    for p in sorted(glob.glob(os.path.join(csv_dir, "*.[cC][sS][vV]"))):
        try:
            rows += read_csv_file(p)
        except (OSError, UnicodeDecodeError, csv.Error) as e:
            log.warning("CSV を読めません（UTF-8 で保存してください）: %s (%s)", p, e)
    return rows


def _url_key(u: str) -> str:
    u = u.split("#", 1)[0].rstrip("/")
    return re.sub(r"^https?://", "", u).lower()


def dedupe(rows: list[ChutoJob]) -> list[ChutoJob]:
    """同じ店舗の同じ求人（URL。URL が無い行は 企業名＋求人名＋勤務地）は確認日の新しい1行に"""
    best: dict[tuple, ChutoJob] = {}
    for r in rows:
        key = (r.store_id, _url_key(r.url)) if r.url else (r.store_id, "", r.company, r.job_title, r.work_location)
        cur = best.get(key)
        if cur is None or r.checked_date > cur.checked_date:
            best[key] = r
    return list(best.values())


def drop_expired(rows: list[ChutoJob], today: dt.date, max_age_days: int = 90) -> tuple[list[ChutoJob], int]:
    """確認日から max_age_days 日を超えた行・確認日が無い/不正な行を除外。(残り, 除外数)"""
    keep = []
    for r in rows:
        if r.checked_date:
            age = (today - dt.date.fromisoformat(r.checked_date)).days
            if -1 <= age <= max_age_days:  # 時差で1日先の日付は許容
                keep.append(r)
    return keep, len(rows) - len(keep)


def muni_of(address: str) -> str:
    """住所 → 都道府県＋市区町村（例: 大阪府大阪市浪速区）"""
    a = nfkc(address).replace(" ", "")
    m = _PREF.match(a)
    pref = m.group(1) if m else ""
    rest = a[len(pref):]
    mm = _MUNI.match(rest)
    return pref + (mm.group(1) if mm else "")


def locate(job: ChutoJob, store: dict, geocoder) -> tuple[float | None, float | None, str]:
    """勤務地（住所 or 最寄駅）を座標にする。都道府県が無ければ店舗の都道府県を補う"""
    loc = nfkc(job.work_location).strip()
    pref = store.get("prefecture_label", "")
    if loc and not _looks_like_pr(loc):
        pt = _locate_text(loc, pref, geocoder)
        if pt:
            return pt
    # 勤務地が書かれていない・宣伝文しか無い場合は、検索した市区町村（doda がその地域の求人として出したもの）の中心で代用
    area = nfkc(job.search_area).strip()
    if area:
        q = area if _PREF.match(area) else pref + area
        pt = geocoder.geocode(q)
        if pt:
            return pt[0], pt[1], "検索エリア"
    return None, None, "不明"


def _looks_like_pr(s: str) -> bool:
    """「【転勤なし】…全国の直営店…」のような宣伝文は住所として扱わない"""
    return len(s) > 60 or bool(re.search(r"[【】★◎■□◆※《》＼!！]|全国|各店舗|いずれか|または|など", s))


def _locate_text(loc: str, pref: str, geocoder):
    parts = [p for p in re.split(r"[／/、,]|\s+", loc) if p]
    for p in parts or [loc]:
        p = re.sub(r"^(最寄(?:り)?駅?|勤務地)[:：]?", "", p)
        m = re.search(r"([^\s「」()（）]+?)駅", p)
        if m and not re.search(r"\d", p.split(m.group(0))[0]):
            pt = geocoder.station(m.group(1), pref)
            if pt:
                return pt[0], pt[1], "最寄駅"
        if re.search(r"[市区町村郡]", p):
            q = p if _PREF.match(p) else pref + p
            pt = geocoder.geocode(q)
            if pt:
                return pt[0], pt[1], "住所"
            muni = muni_of(q)
            if muni and muni != q:
                pt = geocoder.geocode(muni)
                if pt:
                    return pt[0], pt[1], "市区町村の中心"
    return None


def classify_job(job: ChutoJob, store: dict, settings: dict) -> tuple[str, str]:
    """既存の直接競合判定（records.classify）を流用"""
    cls, why = classify({"clientName": job.company, "recruitmentOccupationName": job.job_title},
                        {"clientShopName": ""}, store, settings, job.job_category)
    if cls == CLASS_OWN:
        return GROUP_OWN, why
    if cls == CLASS_DIRECT:
        return GROUP_DIRECT, why
    return GROUP_OTHER, ""


def filter_by_distance(rows: list[ChutoJob], radius_km: float) -> tuple[list[ChutoJob], int, int]:
    """(半径内, 半径外の件数, 位置不明の件数)"""
    near = [r for r in rows if r.distance_km is not None and r.distance_km <= radius_km]
    far = sum(1 for r in rows if r.distance_km is not None and r.distance_km > radius_km)
    unknown = sum(1 for r in rows if r.distance_km is None)
    return near, far, unknown


def annual_stats(rows: list[ChutoJob]) -> dict:
    v = sorted(r.salary_min_man for r in rows if r.salary_min_man)
    if not v:
        return {"n": 0}
    q = statistics.quantiles(v, n=4, method="inclusive") if len(v) >= 2 else [v[0], v[0], v[0]]
    rd = lambda x: round(x, 1)  # noqa: E731
    return {"n": len(v), "p25": rd(q[0]), "median": rd(statistics.median(v)), "p75": rd(q[2]), "min": v[0], "max": v[-1]}


def _median(v: list[float]) -> float | None:
    return round(statistics.median(v), 1) if v else None


def attr_summary(jobs: list[ChutoJob]) -> dict:
    """待遇の比較用集計。率は「記載あり」の割合（記載なしは無いとは限らない）"""
    n = len(jobs)
    out = {"n": n, "detail_n": sum(1 for j in jobs if j.source_level == "detail")}
    known = [j.transfer for j in jobs if j.transfer]
    out["transfer"] = {"known": len(known), **{c: known.count(c) for c in TRANSFER_CHOICES}}
    out["no_transfer_rate"] = round(sum(1 for t in known if t in ("転勤なし", "転居なし")) / len(known), 3) if known else None
    out["housing_rate"] = round(sum(1 for j in jobs if {"社宅・寮", "住宅手当"} & set(j.housing or [])) / n, 3) if n else None
    for key in ("housing", "requirements", "benefits", "other"):
        out[key] = {name: (round(sum(1 for j in jobs if name in (getattr(j, key) or [])) / n, 3) if n else None)
                    for name, _ in ATTR_VOCAB[key]}
    hol = [j.annual_holidays for j in jobs if j.annual_holidays]
    ot = [j.overtime_h for j in jobs if j.overtime_h is not None]
    out["holidays"] = {"n": len(hol), "median": _median(hol)}
    out["overtime"] = {"n": len(ot), "median": _median(ot)}
    return out


def benchmark_rows(cfg: dict) -> dict:
    b = (cfg or {}).get("benchmarks") or {}
    items = []
    for it in b.get("items") or []:
        annual = it.get("annual_man")
        mc, bonus = it.get("monthly_contractual_yen"), it.get("annual_bonus_yen")
        if annual is None and mc is not None and bonus is not None:
            annual = round((float(mc) * 12 + float(bonus)) / 10000, 1)
        items.append({"occupation": it.get("occupation", ""), "monthly_scheduled_yen": it.get("monthly_scheduled_yen"),
                      "monthly_contractual_yen": mc, "annual_bonus_yen": bonus, "annual_man": annual})
    return {"source": b.get("source", ""), "source_url": b.get("source_url", ""), "year": b.get("year"), "items": items}


def build(stores: list[dict], settings: dict, cfg: dict, geocoder, today: dt.date) -> dict:
    radius = float(cfg.get("radius_km", 10))
    raw = load_csvs(cfg.get("csv_dir", "data/doda"))
    rows = dedupe(raw)
    rows, expired = drop_expired(rows, today, int(cfg.get("max_age_days", 90)))
    by_store: dict[str, list[ChutoJob]] = {}
    for r in rows:
        by_store.setdefault(r.store_id, []).append(r)
    known = {s["id"] for s in stores}
    unknown_ids = sorted(set(by_store) - known)
    for sid in unknown_ids:
        log.warning("config/stores.yaml に無い store_id の行を無視: %s", sid)
    out_stores = []
    for st in stores:
        jobs = by_store.get(st["id"], [])
        for j in jobs:
            lat, lng, how = locate(j, st, geocoder)
            j.location_method = how
            j.distance_km = round(haversine_km(st["lat"], st["lng"], lat, lng), 2) if lat is not None and st.get("lat") else None
            j.group, j.matched_by = classify_job(j, st, settings)
        near, far, unknown = filter_by_distance(jobs, radius)
        market = [j for j in near if j.group != GROUP_OWN]
        direct = [j for j in market if j.group == GROUP_DIRECT]
        monthly_only = [j for j in market if not j.salary_min_man and j.monthly_min_yen]
        dates = sorted(j.checked_date for j in jobs if j.checked_date)
        out_stores.append({
            "id": st["id"], "name": st["name"], "region": st.get("region", ""), "pref": st.get("prefecture_label", ""),
            "address": st.get("address", ""), "muni": muni_of(st.get("address", "")),
            "checked_from": dates[0] if dates else None, "checked_to": dates[-1] if dates else None,
            "total": len(jobs), "near": len(near), "far": far, "unknown_location": unknown,
            "all": annual_stats(market), "direct": annual_stats(direct), "other": annual_stats([j for j in market if j.group == GROUP_OTHER]),
            "attr_all": attr_summary(market), "attr_direct": attr_summary(direct),
            "monthly_only_n": len(monthly_only), "no_salary_n": sum(1 for j in market if not j.salary_min_man and not j.monthly_min_yen),
            "jobs": [asdict(j) for j in sorted(near, key=lambda j: (j.group != GROUP_DIRECT, j.distance_km or 0))],
            "excluded": [asdict(j) for j in jobs if j not in near],
        })
    return {
        "generated": dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M"), "today": today.isoformat(),
        "radius_km": radius, "max_age_days": int(cfg.get("max_age_days", 90)),
        "csv_rows": len(raw), "dup_removed": len(raw) - len(dedupe(raw)), "expired": expired, "unknown_store_ids": unknown_ids,
        "job_categories": cfg.get("job_categories") or [], "detail_pages_max": int(cfg.get("detail_pages_max", 10)),
        "vocab": {"transfer": TRANSFER_CHOICES, **{k: [n for n, _ in v] for k, v in ATTR_VOCAB.items()}},
        "attr_labels": ATTR_LABELS, "benchmarks": benchmark_rows(cfg), "stores": out_stores,
    }


def write_pages(data: dict, docs_dir: str = "docs"):
    base = os.path.join(docs_dir, "chuto")
    os.makedirs(base, exist_ok=True)
    stores = data["stores"]
    summary = {k: v for k, v in data.items() if k != "stores"}
    summary["stores"] = [{k: v for k, v in s.items() if k not in ("jobs", "excluded")} for s in stores]
    with open(os.path.join(base, "data.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False)
    for name in ("index.html", "prompt.html", "style.css"):
        shutil.copyfile(os.path.join(TEMPLATE_DIR, name), os.path.join(base, name))
    for s in stores:
        d = os.path.join(base, s["id"])
        os.makedirs(d, exist_ok=True)
        shutil.copyfile(os.path.join(TEMPLATE_DIR, "store.html"), os.path.join(d, "index.html"))
        with open(os.path.join(d, "data.json"), "w", encoding="utf-8") as f:
            json.dump({**{k: v for k, v in data.items() if k != "stores"}, "store": s}, f, ensure_ascii=False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="中途採用 年収相場（doda の手動取得 CSV を集計）")
    ap.add_argument("--stores", default="config/stores.yaml")
    ap.add_argument("--settings", default="config/settings.yaml")
    ap.add_argument("--config", default="config/chuto.yaml")
    ap.add_argument("--docs", default="docs")
    ap.add_argument("--csv-dir", default=None, help="CSV の置き場（省略時は config の csv_dir）")
    ap.add_argument("--today", default=None, help="基準日 YYYY-MM-DD（省略時は今日 JST）")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    def load(p):
        with open(p, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    settings, cfg = load(a.settings), load(a.config)
    if a.csv_dir:
        cfg["csv_dir"] = a.csv_dir
    defaults = settings.get("store_defaults") or {}
    stores = [{**defaults, **s} for s in (load(a.stores).get("stores") or [])]
    from .http import PoliteSession

    geocoder = Geocoder(PoliteSession(delay_sec=1.0), settings.get("geocode_cache", "data/geocode_cache.json"))
    today = dt.date.fromisoformat(a.today) if a.today else dt.datetime.now(JST).date()
    try:
        data = build(stores, settings, cfg, geocoder, today)
    finally:
        geocoder.save()
    write_pages(data, a.docs)
    got = sum(1 for s in data["stores"] if s["total"])
    msg = f"中途採用 年収相場: CSV {data['csv_rows']} 行（重複 {data['dup_removed']}・期限切れ {data['expired']} を除外）／取得済み {got} / {len(data['stores'])} 店"
    log.info(msg)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
