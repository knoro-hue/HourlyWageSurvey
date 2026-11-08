"""ネットワーク不要の単体テスト: python -m pytest -q"""
from wage_survey.geo import haversine_km, normalize_address
from wage_survey.http import RobotsRules
from wage_survey.nuxt_state import parse_nuxt
from wage_survey.records import extract_workplace, parse_wage

NUXT_HTML = """<script>window.__NUXT__=(function(a,b,c,d){return {state:{search:{searchSolrList:{searchCount:623,
jobList:[{jobStockCd:"J0137519065",clientCd:"002479700049",clientName:a,wage:{label:b,wageAmount:1300,
specialWageAmount:"時給1300～1625円"},flag:!0,none:void 0,m:new Map([]),list:[c,d,-1.5,"x\\u3042\\n"]}]}}}}}("会社A","時給",34.65,135.5));</script>"""


def test_parse_nuxt():
    d = parse_nuxt(NUXT_HTML)
    sl = d["state"]["search"]["searchSolrList"]
    assert sl["searchCount"] == 623
    j = sl["jobList"][0]
    assert j["clientName"] == "会社A"
    assert j["flag"] is True and j["none"] is None and j["m"] is None
    assert j["list"] == [34.65, 135.5, -1.5, "xあ\n"]


def test_parse_wage_range():
    assert parse_wage({"label": "時給", "wageAmount": 1300, "specialWageAmount": "時給1300～1625円"})[:3] == ("時給", 1300.0, 1625.0)
    assert parse_wage({"label": "時給", "wageAmount": 1430, "specialWageAmount": "時給1430円"})[:3] == ("時給", 1430.0, None)


def test_robots_wildcard():
    r = RobotsRules("User-Agent: bingbot\nDisallow: /x/\n\nUser-Agent: *\nDisallow: /cl/\nDisallow: /*?*pageNo=\n")
    assert r.allowed("/osaka/city-262/town-144/ss-3/")
    assert not r.allowed("/osaka/city-262/?pageNo=2")
    assert r.allowed("/cl-002479700049/job-1/")
    assert not r.allowed("/cl/abc")
    assert r.allowed("/x/")


def test_workplace_and_address():
    assert extract_workplace("株式会社エンクルー　※勤務地：［大阪市浪速区難波中］周辺 / ") == "大阪市浪速区難波中"
    assert extract_workplace("", "※上記は本社所在地になります。\n\n«勤務地»\n大阪市浪速区難波中") == "大阪市浪速区難波中"
    assert extract_workplace("トライ　※勤務地：大阪府大阪市浪速区 ") == "大阪府大阪市浪速区"
    assert normalize_address("大阪府大阪市浪速区日本橋3丁目6-22 布谷ビル1F～4F") == "大阪府大阪市浪速区日本橋3丁目6-22"
    assert normalize_address("大阪府大阪市浪速区難波中２－１０－７０なんばパークス店１階") == "大阪府大阪市浪速区難波中2-10-70"


def test_haversine():
    assert abs(haversine_km(34.663044, 135.505203, 34.70249, 135.49595) - 4.47) < 0.05


def test_geocode_specificity():
    from wage_survey.geo import is_specific_match

    assert not is_specific_match("大阪府なんば駅", "大阪府")
    assert is_specific_match("大阪府大阪市浪速区日本橋3丁目6-22", "大阪府大阪市浪速区日本橋三丁目６番２２号")
    assert is_specific_match("大阪市浪速区難波中", "大阪府大阪市浪速区難波中")
    assert is_specific_match("大阪府大阪市浪速区", "大阪府大阪市浪速区")
    assert not is_specific_match("大阪府大阪市浪速区", "大阪府堺市北区")


def test_brand_match_excludes_tenants():
    from wage_survey.records import CLASS_DIRECT, CLASS_INDIRECT, classify

    settings = {"direct_competitor": {"brand_keywords": ["エディオン", "ケーズデンキ"]}}
    store = {"own_keywords": ["ドスパラ"]}
    tenant = classify({"clientName": "株式会社西松屋チェーン"}, {"clientShopName": "西松屋チェーン 1113 エディオン京橋店"}, store, settings, "")
    assert tenant[0] == CLASS_INDIRECT
    ks = classify({"clientName": "株式会社関西ケーズデンキ"}, {"clientShopName": "ケーズデンキ 東住吉中野店"}, store, settings, "")
    assert ks[0] == CLASS_DIRECT


def test_snar_parse():
    from wage_survey.snar import parse_jobs, parse_wage_text

    html = ('<div id="rpt_GroupItem_customBg_11" class="whiteBg"><div class="contents"><h3 class="tit">'
            '<a id="rpt_GroupItem_lkb_Title_11" href="#">販売スタッフ_ドスパラなんば店</a></h3>'
            '<p> ゲーミングパソコン販売！<br />時給：1,250円以上+交通費支給 <a>［more］</a></p>'
            '<dl class="guidelines"><dt>職種</dt><dd>販売職 ＞店舗接客・販売（アルバイト）</dd>'
            '<dt>勤務地</dt><dd>大阪府 ＞大阪市（ドスパラ大阪・なんば店）</dd></dl></div></div>')
    jobs = parse_jobs(html)
    assert len(jobs) == 1 and "なんば店" in jobs[0]["title"] and "なんば店" in jobs[0]["location"]
    assert parse_wage_text(jobs[0]["body"])[:2] == (1250.0, None)


def test_area_label_and_selection():
    from wage_survey.areas import clean_label, select_areas
    from wage_survey.geo import is_specific_match

    assert clean_label("23区全体千代田区") == "千代田区"
    assert clean_label("東京都下（23区外）八王子市") == "八王子市"
    assert clean_label("道東エリア阿寒郡") == "阿寒郡"
    assert clean_label("大阪市浪速区") == "大阪市浪速区"
    assert is_specific_match("福岡県糟屋郡", "福岡県糟屋郡粕屋町")
    master = {"tokyo/city-32": {"pref": "tokyo", "label": "23区全体千代田区", "lat": None, "lng": None, "kind": "city"}}
    st = {"address": "東京都千代田区外神田3-11-2", "lat": 35.70, "lng": 139.77}
    assert select_areas(st, master, 5) == ["tokyo/city-32"]


def test_hourly_values_dedup_and_far_location():
    from wage_survey.collector import _fix_far_location
    from wage_survey.records import JobRow
    from wage_survey.report import hourly_values

    def row(company, wage, dist=1.0):
        return JobRow(store_id="s", classification="③直接競合", site="x", job_id="1", url="", company=company, shop="",
                      occupation="家電販売", category="", employment="", wage_type="時給", wage_min=wage, wage_max=None,
                      wage_text="", hourly_min=wage, hourly_max=None, address="東京都新宿区", station="", lat=None, lng=None,
                      location_method="掲載座標", distance_km=dist, shift="", work_content="", publication_start="", publication_end="")
    rows = [row("派遣A", 2050)] * 77 + [row("B", 1300), row("C", 1250)]
    assert sorted(hourly_values(rows)) == [1250, 1300, 2050]
    r = row("派遣A", 1500, dist=350.0)
    store = {"lat": 38.26, "lng": 140.88}
    master = {"miyagi/city-43/town-11": {"lat": 38.27, "lng": 140.87, "label": "仙台市青葉区"}}
    _fix_far_location(r, {"miyagi/city-43/town-11"}, store, master, 10)
    assert r.distance_km is not None and r.distance_km < 3 and "検索エリア" in r.location_method


def test_manual_rows_from_folder(tmp_path):
    from wage_survey.collector import load_manual_rows

    class G:
        def geocode(self, q):
            return (34.66, 135.50) if "浪速区" in q else None

        def station(self, name, pref):
            return None

    d = tmp_path / "manual"
    d.mkdir()
    head = "store_id,classification,site,company,shop,occupation,employment,wage_type,wage_min,wage_max,address,work_content,url,checked_date\n"
    (d / "indeed_a.csv").write_text("﻿" + head + "s1,direct,Indeed,株式会社ノジマ,,販売,アルバイト,時給,1300,1500,大阪市浪速区,,https://x/1,2099-01-01\n"
                                    "s1,indirect,Indeed,A社,,軽作業,アルバイト,時給,1200,,遠いところ,,https://x/2,2000-01-01\n", encoding="utf-8")
    (d / "indeed_b.csv").write_text(head + "s1,direct,Indeed,株式会社ノジマ,,販売,アルバイト,時給,1300,1500,大阪市浪速区,,https://x/1,2099-01-01\n", encoding="utf-8")
    store = {"id": "s1", "lat": 34.66, "lng": 135.50, "prefecture_label": "大阪府"}
    rows = load_manual_rows(store, {"manual_jobs_csv": str(tmp_path / "none.csv"), "manual_jobs_dir": str(d)}, G())
    assert len(rows) == 1 and rows[0].classification == "③直接競合" and rows[0].distance_km == 0 and rows[0].hourly_min == 1300


def test_main_has_no_shadowed_imports():
    """関数内 import で write_index 等がローカル変数扱いになり、調査の最後で落ちる不具合の再発防止"""
    from wage_survey import main as m

    assert "write_index" not in m.main.__code__.co_varnames
    assert "write_dashboard" not in m.main.__code__.co_varnames


def test_main_end_to_end_without_network(tmp_path, monkeypatch):
    """収集部分を差し替えて main を最後まで通す（レポート・ダッシュボード・メイン画面の出力まで）"""
    import shutil

    from wage_survey import main as m
    from wage_survey.collector import StoreResult

    monkeypatch.setattr(m, "collect_store", lambda st, *a, **k: StoreResult(store=st))
    shutil.copytree("wage_survey/templates", tmp_path / "t", dirs_exist_ok=True)
    rc = m.main(["--store", "dospara-namba", "--out", str(tmp_path / "reports"), "--docs", str(tmp_path / "docs"),
                 "--areas", str(tmp_path / "none.json"), "--date", "2099-01-01"])
    assert rc in (0, 1)
    assert (tmp_path / "docs" / "stores.json").exists()
