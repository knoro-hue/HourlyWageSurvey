"""③直接競合の待遇調査（Claude のウェブ検索・ウェブ取得で Indeed・バイトル・公式採用ページ等を調べる）

マイナビバイトに載らない直接競合（家電量販・PCショップ・中古PC/スマホ）の求人を、
Claude API のサーバー側ツール（web_search / web_fetch）で調べ、時給と待遇を表にする。

1段目: 調査（ウェブ検索・取得を使って、根拠URL付きの調査メモを書く）
2段目: 抽出（調査メモを JSON スキーマに沿って構造化。ツールなし）

環境変数 ANTHROPIC_API_KEY が無い場合は何もしない。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

RESEARCH_SYSTEM = """あなたは小売業の採用担当向けに、競合店のアルバイト求人を調べるリサーチャーです。
ウェブ検索とウェブ取得を使い、事実だけを根拠URL付きで報告してください。

調べる対象:
- 指定店舗から半径{radius}km以内（できれば5km以内を優先）にある、家電量販店・パソコン専門店・PCパーツ店・中古PC/スマホ買取販売店の「店舗スタッフ（販売・接客・買取・修理受付など）」のアルバイト・パート求人
- 例: ヨドバシカメラ、ビックカメラ、ソフマップ、エディオン、ジョーシン、ケーズデンキ、ヤマダデンキ（LABI）、コジマ、ノジマ、ベスト電器、パソコン工房、ツクモ、じゃんぱら、イオシス、ゲオ（モバイル）、PC DEPOT、その他の地域のPCショップ
- 自社（ドスパラ／サードウェーブ）の求人は対象外

情報源:
- 各社の公式採用ページ、Indeed、バイトル、タウンワーク、マイナビバイト、求人ボックス、an など。どの求人サイトでもよい
- 同じ求人が複数あれば、更新日が新しい公式ページを優先し、採用した版（日付）を明記する
- 実在を確認できない店舗や求人は書かない。推測で埋めない

求人ごとに書くこと（求人に記載がない項目は「記載なし」。記載なしは制度が無いという意味ではない）:
企業・店舗名／所在地または最寄駅／時給（開始時給・上限、条件付きの時給はその条件）／交通費／昇給／社員登用／社員割引／社会保険／研修（期間・研修時給）／加算・手当（深夜・土日祝・資格・語学など）／その他の特徴／情報源サイト名と URL／掲載日・更新日（分かれば）
"""

RESEARCH_USER = """店舗: {name}
所在地: {address}
自店の募集時給: {own}

この店舗の直接競合となる近隣店舗のアルバイト求人を調べ、上の項目で一覧にしてください。
すでに把握している求人（重複して調べなくてよい）:
{known}
最大{max_items}件まで。最後に、調べたが見つからなかった主な競合があれば一言添えてください。"""

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "postings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "store": {"type": "string"},
                    "address_or_station": {"type": "string"},
                    "hourly_min": {"type": ["integer", "null"]},
                    "hourly_max": {"type": ["integer", "null"]},
                    "wage_text": {"type": "string"},
                    "transport": {"type": "string"},
                    "raise": {"type": "string"},
                    "promotion": {"type": "string"},
                    "discount": {"type": "string"},
                    "social_insurance": {"type": "string"},
                    "training": {"type": "string"},
                    "allowances": {"type": "string"},
                    "features": {"type": "string"},
                    "source_site": {"type": "string"},
                    "source_url": {"type": "string"},
                    "source_date": {"type": "string"},
                },
                "required": ["company", "store", "address_or_station", "hourly_min", "hourly_max", "wage_text", "transport",
                             "raise", "promotion", "discount", "social_insurance", "training", "allowances", "features",
                             "source_site", "source_url", "source_date"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["postings", "notes"],
    "additionalProperties": False,
}


def _client():
    import anthropic

    return anthropic.Anthropic()


def _text(msg) -> str:
    return "\n".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def research_store(store: dict, own_wage: float | None, known: list[str], cfg: dict) -> dict | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.info("ANTHROPIC_API_KEY が無いため Web 調査をスキップ")
        return None
    import anthropic

    client = _client()
    model = cfg.get("model", "claude-opus-5-5")
    radius = store.get("direct_radius_km", 10)
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": int(cfg.get("max_searches", 15)),
         "user_location": {"type": "approximate", "country": "JP", "region": store.get("prefecture_label", ""),
                           "timezone": "Asia/Tokyo"}},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": int(cfg.get("max_fetches", 15))},
    ]
    messages = [{"role": "user", "content": RESEARCH_USER.format(
        name=store["name"], address=store.get("address", ""),
        own=f"{int(own_wage)}円" if own_wage else "不明",
        known="\n".join(f"- {k}" for k in known[:30]) or "なし",
        max_items=int(cfg.get("max_items", 15)),
    )}]
    system = RESEARCH_SYSTEM.format(radius=radius)
    usage = {"input_tokens": 0, "output_tokens": 0, "searches": 0}
    try:
        msg = None
        for _ in range(int(cfg.get("max_continuations", 6))):
            with client.beta.messages.stream(
                model=model, max_tokens=32000, system=system, tools=tools, messages=messages,
                output_config={"effort": cfg.get("effort", "medium")},
                betas=[FALLBACK_BETA], fallbacks="default",
            ) as stream:
                msg = stream.get_final_message()
            usage["input_tokens"] += msg.usage.input_tokens or 0
            usage["output_tokens"] += msg.usage.output_tokens or 0
            stu = getattr(msg.usage, "server_tool_use", None)
            usage["searches"] += getattr(stu, "web_search_requests", 0) or 0
            if msg.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": msg.content})
                continue
            break
        if msg is None or msg.stop_reason == "refusal":
            log.warning("%s: Web 調査が完了しませんでした（%s）", store["id"], msg.stop_reason if msg else "no response")
            return None
        memo = _text(msg)

        ext = client.beta.messages.create(
            model=model, max_tokens=16000,
            system="調査メモに書かれている求人だけを、指定のJSON形式に写してください。メモに無い情報は補わず、記載が無い項目は「記載なし」、時給が読み取れなければ null にします。",
            messages=[{"role": "user", "content": memo}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": EXTRACT_SCHEMA}},
            betas=[FALLBACK_BETA], fallbacks="default",
        )
        usage["input_tokens"] += ext.usage.input_tokens or 0
        usage["output_tokens"] += ext.usage.output_tokens or 0
        if ext.stop_reason in ("refusal", "max_tokens"):
            log.warning("%s: 構造化に失敗（%s）", store["id"], ext.stop_reason)
            return None
        data = json.loads(_text(ext))
    except anthropic.RateLimitError:
        log.warning("%s: レート制限のため Web 調査をスキップ", store["id"])
        return None
    except anthropic.APIStatusError as e:
        log.warning("%s: Web 調査で API エラー %s: %s", store["id"], e.status_code, e.message)
        return None
    except anthropic.APIConnectionError:
        log.warning("%s: Web 調査で接続エラー", store["id"])
        return None
    except (json.JSONDecodeError, ValueError) as e:
        log.warning("%s: 調査結果の解析に失敗: %s", store["id"], e)
        return None
    data["memo"] = memo
    data["model"] = model
    data["usage"] = usage
    data["researched_at"] = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec="minutes")
    log.info("%s: Web 調査 %d 件（入力 %d / 出力 %d トークン、検索 %d 回）", store["id"], len(data.get("postings", [])),
             usage["input_tokens"], usage["output_tokens"], usage["searches"])
    return data
