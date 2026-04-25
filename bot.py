import os
import json
import re
import base64
import requests
import feedparser
import anthropic
import tweepy
import html
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from pathlib import Path
import urllib.parse

# ===== CONFIG =====
ASSOCIATE_ID = os.environ.get("ASSOCIATE_ID", "gadgetradarjp-22")
MOSHIMO_A_ID = "5492887"  # もしもアフィリエイト 楽天
STATE_FILE = "state.json"
LESSONS_FILE = "lessons.json"
MAX_LESSONS = 20

RSS_FEEDS = [
    {"name": "RoomClip マガジン",   "url": "https://magazine.roomclip.jp/feed"},
    {"name": "LIMIA",               "url": "https://limia.jp/feed/"},
    {"name": "ROOMIE",              "url": "https://www.roomie.jp/feed/"},
    {"name": "ESSE online",         "url": "https://esse-online.jp/feed/"},
    {"name": "kufura",              "url": "https://kufura.jp/feed/"},
    {"name": "暮らしニスタ",         "url": "https://kurashinista.jp/feed"},
    {"name": "北欧、暮らしの道具店", "url": "https://hokuohkurashi.com/note/feed"},
    {"name": "Suumo ジャーナル",    "url": "https://suumo.jp/journal/feed/"},
]

KURASHI_KEYWORDS = [
    # インテリア
    "インテリア", "家具", "ソファ", "テーブル", "椅子", "棚", "本棚",
    "照明", "ライト", "カーテン", "ラグ", "クッション", "壁紙",
    # 収納・整理
    "収納", "整理", "片付け", "断捨離", "整頓", "収納グッズ", "仕切り",
    "ボックス", "かご", "引き出し", "クローゼット",
    # キッチン・家事
    "キッチン", "食洗機", "電気ケトル", "ホットクック", "電子レンジ",
    "掃除", "洗濯", "家事", "ロボット掃除機", "ルンバ", "ブラーバ",
    # 暮らし全般
    "暮らし", "生活", "丁寧な暮らし", "シンプルライフ", "ミニマリスト",
    "観葉植物", "グリーン", "DIY", "セルフリノベ",
    # 新居・住まい
    "新築", "注文住宅", "マイホーム", "間取り", "新居",
    # ブランド
    "無印良品", "IKEA", "イケア", "ニトリ", "カインズ",
    # スマートホーム
    "スマートホーム", "IoT", "スマートスピーカー", "Alexa", "Google Home",
    # 防災
    "防災", "非常用", "備蓄",
]

# 優先キーワード（フィード上位に表示）
PRIORITY_KEYWORDS = [
    "インテリア", "収納", "整理", "マイホーム", "新築", "注文住宅",
    "無印良品", "IKEA", "ニトリ", "ロボット掃除機",
]

# 除外キーワード（暮らし系以外のジャンルをスキップ）
EXCLUDE_KEYWORDS = [
    "スマホ", "iPhone", "Android", "PC", "ノートPC", "パソコン", "タブレット",
    "ゲーム", "PlayStation", "Nintendo", "Xbox",
    "自動車", "クルマ", "バイク", "EV",
    "株", "投資", "FX", "仮想通貨", "暗号資産",
    "アニメ", "マンガ", "映画", "ドラマ",
    "転職", "就活", "資格",
    "カメラ", "レンズ", "ミラーレス",
    "イヤホン", "ヘッドホン", "AirPods",
    "GPU", "CPU", "SSD", "メモリ",
]

# ===== LESSONS =====
def load_recent_lessons(n: int = 3) -> list:
    """lessons.jsonから直近n件の教訓を返す"""
    try:
        if Path(LESSONS_FILE).exists():
            with open(LESSONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                lessons = [item["lesson"] for item in data.get("lessons", [])]
                return lessons[-n:]
    except Exception as e:
        print(f"lessons.json読み込みエラー: {e}")
    return []


def save_lessons(new_lessons: list) -> None:
    """新しい教訓をlessons.jsonに追記する（最大MAX_LESSONS件保持）"""
    try:
        data = {"lessons": []}
        if Path(LESSONS_FILE).exists():
            with open(LESSONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for lesson in new_lessons:
            data["lessons"].append({"date": today, "lesson": lesson})
        if len(data["lessons"]) > MAX_LESSONS:
            data["lessons"] = data["lessons"][-MAX_LESSONS:]
        with open(LESSONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"lessons.json保存エラー: {e}")


# ===== STATE =====
MAX_STATE_URLS = 200   # posted_urls の上限（古い順に削除）
SKIP_TTL_DAYS = 3      # skip_urls の有効期限（日）。キーワード変更後も自動再チェック
def load_state():
    if Path(STATE_FILE).exists():
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)

            posted = state.get("posted_urls", [])

            # skip_urls: 旧フォーマット（リスト）→ 新フォーマット（{url: date}）に自動移行
            raw_skip = state.get("skip_urls", {})
            if isinstance(raw_skip, list):
                raw_skip = {}  # 旧フォーマットはリセット（TTL不明のため）

            today = datetime.now(timezone.utc).date()
            cutoff = today - timedelta(days=SKIP_TTL_DAYS)
            valid_skip = {
                url: date_str
                for url, date_str in raw_skip.items()
                if datetime.strptime(date_str, "%Y-%m-%d").date() >= cutoff
            }
            expired = len(raw_skip) - len(valid_skip)
            if expired > 0:
                print(f"skip_urls: {expired}件の期限切れエントリを削除（{SKIP_TTL_DAYS}日TTL）")

            return posted, valid_skip
        except (json.JSONDecodeError, OSError) as e:
            print(f"state.json読み込みエラー（リセットします）: {e}")
    return [], {}

def save_state(posted_urls, skip_urls_dict):
    # posted_urls は上限超えたら古い順に削除
    trimmed = list(posted_urls)
    if len(trimmed) > MAX_STATE_URLS:
        trimmed = trimmed[-MAX_STATE_URLS:]
        print(f"state.json: posted_urls を {MAX_STATE_URLS} 件に整理")
    with open(STATE_FILE, "w") as f:
        json.dump({
            "posted_urls": trimmed,
            "skip_urls": skip_urls_dict,
        }, f, ensure_ascii=False, indent=2)

# レビュー・新発売キーワード（伸びやすい記事タイプ）
REVIEW_OR_NEW_KEYWORDS = [
    "レビュー", "評価", "実機", "ハンズオン", "使ってみた", "試した",
    "比較", "vs", "VS", "対決", "違い", "どっち",
    "新発売", "発売開始", "発表", "登場", "新モデル", "新型", "新製品",
    "スペック", "仕様", "詳細", "まとめ",
]

# ===== FETCH RSS =====
def _is_excluded(title: str) -> bool:
    return any(kw in title for kw in EXCLUDE_KEYWORDS)

def _is_kurashi_related(title: str) -> bool:
    return any(kw in title for kw in KURASHI_KEYWORDS)

def _is_priority(title: str) -> bool:
    return any(kw in title for kw in PRIORITY_KEYWORDS)

def _is_review_or_new(title: str) -> bool:
    return any(kw in title for kw in REVIEW_OR_NEW_KEYWORDS)


def _is_gadget_by_claude(title: str, source: str) -> bool:
    """Claude Haiku で記事が消費者向けガジェット記事か判定する。
    API失敗時は True（フォールスルー）を返す。"""
    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        prompt = (
            f"次の記事は消費者向けの物理的なハードウェア製品（ガジェット・家電・PC・スマホ・カメラ等）に関する記事ですか？\n"
            f"ソース: {source}\n"
            f"タイトル: {title}\n"
            f"注意: 以下のいずれかに該当する記事は「no」と答えてください。\n"
            f"- ソフトウェア・アプリ・ドライバ・OSアップデート・Webサービスの記事\n"
            f"- 中古・リユース・ジャンク品・フリマ・二次流通に関する記事\n"
            f"新品の物理的なハードウェア製品についての記事のみ「yes」と答えてください。\n"
            f"「yes」か「no」だけ答えてください。"
        )
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = response.content[0].text.strip().lower()
        return "yes" in answer
    except Exception:
        return True


def fetch_new_articles(posted_urls_set, skip_urls_dict):
    articles = []
    stats = {"total": 0, "already_posted": 0, "skip_cached": 0,
             "excluded": 0, "not_gadget_kw": 0, "rejected_by_claude": 0, "passed": 0}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_skips = {}  # {url: date_str}

    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            feed_entries = feed.entries[:10]
            print(f"[{feed_info['name']}] {len(feed_entries)}件取得")
            for entry in feed_entries:
                url = entry.get("link", "")
                title = entry.get("title", "")
                if not url:
                    continue
                stats["total"] += 1
                short = title[:55]

                if url in posted_urls_set:
                    stats["already_posted"] += 1
                    print(f"  [投稿済] {short}")
                    continue
                if url in skip_urls_dict:
                    stats["skip_cached"] += 1
                    print(f"  [スキップ済] {short}")
                    continue
                if _is_excluded(title):
                    stats["excluded"] += 1
                    print(f"  [除外KW] {short}")
                    new_skips[url] = today_str
                    continue
                if not _is_kurashi_related(title):
                    stats["not_gadget_kw"] += 1
                    print(f"  [暮らし外] {short}")
                    new_skips[url] = today_str
                    continue
                if not _is_gadget_by_claude(title, feed_info["name"]):
                    stats["rejected_by_claude"] += 1
                    print(f"  [Claude除外] {short}")
                    new_skips[url] = today_str
                    continue

                stats["passed"] += 1
                is_prio = _is_priority(title)
                is_rev = _is_review_or_new(title)
                tier = "★priority" if is_prio else "📄normal"
                rev_label = "📝" if is_rev else ""
                print(f"  [通過✓] [{tier}]{rev_label} {short}")
                articles.append({
                    "url": url,
                    "title": title,
                    "summary": entry.get("summary", "")[:500],
                    "source": feed_info["name"],
                    "priority": is_prio,
                    "review_or_new": is_rev,
                })
        except Exception as e:
            print(f"RSS fetch error ({feed_info['name']}): {e}")

    print(
        f"\n--- フィルター結果 ---\n"
        f"  合計チェック: {stats['total']}件\n"
        f"  投稿済みスキップ: {stats['already_posted']}件\n"
        f"  キャッシュスキップ: {stats['skip_cached']}件\n"
        f"  除外KW: {stats['excluded']}件\n"
        f"  暮らし外: {stats['not_gadget_kw']}件\n"
        f"  Claude除外: {stats['rejected_by_claude']}件\n"
        f"  通過: {stats['passed']}件"
    )

    # ソート: 0: priority+review  1: priority  2: review  3: normal
    def _sort_key(a):
        rev = a.get("review_or_new", False)
        if a["priority"]:
            return 0 if rev else 1
        return 2 if rev else 3
    articles.sort(key=_sort_key)
    prio = sum(1 for a in articles if a["priority"])
    rev  = sum(1 for a in articles if a.get("review_or_new"))
    print(f"新着記事: {len(articles)}件（priority: {prio}件 / レビュー・新発売: {rev}件）")
    return articles, new_skips

# ===== AMAZON URL =====
def extract_asin(article) -> str | None:
    """RSS概要・記事URLからASINを抽出して返す。見つからない場合はNone。"""
    for text in [article.get("summary", ""), article.get("url", "")]:
        m = re.search(r'amazon\.co\.jp(?:/[^/]+)?/dp/([A-Z0-9]{10})', text)
        if m:
            return m.group(1)
    return None

def extract_amazon_url(article, fallback_keyword: str = ""):
    # RSS概要・記事URLからASINを探す（記事ページはクロールしない）
    # ※ページクロールは広告・関連商品の無関係なASINを拾うため廃止
    asin = extract_asin(article)
    if asin:
        return f"https://www.amazon.co.jp/dp/{asin}?tag={ASSOCIATE_ID}"
    keyword = fallback_keyword or article["title"]
    encoded = urllib.parse.quote(keyword)
    return f"https://www.amazon.co.jp/s?k={encoded}&tag={ASSOCIATE_ID}"

# ===== ARTICLE GENERATION =====

# キーワード → セクション別追加ヒントのマッピング
KEYWORD_HINTS = [
    {
        "keywords": ["レビュー", "比較", "違い", "どっち", "vs", "VS", "対決", "評価"],
        "spec_hint": "本製品と前モデル（または主な競合製品）をHTMLのtableタグで比較表にすること。項目：サイズ・重量・主要スペック・価格",
        "recommend_hint": "おすすめしない人（こんな人は見送って）も合わせて記述する",
        "summary_hint": "どんな人に買う価値があるかを明示して締めくくる",
    },
    {
        "keywords": ["おすすめ", "ランキング", "選び方", "人気"],
        "spec_hint": "3〜5製品をHTMLのtableタグでランキング比較表にすること。項目：製品名・価格・特徴・おすすめ度",
        "recommend_hint": "予算別・用途別（在宅勤務・ゲーミング・学生など）で最適な選択肢を提示する",
        "summary_hint": "どの製品を選ぶべきかの最終アドバイスを含めて締めくくる",
    },
    {
        "keywords": ["新発売", "発売開始", "発表", "登場", "新モデル", "新型"],
        "spec_hint": "スペックを文章で説明しつつ、前モデルからの変更点を必ず明記する",
        "recommend_hint": "前モデルからの買い替えを検討している人向けのアドバイスを含める",
        "summary_hint": "予約・購入タイミングのアドバイスを含めて締めくくる",
    },
    {
        "keywords": ["セール", "割引", "タイムセール", "クーポン", "特価", "安い"],
        "spec_hint": "スペックを文章で説明する",
        "recommend_hint": "「今買うべき理由」をセールの観点から強調し、購入を迷っている読者の背中を押す",
        "summary_hint": "セール期間・在庫への注意喚起を含め、購入を促す文章で締めくくる",
    },
    {
        "keywords": ["使い方", "設定", "方法", "手順", "やり方", "初心者"],
        "spec_hint": "スペックより実際の操作手順・設定方法をステップ形式（①②③）で説明する",
        "recommend_hint": "初心者・初めて使う人向けのポイントを特に丁寧に説明する",
        "summary_hint": "つまずきやすいポイントの注意点を含めて締めくくる",
    },
    {
        "keywords": ["コスパ", "安くて", "予算", "円以下", "円台", "格安"],
        "spec_hint": "スペックと価格の比率（コスパ）を中心に、同価格帯の競合と比較して説明する",
        "recommend_hint": "予算を重視する読者向けに、節約しながら満足度を高める使い方を提案する",
        "summary_hint": "コスパの総評と「この価格帯でこれを選ぶ理由」を明示して締めくくる",
    },
]

DEFAULT_HINTS = {
    "spec_hint": "スペックを文章で説明。数値の意味・他製品との違いを補足する。競合製品と比較しこの製品を選ぶ理由を明示する",
    "recommend_hint": "在宅勤務・ゲーミング・動画編集・学生など具体的な使用シーンを3パターン以上挙げて説明する",
    "summary_hint": "購入前に確認すべきポイントを含めて締めくくる",
}


def _get_hints(title: str) -> dict:
    """タイトルのキーワードからセクション別ヒントを取得"""
    for rule in KEYWORD_HINTS:
        if any(kw in title for kw in rule["keywords"]):
            tag = rule["keywords"][0]
            print(f"記事ヒント適用: {tag}")
            return rule
    return DEFAULT_HINTS


# WPカテゴリーIDキャッシュ {slug: wp_category_id}
# カテゴリーIDキャッシュ {slug: wp_category_id}
_wp_category_cache: dict = {}


def analyze_image_with_claude(image_data: bytes, title: str) -> str:
    """OGP画像をClaudeビジョンで分析し、製品の外観情報を返す。失敗時は空文字。"""
    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        b64 = base64.standard_b64encode(image_data).decode("utf-8")
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": (
                        f"この製品画像（{title}）から読み取れる情報を日本語の箇条書きで答えてください。\n"
                        "対象: カラーバリエーション・フォームファクター・ポート類・外観上の特徴。\n"
                        "推測・憶測は書かない。画像から確認できる事実のみ。"
                    )},
                ],
            }],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"画像分析エラー: {e}")
    return ""


def _extract_approved_numbers(summary: str, perplexity_info: str) -> str:
    """元記事概要とPerplexity情報から数字情報を抽出して承認済みリストを返す"""
    combined = summary + "\n" + perplexity_info
    found = []
    # 価格: ¥12,800 / 12,800円
    found += re.findall(r'[¥￥][\d,]+(?:\.\d+)?|[\d,]+(?:\.\d+)?円', combined)
    # 日付: 2024年4月19日 / 4月19日
    found += re.findall(r'\d{4}年\d{1,2}月(?:\d{1,2}日)?|\d{1,2}月\d{1,2}日', combined)
    # スペック数値: 256GB / 6.1インチ / 4000mAh / 3.7GHz
    found += re.findall(r'[\d.]+\s*(?:GB|TB|MB|GHz|MHz|mAh|インチ|inch|mm|cm|kg|g(?!en)|W(?!\b)|Hz)', combined, re.IGNORECASE)
    # シリーズ世代: 第3世代 / Gen 4
    found += re.findall(r'第\d+世代|Gen\s*\d+', combined, re.IGNORECASE)
    unique = list(dict.fromkeys(found))  # 順序を保ちつつ重複除去
    return "・".join(unique) if unique else ""


def _build_prompt(article: dict, hints: dict, perplexity_info: str = "", lessons: list = None,
                  image_analysis: str = "") -> str:
    if lessons is None:
        lessons = []
    title = article['title']
    url = article['url']
    summary = article['summary']

    perplexity_section = ""
    if perplexity_info:
        perplexity_section = f"\n## 記事内容・スペック情報（Perplexity調査結果）\n{perplexity_info}\n"

    image_section = ""
    if image_analysis:
        image_section = f"\n## 製品画像から読み取れた情報\n{image_analysis}\n"

    approved_numbers = _extract_approved_numbers(summary, perplexity_info)
    if approved_numbers:
        numbers_rule = f"\n【承認済み数字リスト】記事本文で使用できる具体的な数字はこれだけです: {approved_numbers}\nこのリストにない価格・日付・スペック数値・世代番号を本文中に一切記載してはならない。不明な場合は「公式サイトをご確認ください」と書くこと。"
    else:
        numbers_rule = "\n【数字使用禁止】元記事・追加情報に数値情報が確認できません。価格・発売日・スペック数値・世代番号などの具体的な数字を本文中に一切記載してはならない。「公式サイトをご確認ください」と書くこと。"

    lessons_section = ""
    if lessons:
        lesson_lines = "\n".join(f"- {l}" for l in lessons)
        lessons_section = f"\n【過去の改善指示（必ず守ること）】\n{lesson_lines}"

    return f"""以下の暮らし・インテリア記事をもとに、Googleにインデックスされやすいオリジナルブログ記事を日本語で作成してください。

記事タイトル: {title}
記事URL: {url}
記事概要: {summary}
{image_section}{perplexity_section}
以下のJSON形式のみで出力してください：
{{"title": "検索意図を満たすSEOタイトル（30〜40字・数字や「収納」「インテリア」「アイデア」などを含める）", "product_name": "記事のメイン商品・テーマ名（楽天で検索して見つかる形式）", "meta_description": "検索結果に表示されるメタディスクリプション（100〜120字・記事の魅力と読む価値を伝える文章）", "category_slug": "カテゴリーslug（英小文字・ハイフン区切り）", "category_name": "カテゴリーの日本語表示名", "content": "HTMLの本文（下記の構成・ルールに従う）"}}

カテゴリー例（合致するものがあれば優先して使う）: interior（インテリア） / storage（収納・整理） / kitchen（キッチン・家事） / kurashi（暮らし全般） / smarthome（スマートホーム） / diy（DIY・リノベ） / myhome（マイホーム・住まい）
合致しないジャンルの場合は新しいslugと日本語名を自由に設定してよい

【本文の構成】
<div class="point-box">
<p><strong>この記事のポイント</strong></p>
<ul>
<li>（この製品の最大の特徴を30字以内の体言止めで）</li>
<li>（価格・コスパの結論を30字以内の体言止めで）</li>
<li>（どんな人におすすめかを30字以内の体言止めで）</li>
</ul>
</div>

<h2>この製品が注目される理由</h2>
<p>製品の背景・登場した経緯・どんな人に向いているかを200字以上で独自の視点を交えて説明</p>

<h2>主な特徴・スペック</h2>
<p>{hints["spec_hint"]}</p>

<h2>価格・発売情報</h2>
<p>【厳守】承認済み数字リストの価格・発売日のみ記載する。リストにない場合は「価格・発売日は未発表です。公式サイトをご確認ください」とだけ書く。</p>

<h2>こんな人におすすめ</h2>
<p>{hints["recommend_hint"]}</p>

<h2>よくある質問</h2>
<p>この製品について読者が疑問に思いそうな質問を2〜3個、Q&A形式で完全な回答を書く（回答を途中で終わらせない）</p>

<h2>編集部の視点・総評</h2>
<p>【必須・200字以上】このガジェット・製品に対する編集部独自の意見・評価を書く。以下を必ず含めること：(1)競合製品との明確な差別化ポイント (2)実際に使った場合のメリット・デメリット (3)購入を迷っている人への具体的なアドバイス。「〜と思います」「〜でしょう」など主観的な表現を積極的に使い、情報の羅列ではなく本音のコメントを書くこと。</p>

<h2>まとめ</h2>
<p>150字以上で締めくくり。{hints["summary_hint"]}</p>

【ルール】
- JSONのみ出力（前後に説明文不要）
- 元記事の文章をそのままコピーしない（重複コンテンツ回避）
- 全体2000字以上（薄いコンテンツ回避・AdSense審査基準を満たすため）
- 「編集部の視点・総評」セクションは必ず200字以上の独自考察を書く（最重要）
- h2・h3タグと<p>・<table>タグを使用したHTML
- point-boxの各<li>は体言止めで30字以内。「〜できる」ではなく「〜性能」「〜対応」などの名詞止め
- 読者が「この記事を読んでよかった」と思える独自情報・視点を必ず含める
- 「編集部の視点・総評」は200字未満の場合は失格とする（必ず200字以上書くこと）
- 他製品との比較は必ず現行最新モデルを対象にする（型番が古いモデルを「現在の〜」と表現しない）
- リーク・予測情報は「〜とされている」「〜の可能性がある」など断定しない表現を使う
- 比較元のスペック（現行モデルのメモリ・チップなど）もリーク・未確認の場合は「〜とされる」「〜と伝えられる」と断定しない
- 価格・発売情報セクションの末尾に「AmazonやAmazonでも在庫・価格を確認できます」という自然な一文を添える（強引なセールストークは不要）
- 「こんな人におすすめ」セクションで具体的なユーザー像を3つ挙げ、各々に「〜な方にとって〇〇なのでおすすめです」という形で購入理由を明示する{numbers_rule}{lessons_section}"""


# ===== PERPLEXITY ENRICHMENT =====
def enrich_with_perplexity(title: str, article_url: str = "") -> str:
    """Perplexity API で記事本文の要約＋最新スペック情報を一括取得する"""
    api_key = os.environ.get("PERPLEXITY_API_KEY", "")
    if not api_key:
        return ""
    url_hint = f"\n参照記事URL: {article_url}" if article_url else ""
    try:
        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "sonar",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"「{title}」について以下の2点を日本語でまとめてください。{url_hint}\n\n"
                            f"## 1. 記事の主な内容（上記URLの記事を参照し、製品の特徴・評価・詳細を200字程度で要約）\n\n"
                            f"## 2. 最新スペック・価格情報（2026年時点の確認済み情報のみ）\n"
                            f"- 価格（円）\n"
                            f"- 発売日\n"
                            f"- 主要スペック（画面サイズ・CPU・メモリ・ストレージ・バッテリーなど該当するもの）\n"
                            f"- 主な特徴・強み\n"
                            f"- 比較対象がある場合は現行最新モデルとの違い\n"
                            f"情報がない項目は省略してください。"
                        )
                    }
                ],
                "max_tokens": 1200,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                print(f"Perplexity API: unexpected response structure: {str(data)[:200]}")
            return content
        print(f"Perplexity API status: {resp.status_code}")
    except Exception as e:
        print(f"Perplexity API error: {e}")
    return ""


def evaluate_article(article: dict, generated_html: str) -> list:
    """生成記事を自己評価し、次回への改善ルールを1〜2件返す"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return []
    try:
        client = anthropic.Anthropic(api_key=api_key)
        prompt = f"""以下のガジェット記事をSEOとアフィリエイトコンバージョンの観点で評価してください。

元記事タイトル: {article['title']}
元記事概要: {article.get('summary', '')[:300]}

生成された記事のHTML（先頭2000文字）:
{generated_html[:2000]}

以下の観点で評価し、改善点を1〜2個、次回の記事生成時に適用できる具体的なルールとして返してください：
- SEO: タイトルに検索されやすいキーワードが含まれているか、見出し構造が適切か
- コンバージョン: 読者が「買いたい」と思えるベネフィット訴求ができているか（嘘・誇張は禁止）
- 信頼性: 事実と推測が明確に区別されているか

JSONの文字列リスト形式のみで出力してください（例: ["改善点1", "改善点2"]）"""

        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()
        m = re.search(r'\[[\s\S]+\]', raw)
        if m:
            return json.loads(m.group())
        print(f"evaluate_article: JSONリストが見つかりません: {raw[:100]}")
    except Exception as e:
        print(f"記事評価エラー: {e}")
    return []


def generate_article(article, image_data: bytes | None = None):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=api_key)
    hints = _get_hints(article["title"])

    # OGP画像のビジョン分析
    image_analysis = ""
    if image_data:
        image_analysis = analyze_image_with_claude(image_data, article["title"])
        if image_analysis:
            print(f"画像分析完了 ({len(image_analysis)}文字)")

    # Perplexityで記事本文要約＋最新スペック情報を一括取得
    perplexity_info = enrich_with_perplexity(article["title"], article["url"])
    if perplexity_info:
        print(f"Perplexity情報取得済み ({len(perplexity_info)}文字)")

    recent_lessons = load_recent_lessons(n=3)
    if recent_lessons:
        print(f"過去の教訓 {len(recent_lessons)}件を適用")

    prompt = _build_prompt(article, hints, perplexity_info, recent_lessons, image_analysis)

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = message.content[0].text.strip()
    m = re.search(r'\{[\s\S]+\}', raw)
    if not m:
        raise ValueError(f"JSONが見つかりません: {raw[:200]}")
    result = json.loads(m.group())

    # タイトルの全角スペース・余分な空白を正規化
    if "title" in result:
        result["title"] = result["title"].replace("\u3000", " ").strip()

    new_lessons = evaluate_article(article, result.get("content", ""))
    if new_lessons:
        save_lessons(new_lessons)
        print(f"教訓 {len(new_lessons)}件を保存")

    return result

def get_rakuten_url(keyword):
    encoded = urllib.parse.quote(keyword, safe="")
    rakuten_search = f"https://search.rakuten.co.jp/search/mall/{encoded}/"
    encoded_rakuten = urllib.parse.quote(rakuten_search, safe="")
    return (
        f"https://af.moshimo.com/af/c/click"
        f"?a_id={MOSHIMO_A_ID}&p_id=54&pc_id=54&pl_id=616"
        f"&url={encoded_rakuten}"
    )

# ===== OGP IMAGE =====
def fetch_ogp_image(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; GadgetBlogBot/1.0)"}
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            img_url = og_image["content"]
            img_resp = requests.get(img_url, headers=headers, timeout=10)
            if img_resp.status_code == 200 and len(img_resp.content) > 0:
                return img_resp.content
    except Exception as e:
        print(f"OGP image fetch error: {e}")
    return None

# ===== X (TWITTER) =====
SOURCE_HASHTAGS = {
    "価格.com 新製品":  "#ガジェット #新製品",
    "PC Watch":         "#PC #ガジェット",
    "AV Watch":         "#AV機器 #ガジェット",
    "Akiba PC Hotline": "#自作PC #ガジェット",
    "ASCII.jp":         "#ガジェット #テック",
    "ITmedia ニュース":  "#ガジェット #テック",
    "Engadget Japan":   "#ガジェット #レビュー",
    "GIZMODO Japan":    "#ガジェット #テック",
}

CATEGORY_HASHTAGS = {
    "smartphone":        "#スマホ #スマートフォン",
    "earphone":          "#イヤホン #ワイヤレスイヤホン",
    "headphone":         "#ヘッドホン",
    "camera":            "#カメラ #写真",
    "pc":                "#PC #パソコン",
    "laptop":            "#ノートPC",
    "gaming":            "#ゲーミング",
    "tablet":            "#タブレット",
    "smart-home":        "#スマートホーム",
    "wearable":          "#ウェアラブル #スマートウォッチ",
    "speaker":           "#スピーカー #音楽",
    "keyboard":          "#キーボード",
    "monitor":           "#モニター #ディスプレイ",
    "router":            "#WiFi #ルーター",
    "battery":           "#モバイルバッテリー",
    "drone":             "#ドローン",
    "storage":           "#SSD #ストレージ",
    "projector":         "#プロジェクター",
    "tv":                "#テレビ #4K",
    "audio":             "#オーディオ #音楽",
}


def get_hashtags(source: str, category_slug: str = "") -> str:
    source_tag = SOURCE_HASHTAGS.get(source, "#ガジェット #新製品")
    category_tag = CATEGORY_HASHTAGS.get(category_slug, "")
    if category_tag:
        return f"{category_tag} {source_tag}"
    return source_tag


def extract_product_hashtag(tweet_text: str) -> str | None:
    """ツイート文の「製品名」からハッシュタグを1つ生成する"""
    m = re.search(r'「([^」]+)」', tweet_text)
    if not m:
        return None
    hashtag = re.sub(r'[\s\u3000\-\/\(\)\.\,・\"\']', '', m.group(1))
    if len(hashtag) < 2 or len(hashtag) > 15:
        return None
    return f"#{hashtag}"


def upload_media_to_x(image_data: bytes) -> str | None:
    """X v1.1 APIで画像をアップロードしてmedia_idを返す。失敗時はNone。"""
    if not image_data:
        return None
    try:
        auth = tweepy.OAuth1UserHandler(
            os.environ["X_API_KEY"],
            os.environ["X_API_KEY_SECRET"],
            os.environ["X_ACCESS_TOKEN"],
            os.environ["X_ACCESS_TOKEN_SECRET"]
        )
        api_v1 = tweepy.API(auth)
        media = api_v1.media_upload(filename="ogp.jpg", file=io.BytesIO(image_data))
        print(f"メディアアップロード完了 → media_id={media.media_id}")
        return str(media.media_id)
    except Exception as e:
        print(f"メディアアップロードエラー（テキストのみで投稿）: {e}")
        return None


def post_to_x(tweet_text: str, media_ids: list | None = None, reply_to_tweet_id: str | None = None) -> str | None:
    """X v2 APIでツイートを投稿する。成功時はtweet_idを返す。失敗時は例外を発生させる。"""
    worker_url = os.environ.get("CF_WORKER_URL", "")
    worker_key = os.environ.get("CF_WORKER_KEY", "")

    if not worker_url or reply_to_tweet_id:
        # Worker未設定時、またはリプライ投稿時は tweepy 直接呼び出し（WorkerはreplyAPIに未対応）
        client = tweepy.Client(
            consumer_key=os.environ["X_API_KEY"],
            consumer_secret=os.environ["X_API_KEY_SECRET"],
            access_token=os.environ["X_ACCESS_TOKEN"],
            access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"]
        )
        kwargs = {"text": tweet_text}
        if media_ids:
            kwargs["media_ids"] = media_ids
        if reply_to_tweet_id:
            kwargs["in_reply_to_tweet_id"] = reply_to_tweet_id
        response = client.create_tweet(**kwargs)
        tweet_id = str(response.data["id"])
        print(f"X投稿完了 → https://x.com/i/web/status/{tweet_id}")
        return tweet_id

    payload = {"text": tweet_text}
    if media_ids:
        payload["media_ids"] = media_ids

    resp = requests.post(
        worker_url,
        json=payload,
        headers={"X-Worker-Key": worker_key},
        timeout=15,
    )
    if not resp.ok:
        raise Exception(f"Worker経由X投稿エラー: {resp.status_code} - {resp.text[:300]}")
    data = resp.json()
    tweet_id = str(data.get("data", {}).get("id", "unknown"))
    print(f"X投稿完了（Worker経由）→ https://x.com/i/web/status/{tweet_id}")
    return tweet_id


def generate_blog_tweet(article: dict) -> str:
    """WordPressブログ記事の紹介ツイートを生成する。URLはリプライで投稿するため含めない。"""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = f"""以下のガジェット・家電ブログ記事をもとに、Xの投稿文を日本語で作成してください。

記事タイトル: {article['title']}
記事概要: {article['summary']}

フォーマット：
1行目: 数字・驚き・問いかけのいずれかで始めるフック文（例:「1万円以下でこの性能、どういうこと？」「バッテリー持続48時間のイヤホンが登場」「え、これ本当に○○円?」）
2行目: 製品の一番尖った特徴を1文で
3行目以降: 補足（価格情報があれば「実売X万円前後」の形で含める）
最後: 「詳しくはブログで」「気になる人はチェック」などで締める

ルール：
- URLは含めない（リプライで別途投稿するため）
- ですます調は使わない
- 絵文字は冒頭に1〜2個だけ使ってよい（📱💻🎧🖥️⌨️📷🔋など製品に合うもの）
- 140〜160文字程度で簡潔に
- 投稿文のみ出力（説明不要）"""
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )
    tweet_body = message.content[0].text.strip()
    hashtags = get_hashtags(article.get("source", ""), article.get("category_slug", ""))
    product_hashtag = extract_product_hashtag(tweet_body)
    if product_hashtag:
        hashtags = f"{product_hashtag} {hashtags}"
    return f"{tweet_body}\n\n{hashtags} #PR\n↓ リンクはリプ欄"


def post_article_to_x(article: dict, wp_url: str, amazon_url: str, rakuten_url: str, image_data: bytes | None) -> None:
    """ブログ記事をXに投稿する。メインツイート → リプライ（リンク）の2回投稿。失敗してもWordPress投稿には影響しない。"""
    try:
        tweet_text = generate_blog_tweet(article)
        print(f"生成されたツイート:\n{tweet_text}\n")
        media_id = upload_media_to_x(image_data) if image_data else None
        tweet_id = post_to_x(tweet_text, media_ids=[media_id] if media_id else None)
        if tweet_id:
            reply_text = f"📝 ブログ → {wp_url}\n🛒 Amazon → {amazon_url}\n🛍️ 楽天 → {rakuten_url}"
            post_to_x(reply_text, reply_to_tweet_id=tweet_id)
            print(f"リプライ投稿完了 → tweet_id={tweet_id}")
    except Exception as e:
        import traceback
        print(f"X投稿エラー: {e}")
        traceback.print_exc()
        if hasattr(e, "response") and e.response is not None:
            print(f"X APIエラー詳細: {e.response.text}")


# ===== MAIN =====
def main():
    posted_urls_list, skip_urls_dict = load_state()
    posted_urls_set = set(posted_urls_list)

    articles, new_skips = fetch_new_articles(posted_urls_set, skip_urls_dict)
    skip_urls_dict.update(new_skips)

    if not articles:
        print("新着記事なし")
        save_state(posted_urls_list, skip_urls_dict)
        return

    article = articles[0]  # priority+review優先（ソート済み）
    print(f"処理中: {article['title']}")

    # 画像を先に取得（記事生成のビジョン分析に使うため）
    image_data = fetch_ogp_image(article["url"])

    try:
        generated = generate_article(article, image_data=image_data)
    except Exception as e:
        print(f"記事生成エラー: {e}")
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        skip_urls_dict[article["url"]] = today_str  # 生成失敗はskip（TTL内で再試行しない）
        save_state(posted_urls_list, skip_urls_dict)
        return

    amazon_url = extract_amazon_url(article, fallback_keyword=generated.get("product_name", ""))
    rakuten_keyword = generated.get("product_name") or article["title"]
    rakuten_url = get_rakuten_url(rakuten_keyword)
    html_content = build_post_html(generated["content"], article["url"], amazon_url, rakuten_url)

    featured_media_id = None
    if image_data:
        featured_media_id = upload_image_to_wp(image_data)

    try:
        excerpt = generated.get("meta_description") or ""
        category_slug = generated.get("category_slug") or None
        category_name = generated.get("category_name") or ""
        post_url = post_to_wordpress(generated["title"], html_content, featured_media_id, excerpt, category_slug, category_name)
        print(f"投稿完了: {post_url}")
        posted_urls_set.add(article["url"])
        save_state(list(posted_urls_set), skip_urls_dict)
        article["category_slug"] = category_slug or ""
        post_article_to_x(article, post_url, amazon_url, rakuten_url, image_data)
    except Exception as e:
        print(f"WordPress投稿エラー: {e}")
        posted_urls_set.add(article["url"])
        save_state(list(posted_urls_set), skip_urls_dict)

if __name__ == "__main__":
    main()
