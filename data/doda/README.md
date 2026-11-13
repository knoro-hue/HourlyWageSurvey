# 中途採用（doda）CSV の置き場

ここに置いた `*.csv` を集計して [中途採用 年収相場ページ](https://knoro-hue.github.io/HourlyWageSurvey/chuto/) を作ります（アップロード後、数分で反映）。

> **部内の判断材料のみ。外部へ提供しないでください。** doda の利用規約に「私的利用の範囲を超える利用・第三者提供の禁止」があります（適用範囲は要確認）。取得は人が画面の前にいる状態で、1店舗ずつ・少量・低頻度（目安：1店舗あたり月1回まで）で行ってください。サーバーや自動化ツールで doda に取りに行くことはしません。

## 手順
1. [指示文ジェネレータ](https://knoro-hue.github.io/HourlyWageSurvey/chuto/prompt.html) で店舗を選び、指示文をコピー
2. 会社PCのローカル Claude アプリに貼り付け → Claude がブラウザで doda の検索結果（各検索の1ページ目のみ・5秒以上間隔。詳細ページは家電量販・PC関連と自社の求人に限り1店舗10件まで）を読み、CSV を出力
3. CSV を **UTF-8** で `doda_<店舗ID>_<YYYYMMDD>.csv` として保存し、中身を確認（推測で埋めた値が無いか）
4. GitHub でこのフォルダを開き「Add file → Upload files」（[直接開く](https://github.com/knoro-hue/HourlyWageSurvey/upload/main/data/doda)）→ ファイルをドラッグ →「Commit changes」
5. Actions の「中途採用 年収相場の集計」が自動で動き、ページが更新されます

## CSV の列（UTF-8・ヘッダ付き）
| 列 | 内容 |
|---|---|
| store_id | 店舗ID（config/stores.yaml の id。例 dospara-namba） |
| search_area | その求人が出た検索の勤務地（都道府県＋市区町村） |
| site | doda |
| company / job_title | 企業名・求人名 |
| employment | 正社員／契約社員／その他 |
| salary_min_man / salary_max_man | 年収の下限・上限（万円・数値）。モデル年収の例は入れない |
| monthly_min_yen | 年収の範囲が無く月給だけの場合の月給下限（円） |
| bonus | 有／無／空欄 |
| annual_holidays / overtime_avg_h | 年間休日（日）・月平均残業（時間）。数値のみ |
| transfer | 転勤：転勤なし／転居なし／選択可／転勤あり |
| housing | 社宅・住宅補助：社宅・寮｜住宅手当｜引越し補助 |
| requirements | 応募条件・必要資格：未経験可｜学歴不問｜高卒以上｜大卒以上｜運転免許｜販売・接客経験｜社会人経験｜管理職経験｜PCスキル |
| benefits | 福利厚生：賞与｜昇給｜交通費｜退職金｜家族手当｜資格支援｜社員割引｜持株会｜育児支援｜研修制度 |
| other_conditions | その他：完全週休2日｜土日休み｜車通勤可｜リモート可｜副業可｜上場企業 |
| work_location | 勤務地の住所または最寄駅だけ（宣伝文は入れない） |
| job_category | 検索に使った職種 |
| source_level | search（一覧だけ）／detail（詳細ページも確認） |
| url / checked_date | 求人のURL・確認日 YYYY-MM-DD |

- 待遇の列は**文章を転記せず選択肢から選ぶ**（複数は半角 `|` でつなぐ）。書かれていなければ空欄（推測で埋めない）。
- 選択肢の一覧は `wage_survey/chuto.py` の `TRANSFER_CHOICES`・`ATTR_VOCAB`。以前の形式（transfer_note・other_benefits・tags など文章の列）の CSV も、同じ規則で選択肢に振り分けて集計します。

## 集計のルール
- 同じ店舗の同じ URL は1件（確認日の新しい方を採用）。古いファイルは消さなくてよい
- 確認日から **90日** を過ぎた行・確認日が無い行は集計しない（毎週月曜に自動で再集計）
- 勤務地を座標にして店舗から **10km以内** を集計。勤務地が書かれていない・宣伝文だけの行は search_area（検索した市区町村）の中心で代用。それでも位置が分からない行は店舗別ページの「除外」に表示
- 直接競合の判定は時給調査と同じルール（config/settings.yaml の direct_competitor）
- 年収の **下限** で中央値・25/75% を計算。月給のみの求人は年収換算せず件数だけ表示
- 待遇は項目ごとに「記載あり」の割合を店舗一覧・店舗別ページで比較（記載が無い＝制度が無い、とは限らない）
- 設定は config/chuto.yaml（半径・期限・職種・国の賃金統計の比較値）

## 削除したいとき
GitHub でファイルを開き、ゴミ箱アイコン →「Commit changes」。再集計されます。
