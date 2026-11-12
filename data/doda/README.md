# 中途採用（doda）CSV の置き場

ここに置いた `*.csv` を集計して [中途採用 年収相場ページ](https://knoro-hue.github.io/HourlyWageSurvey/chuto/) を作ります（アップロード後、数分で反映）。

> **部内の判断材料のみ。外部へ提供しないでください。** doda の利用規約に「私的利用の範囲を超える利用・第三者提供の禁止」があります（適用範囲は要確認）。取得は人が画面の前にいる状態で、1店舗ずつ・少量・低頻度（目安：1店舗あたり月1回まで）で行ってください。サーバーや自動化ツールで doda に取りに行くことはしません。

## 手順
1. [指示文ジェネレータ](https://knoro-hue.github.io/HourlyWageSurvey/chuto/prompt.html) で店舗を選び、指示文をコピー
2. 会社PCのローカル Claude アプリに貼り付け → Claude がブラウザで doda の検索結果（各検索の1ページ目のみ・数秒間隔）を読み、CSV を出力
3. CSV を **UTF-8** で `doda_<店舗ID>_<YYYYMMDD>.csv` として保存し、中身を確認（推測で埋めた値が無いか）
4. GitHub でこのフォルダを開き「Add file → Upload files」（[直接開く](https://github.com/knoro-hue/HourlyWageSurvey/upload/main/data/doda)）→ ファイルをドラッグ →「Commit changes」
5. Actions の「中途採用 年収相場の集計」が自動で動き、ページが更新されます

## CSV の列（UTF-8・ヘッダ付き）
| 列 | 内容 |
|---|---|
| store_id | 店舗ID（config/stores.yaml の id。例 dospara-namba） |
| site | doda |
| company | 企業名 |
| job_title | 求人名・職種名 |
| employment | 正社員／契約社員 など |
| salary_min_man / salary_max_man | 年収の下限・上限（万円・数値） |
| monthly_min_yen | 年収表記が無く月給だけの場合の月給下限（円） |
| work_location | 勤務地の住所または最寄駅 |
| job_category | 検索に使った職種（販売・接客／PC・家電販売／店長候補／PCサポート・修理） |
| url | 求人のURL |
| checked_date | 確認日 YYYY-MM-DD |

記載のない値は空欄（推測で埋めない）。

## 集計のルール
- 同じ店舗の同じ URL は1件（確認日の新しい方を採用）。古いファイルは消さなくてよい
- 確認日から **90日** を過ぎた行・確認日が無い行は集計しない（毎週月曜に自動で再集計）
- 勤務地を座標にして店舗から **10km以内** を集計。位置が分からない行は店舗別ページの「除外」に表示
- 直接競合の判定は時給調査と同じルール（config/settings.yaml の direct_competitor）
- 年収の **下限** で中央値・25/75% を計算。月給のみの求人は年収換算せず件数だけ表示
- 設定は config/chuto.yaml（半径・期限・職種・国の賃金統計の比較値）

## 削除したいとき
GitHub でファイルを開き、ゴミ箱アイコン →「Commit changes」。再集計されます。
