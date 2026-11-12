# HourlyWageSurvey — 採用向け 時給調査の自動実行

**ドスパラ全店（42店舗）** について、求人サイトから周辺のアルバイト時給を集めて **自動でレポート化** します（GitHub Actions）。
毎週日曜の未明（2:17開始）に全店を調査し、日曜の昼頃までにダッシュボードが更新されます（月曜朝に確認できます）。

## 📊 ダッシュボード（ここを見る）

### **https://knoro-hue.github.io/HourlyWageSurvey/**

なんば店に直接：https://knoro-hue.github.io/HourlyWageSurvey/dospara-namba/

メイン画面に全店の一覧（自店時給・周辺相場・差額）が並び、店舗名を押すと各店のレポートが開きます。各店レポートでは、自店・直接競合・間接競合の時給、時給の分布、職種別の相場、全求人の検索、企業別の求人数、時給の推移が見られます。Excel・CSV もダッシュボード上部のボタンからダウンロードできます。

> 初回のみ設定が必要です：リポジトリの **Settings → Pages → Build and deployment** で
> Source を **Deploy from a branch**、Branch を **main**（フォルダは `/ (root)` でも `/docs` でも可）にして Save。数分後に上のURLで表示されます。

| 見たいもの | 場所 |
|---|---|
| ダッシュボード（グラフ・検索） | https://knoro-hue.github.io/HourlyWageSurvey/ |
| テキスト版の最新レポート | [`reports/dospara-namba/latest.md`](reports/dospara-namba/latest.md) |
| Excel（①〜⑥シート）・CSV | `reports/dospara-namba/<日付>/` |
| 時系列データ | [`reports/dospara-namba/history.csv`](reports/dospara-namba/history.csv) |
| 実行状況・手動実行 | [Actions タブ](https://github.com/knoro-hue/HourlyWageSurvey/actions) |

## 出力（①〜⑥）

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

## ①③待遇比較（無料）

各店レポートの「① 自店と ③ 直接競合の待遇比較」とExcel「①③待遇比較」に、交通費・昇給・社員登用・社員割引・社会保険・研修・加算を並べます。
自店は自社採用サイトの本文、競合はマイナビバイトの交通費欄と「こだわり条件」から取るため、追加費用・追加アクセスはありません。
あわせて「待遇を明記している求人の割合」（直接競合・間接競合）も表示します。

（参考）Claude のウェブ検索で Indeed・バイトル等も調べる機能が入っていますが、有料のため `config/settings.yaml` の `competitor_research.enabled: false` で停止しています。

## Indeed・バイトル・タウンワーク等

扱いません（社内判断）。②③はマイナビバイトのみです。

自店時給を社内値で入れる場合は `config/stores.yaml` の `own_hourly_wage_manual` を使います。

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

## 中途採用（正社員・契約社員）の年収相場

アルバイト時給調査とは別ページです：<https://knoro-hue.github.io/HourlyWageSurvey/chuto/>

- **データの取り方**：doda はサーバーからの自動アクセスを受け付けないため、担当者が会社PCのローカル Claude アプリに[指示文](https://knoro-hue.github.io/HourlyWageSurvey/chuto/prompt.html)を貼り、検索結果画面の文字を CSV にします（各検索の1ページ目のみ・数秒間隔・少量低頻度）。サーバーから doda に取りに行く処理はありません。
- **取り込み**：CSV を GitHub の画面から `data/doda/` にアップロード → Actions「中途採用 年収相場の集計」（`python -m wage_survey.chuto`）が自動で `docs/chuto/` を更新。手順と列の説明は [data/doda/README.md](data/doda/README.md)。
- **集計**：URL 重複除去・確認日から90日超を除外・店舗から10km以内・直接競合は時給調査と同じ判定・年収下限の中央値（月給のみは件数だけ）。設定は `config/chuto.yaml`。
- **注意**：部内の判断材料のみ。外部へ提供しないでください（doda 利用規約の「私的利用の範囲を超える利用・第三者提供の禁止」。適用範囲は要確認）。
- 比較基準の国の賃金統計（賃金構造基本統計調査・職種別）は `config/chuto.yaml` に出典で確認した値だけを入れます（未入力は「要確認」と表示）。

## 店舗とエリアの自動設定

- **店舗一覧**：ドスパラ公式サイトの店舗一覧（https://www.dospara.co.jp/shop/inc-shop-list.html）から毎月自動更新（`config/stores.yaml`）。店名が「ドスパラ」で始まる店舗が対象で、併設のデジタルドックは除外。店舗は7グループ（`shard`）に分けて順番に調査。
- **エリアマスタ**：マイナビバイトの市区町村・区の名称と代表座標（`data/mynavi_areas.json`）。店舗から半径内に掛かるエリアを自動で選びます。
- **①自店**：自社採用サイトの一覧から、店名（例「大阪・なんば店」）を含む求人を自動で拾います。掲載がない店舗は「求人掲載なし」と表示。
- 手動実行：Actions →「時給調査（定期実行）」→ Run workflow で `task` を選択（`survey`＝調査、`sync-stores`＝店舗一覧更新、`build-areas`＝エリアマスタ更新、`setup-all`＝全部まとめて実行し全店を調査）。

## （参考）手動で店舗を追加する場合

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
data/geocode_cache.json   ジオコーディングキャッシュ
wage_survey/              本体（mynavi.py 取得・解析 / records.py 正規化・分類 / report.py 出力）
reports/                  出力（自動コミット）
```

## 注意

- 求人サイトの利用規約は変更されることがあります。運用前に各サイトの規約を確認し、アクセス間隔・リクエスト上限を緩めないでください。
- 掲載求人ベースの調査のため、非公開求人や他媒体のみの求人は含まれません。
