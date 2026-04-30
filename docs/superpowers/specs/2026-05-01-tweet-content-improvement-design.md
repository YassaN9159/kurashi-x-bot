# kurashi-x-bot ツイート内容改善設計

**日付:** 2026-05-01  
**課題:** エンゲージメント（いいね・保存）が少ない、アフィリエイトのクリック・収益が低い  
**原因仮説:** ツイート末尾に行動促進文がない、記事タイプに関係なく単一プロンプトで生成しているため訴求が弱い。また `evaluate_article` のバグにより自己改善ループが機能していない  
**方針:** 案C — 記事タイプ検出ヘルパー追加 + プロンプト分岐 + evaluate_article バグ修正

---

## 変更スコープ

| ファイル | 変更内容 |
|---|---|
| `bot.py` | evaluate_article バグ修正、記事タイプ定数・ヘルパー追加、_build_prompt() 分岐追加、generate_article() 呼び出し変更 |

---

## セクション 1：evaluate_article バグ修正

### 問題

Claude Haiku がJSON出力をコードブロック（` ```json ` ）で囲むため、正規表現 `\[[\s\S]+\]` がマッチせず `JSONリストが見つかりません` エラーが毎回発生している。結果として `lessons.json` に教訓が蓄積されず、ツイート品質の自己改善ループが機能していない。

### 変更箇所（`bot.py:554-556`）

```python
# 変更前
raw = message.content[0].text.strip()
m = re.search(r'\[[\s\S]+\]', raw)

# 変更後
raw = message.content[0].text.strip()
raw = re.sub(r'```(?:json)?\n?', '', raw).strip()
m = re.search(r'\[[\s\S]*\]', raw)
```

- `re.sub` でコードブロックのフェンス（` ```json ` / ` ``` `）を除去してからパース
- `+`（1文字以上）→ `*`（0文字以上）に変更し、空リスト `[]` にも対応

---

## セクション 2：記事タイプ検出ヘルパー

### 追加する定数（`PRIORITY_KEYWORDS` の直後）

```python
NEW_PRODUCT_KEYWORDS = [
    "新発売", "発売開始", "発表", "登場", "新モデル", "新型", "新商品", "初登場",
]
SALE_KEYWORDS_LIST = [
    "セール", "割引", "タイムセール", "クーポン", "特価", "円引き", "オフ", "お得",
]
```

### 追加するヘルパー関数（`_is_priority()` の直後）

```python
def _is_new_product(title: str) -> bool:
    return any(kw in title for kw in NEW_PRODUCT_KEYWORDS)

def _is_sale(title: str) -> bool:
    return any(kw in title for kw in SALE_KEYWORDS_LIST)
```

### `fetch_new_articles()` の article dict に追加

```python
articles.append({
    "url": url,
    "title": title,
    "summary": summary,
    "source": feed_info["name"],
    "priority": is_prio,
    "trusted": trusted,
    "is_new": _is_new_product(title),   # 追加
    "is_sale": _is_sale(title),         # 追加
})
```

---

## セクション 3：`_build_prompt()` のタイプ別分岐

### 関数シグネチャ変更

```python
def _build_prompt(article: dict, perplexity_info: str = "", lessons: list = None,
                  image_analysis: str = "", is_new: bool = False, is_sale: bool = False) -> str:
```

### プロンプト内フォーマット分岐ブロック（ルール欄末尾に挿入）

```python
if is_new:
    format_instruction = (
        "- フック：スペック・前モデルとの変化点・「買う価値があるか」の視点で書く\n"
        "- 例：「待ってたやつ」「これが出たら旧モデルがお得になりそう」"
    )
    last_lines = (
        "- 最後の1〜2文：「気になったらいいね👍」または「欲しいと思ったらいいねで教えて」\n"
        "  （※予算確認してから買いたい人向けには「後で見返したい方はブックマーク📌」も可）"
    )
elif is_sale:
    format_instruction = (
        "- フック：「今買うべき理由」「いくらお得か」を冒頭で明示する\n"
        "- 例：「〇〇円引きは見逃せない」「GW中だけのチャンス」"
    )
    last_lines = (
        "- 最後の1〜2文：「セール終わる前にいいねで保存👍」または「気になったらいいね」\n"
        "  （※いいね訴求に統一。リプライ誘導は不可）"
    )
else:
    format_instruction = (
        "- フック：「共感・真似したくなる」視点で書く。具体的な設置例・使い方シーンを入れる\n"
        "- 例：「これ知らなかった」「マイホームに絶対取り入れたい」"
    )
    last_lines = (
        "- 最後の1〜2文：以下から記事の文脈に合わせて1つ選ぶ\n"
        "  「マイホーム計画中の方は保存しておいて📌」（新居・間取り系）\n"
        "  「インテリアの参考になったら保存」（インテリア実例系）\n"
        "  「真似したいと思ったらブックマーク🔖」（収納・片付け系）\n"
        "  「後でゆっくり読み返して」（ハウツー・選び方系）"
    )
```

### `generate_article()` の呼び出し変更

```python
prompt = _build_prompt(
    article, perplexity_info, recent_lessons, image_analysis,
    is_new=article.get("is_new", False),
    is_sale=article.get("is_sale", False),
)
```

---

## 非機能要件

- RSSソース変更なし（現状の9ソースを維持）
- 投稿頻度変更なし（すでに1日8回）
- WordPressなし・X専用の構成は変更なし
- アフィリエイト（Amazon + 楽天もしも）のリンク生成ロジックは変更なし

---

## 成功指標

- `evaluate_article` の `JSONリストが見つかりません` エラーが発生しなくなること
- `lessons.json` に教訓が蓄積されること
- ツイート末尾に保存/いいね促進文が含まれること
- 新発売・セール記事で適切なフックが生成されること
