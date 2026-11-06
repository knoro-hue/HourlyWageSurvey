# HourlyWageSurvey — 採用向け 時給調査の自動実行

店舗ごとに、求人サイトから周辺のアルバイト時給を集めて **毎週自動でレポート化** します（GitHub Actions）。
初版の対象は **ドスパラ大阪・なんば店**（大阪府大阪市浪速区日本橋3丁目6-22）。

## 出力（①〜⑥）

最新版：[`reports/dospara-namba/latest.md`](reports/dospara-namba/latest.md)

| 要件 | 出力先 |
|---|---|
| ① 店舗（自店）の時給 | 自社採用サイト（tw-saiyou.snar.jp）から自動取得。Excel「①自店」 |
| ② 周辺 5km の時給（間接競合） | Excel「②間接競合」／ 職種分類別の集計 |
| ③ 家電量販・PC関連小売の時給（直接競合） | Excel「③直接競合」（既定は半径10km） |
| ④ ①②③の一覧と店舗からの距離（km・小数第2位） | Excel「④一覧(距離順)」／ CSV |
| ⑤ ①②③の業務範囲 | Excel「⑤業務範囲」（求人の仕事内容全文・シフト） |
| ⑥ ①②③の求人数・求人サイト | Excel「⑥求人数_企業別」「⑥求人数_エリア職種別」（正確な掲載件数） |

`reports/<店舗ID>/<日付>/` に Excel・CSV・summary.md が保存され、`reports/<店舗ID>/history.csv` に時系列（自店・直接競合中央値・間接競合中央値 等）が追記されます。

## 仕組み

- **データ源**：マイナビバイト（一覧ページに給与・仕事内容・所在地座標が含まれるため、詳細ページへはアクセスしない）。
- **アクセス方針**：正直な User-Agent、robots.txt 遵守、同一サイトへは4秒間隔、1店舗あたり最大450リクエスト。
  - robots.txt で `?pageNo=`（2ページ目以降）が禁止されているため、各条件の **1ページ目（最大30件）だけ** を取得し、代わりに「区 × 職種大分類 × 中分類」に分割して網羅性を上げています。各条件の **掲載件数は正確** に記録されます（⑥）。
- **距離**：求人の掲載座標から店舗までの直線距離（ハーバーサイン式）。派遣・紹介会社の求人は本社座標が入っていることがあるため、「勤務地：〜」の表記 → 最寄駅 → 区の順で位置を推定し、根拠を「位置の根拠」列に残します。ジオコーディングは国土地理院 住所検索API（結果は `data/geocode_cache.json` にキャッシュ）。
- **分類**：
  - ①自店：`own_keywords`（ドスパラ等）に一致し、店舗から `own_match_radius_km` 以内
  - ③直接競合：職種分類「家電量販店」、または競合ブランド名（ヨドバシ、ビックカメラ、ジョーシン、ソフマップ、パソコン工房、ツクモ等）・職種名（PC販売・家電販売等）に一致
  - ②間接競合：上記以外で半径5km以内の全職種
  - 判定ルールは `config/settings.yaml` の `direct_competitor` で編集できます。
- **時給集計**：各求人の下限額。日給・月給は勤務時間が不明なため集計から除外（一覧には掲載）。

## 自動で取れない媒体の扱い（Indeed・バイトル・タウンワーク等）

Indeed・タウンワーク（Indeed統合）・バイトル等は利用規約やアクセス制限によって自動取得の対象外です。必要な求人は
[`data/manual_jobs.csv`](data/manual_jobs.csv) に1行ずつ追記すると、次回の実行から距離計算・集計に含まれます。

```csv
store_id,classification,site,company,shop,occupation,employment,wage_type,wage_min,wage_max,address,work_content,url,checked_date
dospara-namba,own,Indeed,株式会社サードウェーブ,ドスパラ大阪・なんば店,販売スタッフ,アルバイト・パート,時給,1250,,大阪府大阪市浪速区日本橋3丁目6-22,PC・パーツの販売・接客,https://...,2026-11-06
```

`classification` は `own` / `direct` / `indirect`。自店時給を社内値で入れる場合は `config/stores.yaml` の `own_hourly_wage_manual` でも可。

## 実行

- **定期実行**：毎週月曜 06:17（JST）に `.github/workflows/wage-survey.yml` が走り、結果を main にコミットします。
- **手動実行**：GitHub の Actions → 「時給調査（定期実行）」→ Run workflow（店舗ID・最大リクエスト数を指定可）。
- **ローカル**：

```bash
pip install -r requirements.txt
```

```bash
python -m wage_survey.main --store dospara-namba
```

## 全店展開の手順

1. `config/stores.yaml` に店舗を追加（`id`・`name`・`address`・`own_keywords`・半径）。`lat`/`lng` を省略すると住所から自動取得します。
2. `mynavi.indirect_areas` に半径に掛かるエリアのパスを列挙します。
   - 政令市の区：`<都道府県>/city-<市ID>/town-<区ID>`（例：大阪市浪速区＝`osaka/city-262/town-144`）
   - それ以外の市：`<都道府県>/city-<市ID>`
   - ID はマイナビバイトで該当エリアを開いたときの URL で確認できます。
3. `direct_areas`（広域）と `direct_fallback_areas`（30件超の場合の分割先）を設定。
4. 1店舗あたり約30分かかるため、店舗数が増えたら `--store` ごとにジョブを分ける（matrix 化）か、実行頻度を分散してください。

## 構成

```
config/stores.yaml        店舗設定
config/settings.yaml      共通設定（アクセス間隔・直接競合の判定ルール）
data/manual_jobs.csv      手入力求人（Indeed等）
data/geocode_cache.json   ジオコーディングキャッシュ
wage_survey/              本体（mynavi.py 取得・解析 / records.py 正規化・分類 / report.py 出力）
reports/                  出力（自動コミット）
```

## 注意

- 求人サイトの利用規約は変更されることがあります。運用前に各サイトの規約を確認し、アクセス間隔・リクエスト上限を緩めないでください。
- 掲載求人ベースの調査のため、非公開求人や他媒体のみの求人は含まれません。
