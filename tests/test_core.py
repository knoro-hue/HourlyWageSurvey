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
