import os
import io
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
                raw = json.load(f)
                data = raw if isinstance(raw, dict) else {"lessons": []}
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
                raw = json.load(f)
                # 旧形式（list）を新形式（dict）に変換
                data = raw if isinstance(raw, dict) else {"lessons": []}
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

# ===== FETCH RSS =====
def _is_excluded(title: str) -> bool:
    return any(kw in title for kw in EXCLUDE_KEYWORDS)

def _is_kurashi_related(title: str) -> bool:
    return any(kw in title for kw in KURASHI_KEYWORDS)

def _is_priority(title: str) -> bool:
    return any(kw in title for kw in PRIORITY_KEYWORDS)

def _is_kurashi_by_claude(title: str, source: str) -> bool:
    """Claude Haiku で記事が暮らし・インテリア・収納関連か判定する。
    API失敗時は True（フォールスルー）を返す。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return True
    client = anthropic.Anthropic(api_key=api_key)
    prompt = (
        f"以下の記事タイトルとソースが、暮らし・インテリア・収納・家事・新居に関連する内容か判定してください。\n"
        f"タイトル: {title}\nソース: {source}\n"
        f"関連する場合は「YES」、関連しない場合は「NO」とだけ答えてください。"
    )
    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}]
        )
        return "YES" in message.content[0].text.upper()
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
                if not _is_kurashi_by_claude(title, feed_info["name"]):
                    stats["rejected_by_claude"] += 1
                    print(f"  [Claude除外] {short}")
                    new_skips[url] = today_str
                    continue

                stats["passed"] += 1
                is_prio = _is_priority(title)
                tier = "★priority" if is_prio else "📄normal"
                print(f"  [通過✓] [{tier}] {short}")
                articles.append({
                    "url": url,
                    "title": title,
                    "summary": entry.get("summary", "")[:500],
                    "source": feed_info["name"],
                    "priority": is_prio,
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


def _build_prompt(article: dict, perplexity_info: str = "", lessons: list = None,
                  image_analysis: str = "") -> str:
    if lessons is None:
        lessons = []
    title = article["title"]
    summary = article["summary"]

    perplexity_section = ""
    if perplexity_info:
        perplexity_section = f"\n## 参考情報（Perplexity調査結果）\n{perplexity_info}\n"

    image_section = ""
    if image_analysis:
        image_section = f"\n## 画像から読み取れた情報\n{image_analysis}\n"

    lessons_section = ""
    if lessons:
        lesson_lines = "\n".join(f"- {l}" for l in lessons)
        lessons_section = f"\n【過去の改善指示（必ず守ること）】\n{lesson_lines}"

    return f"""あなたは暮らし・インテリア・収納の専門家として X（旧Twitter）に投稿するアカウントです。
ターゲット：来年マイホームを建てる、または新居生活に向けて準備中の女性。楽天ROOMユーザーが共感・保存したくなる内容にする。

以下の記事をもとに X 投稿文を作成してください。

記事タイトル: {title}
記事概要: {summary}
{image_section}{perplexity_section}
投稿タイプは以下のどちらかを選ぶ：
1. Tips 投稿：「○○のコツ」「○○する方法」などの実用情報（記事から学べるポイントを抽出）
2. 商品紹介：具体的な商品の魅力と使いどころ（記事に登場する商品がある場合）

ルール：
- 文体：丁寧語（です・ます調）
- 絵文字：冒頭または途中に 1〜2 個だけ使う（🏠🛋️🌿🧹✨など暮らしに合うもの）
- 文字数：140〜260 字（ハッシュタグ含む）
- URL は含めない（リプライで別途投稿するため）
- ハッシュタグを末尾に 2〜3 個
- 投稿文のみ出力（説明・前置き不要）
{lessons_section}

以下のJSON形式のみで出力してください：
{{"tweet_text": "Xに投稿するツイート本文（ハッシュタグ含む）", "product_name": "楽天・Amazon検索に使う商品名（商品がない場合は空文字）"}}"""


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


def evaluate_article(article: dict, generated_tweet: str) -> list:
    """生成ツイートを自己評価し、次回への改善ルールを1〜3件返す"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return []
    try:
        client = anthropic.Anthropic(api_key=api_key)
        prompt = f"""以下のXツイートを評価し、次回の改善点を抽出してください。

元記事タイトル: {article['title']}
生成されたツイート:
{generated_tweet}

評価の観点：
- 楽天ROOMユーザー（暮らし・インテリア好きな女性）に響く内容か
- 丁寧語（です・ます調）で書かれているか
- 絵文字の使い方は適切か
- 具体的で役立つ情報が含まれているか
- 140〜260字の範囲に収まっているか

改善点を箇条書き（1〜3個、各20字以内）でJSONリスト形式のみで出力してください：
["改善点1", "改善点2"]
改善点が特にない場合は空のリスト [] を返す。"""

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

    # OGP画像のビジョン分析
    image_analysis = ""
    if image_data:
        image_analysis = analyze_image_with_claude(image_data, article["title"])
        if image_analysis:
            print(f"画像分析完了 ({len(image_analysis)}文字)")

    # Perplexity で記事内容・商品情報を補足
    perplexity_info = enrich_with_perplexity(article["title"], article["url"])
    if perplexity_info:
        print(f"Perplexity情報取得済み ({len(perplexity_info)}文字)")

    recent_lessons = load_recent_lessons(n=3)
    if recent_lessons:
        print(f"過去の教訓 {len(recent_lessons)}件を適用")

    prompt = _build_prompt(article, perplexity_info, recent_lessons, image_analysis)

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = message.content[0].text.strip()
    m = re.search(r'\{[\s\S]+\}', raw)
    if not m:
        raise ValueError(f"JSONが見つかりません: {raw[:200]}")
    result = json.loads(m.group())

    new_lessons = evaluate_article(article, result.get("tweet_text", ""))
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
def get_hashtags(source: str, keyword: str = "") -> str:
    base = "#暮らし #インテリア #マイホーム"
    if keyword:
        for k, tag in [
            ("収納", "#収納"),
            ("インテリア", "#インテリア"),
            ("新築", "#新築"),
            ("マイホーム", "#マイホーム"),
            ("キッチン", "#キッチン"),
            ("掃除", "#掃除"),
            ("観葉植物", "#観葉植物"),
            ("無印良品", "#無印良品"),
            ("IKEA", "#IKEA"),
            ("ニトリ", "#ニトリ"),
        ]:
            if k in keyword:
                return f"{tag} {base}"
    return base


def extract_product_hashtag(tweet_text: str) -> str | None:
    """ツイート文の「製品名」からハッシュタグを1つ生成する"""
    m = re.search(r'「([^」]+)」', tweet_text)
    if not m:
        return None
    hashtag = re.sub(r'[\s\u3000\-\/\(\)\.\,・\"\']', '', m.group(1))
    if len(hashtag) < 2 or len(hashtag) > 15:
        return None
    return f"#{hashtag}"


def _get_api_v1() -> tweepy.API:
    """OAuth1認証済みのv1.1 APIクライアントを返す。"""
    auth = tweepy.OAuth1UserHandler(
        os.environ["X_API_KEY"],
        os.environ["X_API_KEY_SECRET"],
        os.environ["X_ACCESS_TOKEN"],
        os.environ["X_ACCESS_TOKEN_SECRET"]
    )
    return tweepy.API(auth)


def upload_media_to_x(image_data: bytes) -> int | None:
    """X v1.1 APIで画像をアップロードしてmedia_idを返す。失敗時はNone。"""
    if not image_data:
        return None
    try:
        api_v1 = _get_api_v1()
        media = api_v1.media_upload(filename="ogp.jpg", file=io.BytesIO(image_data))
        print(f"メディアアップロード完了 → media_id={media.media_id}")
        return media.media_id
    except Exception as e:
        print(f"メディアアップロードエラー（テキストのみで投稿）: {e}")
        return None


def post_to_x(tweet_text: str, media_ids: list | None = None, reply_to_tweet_id: str | None = None) -> str | None:
    """X v1.1 APIでツイートを投稿する。成功時はtweet_idを返す。失敗時は例外を発生させる。"""
    api_v1 = _get_api_v1()
    kwargs = {"status": tweet_text}
    if media_ids:
        kwargs["media_ids"] = media_ids
    if reply_to_tweet_id:
        kwargs["in_reply_to_status_id"] = reply_to_tweet_id
        kwargs["auto_populate_reply_metadata"] = True
    status = api_v1.update_status(**kwargs)
    tweet_id = str(status.id)
    print(f"X投稿完了 → https://x.com/i/web/status/{tweet_id}")
    return tweet_id



def post_article_to_x(article: dict, tweet_text: str, amazon_url: str, rakuten_url: str, image_data: bytes | None) -> None:
    """ツイートを投稿。メインツイート → リプライ（アフィリエイトリンク）の2回投稿。"""
    try:
        print(f"生成されたツイート:\n{tweet_text}\n")
        media_id = upload_media_to_x(image_data) if image_data else None
        tweet_id = post_to_x(tweet_text, media_ids=[media_id] if media_id else None)
        if tweet_id:
            reply_text = f"🛒 Amazon → {amazon_url}\n🛍️ 楽天 → {rakuten_url}"
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

    article = articles[0]  # PRIORITY_KEYWORDS優先でソート済み
    print(f"処理中: {article['title']}")

    image_data = fetch_ogp_image(article["url"])

    try:
        generated = generate_article(article, image_data=image_data)
    except Exception as e:
        print(f"コンテンツ生成エラー: {e}")
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        skip_urls_dict[article["url"]] = today_str
        save_state(posted_urls_list, skip_urls_dict)
        return

    amazon_url = extract_amazon_url(article, fallback_keyword=generated.get("product_name", ""))
    rakuten_keyword = generated.get("product_name") or article["title"]
    rakuten_url = get_rakuten_url(rakuten_keyword)

    post_article_to_x(article, generated["tweet_text"], amazon_url, rakuten_url, image_data)

    posted_urls_set.add(article["url"])
    save_state(list(posted_urls_set), skip_urls_dict)
    print("完了")

if __name__ == "__main__":
    main()
