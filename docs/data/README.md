# ドスパラ全店 時給調査データ（AI・表計算ソフト向け）

このZIPは、ドスパラ各店舗の周辺アルバイト求人を自動収集・集計したデータです。
ChatGPT・Claude・Gemini などのAIや Excel にそのまま読み込んで分析できます。
作成日: 2026-11-12／対象店舗: 42店（調査済み 42店）／求人: 54057件

## ファイル
- stores.csv   … 店舗ごとの集計（1行=1店舗）
- jobs.csv     … 求人一覧（1行=1店舗×1求人。同じ求人が近くの複数店舗に出ることがある）
- benefits.csv … ①自店と③直接競合の待遇比較（交通費・昇給・登用・社割・社保・研修・手当）
- README.md    … この説明

## 区分の定義
- ①自店      … ドスパラの求人（自社採用サイト tw-saiyou.snar.jp の掲載値）
- ③直接競合  … 家電量販店・パソコン専門店・PCパーツ店・中古PC/スマホ店の求人（店舗から10km以内）
- ②間接競合  … それ以外の全職種の求人（店舗から5km以内）
- 自社他店    … 近くにある別のドスパラ店舗の求人

## stores.csv の列
store_id, store_name, region(地域), prefecture, run_date(調査日),
own_hourly(①自店の募集時給・円), indirect_median(②周辺の時給中央値・円), direct_median(③直接競合の時給中央値・円),
gap_vs_indirect(①−②・円。マイナスは相場より低い), gap_vs_direct(①−③・円),
indirect_n / direct_n(時給求人の件数), area_postings(周辺エリアのマイナビバイト掲載件数), report_url(店舗レポート),
*_excl_dispatch(派遣社員・紹介予定派遣の求人を除いた中央値と件数)

## jobs.csv の列
store_id, store_name, class(区分), distance_km(店舗からの直線距離・km), company(企業), shop(店舗・勤務地名),
occupation(職種名), category(職種分類), wage_type(時給/日給/月給), wage_text(給与表記),
hourly(時給の下限・円。時給以外は空), station(最寄駅), location_basis(距離の根拠),
site(求人サイト), url(求人URL), work_content(仕事内容の抜粋・最大160字), benefits(求人に明記された待遇), posted_date(掲載開始日),
employment(雇用形態), dispatch(1=派遣・紹介予定派遣の求人)

## 注意
- 時給は求人の「下限額」。日給・月給は時給に換算していない。中央値は同じ企業・職種・時給の求人を1件として計算。
- ②③はマイナビバイトの掲載求人。サイトの規則により、エリア×職種ごとに先頭30件までの取得（件数そのものは area_postings が正確）。
- Indeed・バイトル・タウンワークの求人は含まない。
- 「記載なし」や空欄は、制度が無いことを意味しない（求人票に書かれていないだけ）。
- 派遣会社の求人は、本社住所ではなく勤務地の表記や最寄駅から位置を推定している（location_basis 参照）。

## AIへの質問例
- 「stores.csv で gap_vs_indirect が小さい（相場より低い）店舗を10店挙げ、地域ごとの傾向を説明して」
- 「jobs.csv から店舗ID dospara-namba の③直接競合だけを抜き出し、時給と待遇を比較表にして」
- 「benefits.csv を使い、自店の求人票に足りない待遇の記載を店舗ごとに指摘して」
