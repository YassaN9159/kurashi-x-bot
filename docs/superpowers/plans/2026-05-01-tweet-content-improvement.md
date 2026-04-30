# kurashi-x-bot ツイート内容改善 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** evaluate_article バグ修正・記事タイプ検出・プロンプト分岐の3点を実装し、ツイートのエンゲージメント（いいね・保存）とアフィリエイトクリック率を改善する

**Architecture:** `bot.py` 1ファイルのみを変更する。タイプ検出ヘルパー（`_is_new_product` / `_is_sale`）をfetch時にarticle dictにフラグとして付与し、`_build_prompt()` がそのフラグを受け取って3分岐のフォーマット指示と末尾促進文を生成する。

**Tech Stack:** Python 3.11、pytest、anthropic SDK、feedparser

---

## ファイル構成

| ファイル | 変更内容 |
|---|---|
| `bot.py` | 定数追加、ヘルパー2関数追加、fetch_new_articles()のarticle dictに2フラグ追加、_build_prompt()シグネチャ変更と分岐追加、generate_article()の呼び出し変更、evaluate_article()のバグ修正 |
| `tests/test_bot.py` | 7テスト追加 |

---

## Task 1: evaluate_article バグ修正

**Files:**
- Modify: `bot.py:554-556`（evaluate_article 内の raw パース箇所）
- Test: `tests/test_bot.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_bot.py` の末尾に追加:

```python
# ===== 13. evaluate_article — コードブロック付きJSON出力を正しくパースできる =====
def test_evaluate_article_parses_fenced_json():
    article = {"title": "無印良品の収納ボックス", "summary": ""}
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text='```json\n["絵文字を減らす", "文字数を短くする"]\n```')]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = bot.evaluate_article(article, "サンプルツイート")

    assert result == ["絵文字を減らす", "文字数を短くする"]


# ===== 14. evaluate_article — 空リストを正しく返す =====
def test_evaluate_article_parses_empty_list():
    article = {"title": "無印良品の収納ボックス", "summary": ""}
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text='```json\n[]\n```')]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = bot.evaluate_article(article, "サンプルツイート")

    assert result == []
```

- [ ] **Step 2: テストが失敗することを確認**

```bash
cd /c/Users/yassa/kurashi-x-bot
pytest tests/test_bot.py::test_evaluate_article_parses_fenced_json tests/test_bot.py::test_evaluate_article_parses_empty_list -v
```

Expected: FAIL（`result` が `[]` になる — バグにより空リストが返る）

- [ ] **Step 3: バグを修正する**

`bot.py` の `evaluate_article()` 内（現在の554〜556行目付近）を以下に変更:

```python
        raw = message.content[0].text.strip()
        raw = re.sub(r'```(?:json)?\n?', '', raw).strip()
        m = re.search(r'\[[\s\S]*\]', raw)
        if m:
            return json.loads(m.group())
        print(f"evaluate_article: JSONリストが見つかりません: {raw[:100]}")
```

- [ ] **Step 4: テストが通ることを確認**

```bash
pytest tests/test_bot.py::test_evaluate_article_parses_fenced_json tests/test_bot.py::test_evaluate_article_parses_empty_list -v
```

Expected: PASS（2/2）

- [ ] **Step 5: 全テストが壊れていないことを確認**

```bash
pytest tests/test_bot.py -v
```

Expected: 全テスト PASS

- [ ] **Step 6: コミット**

```bash
git add bot.py tests/test_bot.py
git commit -m "fix: evaluate_article のコードブロック付きJSON出力をパースできるよう修正"
```

---

## Task 2: 記事タイプ検出ヘルパー追加

**Files:**
- Modify: `bot.py`（定数2つ追加、ヘルパー2関数追加、articles.append() にフラグ2つ追加）
- Test: `tests/test_bot.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_bot.py` の末尾に追加:

```python
# ===== 15. _is_new_product — 新発売キーワードを検出する =====
def test_is_new_product_hit():
    assert bot._is_new_product("山崎実業から新発売の収納ラック登場") is True
    assert bot._is_new_product("ニトリの新モデルチェアが発売開始") is True

def test_is_new_product_miss():
    assert bot._is_new_product("収納上手になる5つのコツ") is False
    assert bot._is_new_product("無印良品セールで半額") is False


# ===== 16. _is_sale — セールキーワードを検出する =====
def test_is_sale_hit():
    assert bot._is_sale("ホットクックが3000円引きのセール開催中") is True
    assert bot._is_sale("IKEAの人気チェアが20%オフ") is True

def test_is_sale_miss():
    assert bot._is_sale("インテリアをすっきり見せる方法") is False
    assert bot._is_sale("無印良品の新作収納ボックスが登場") is False
```

- [ ] **Step 2: テストが失敗することを確認**

```bash
pytest tests/test_bot.py::test_is_new_product_hit tests/test_bot.py::test_is_new_product_miss tests/test_bot.py::test_is_sale_hit tests/test_bot.py::test_is_sale_miss -v
```

Expected: FAIL（`_is_new_product` / `_is_sale` が未定義）

- [ ] **Step 3: 定数を追加する**

`bot.py` の `PRIORITY_KEYWORDS` リスト（現在の79〜86行目）の直後に追加:

```python
NEW_PRODUCT_KEYWORDS = [
    "新発売", "発売開始", "発表", "登場", "新モデル", "新型", "新商品", "初登場",
]
SALE_KEYWORDS_LIST = [
    "セール", "割引", "タイムセール", "クーポン", "特価", "円引き", "オフ", "お得",
]
```

- [ ] **Step 4: ヘルパー関数を追加する**

`bot.py` の `_is_priority()` 関数（現在の204〜205行目）の直後に追加:

```python
def _is_new_product(title: str) -> bool:
    return any(kw in title for kw in NEW_PRODUCT_KEYWORDS)

def _is_sale(title: str) -> bool:
    return any(kw in title for kw in SALE_KEYWORDS_LIST)
```

- [ ] **Step 5: テストが通ることを確認**

```bash
pytest tests/test_bot.py::test_is_new_product_hit tests/test_bot.py::test_is_new_product_miss tests/test_bot.py::test_is_sale_hit tests/test_bot.py::test_is_sale_miss -v
```

Expected: PASS（4/4）

- [ ] **Step 6: articles.append() にフラグを追加する**

`bot.py` の `fetch_new_articles()` 内の `articles.append(...)` ブロック（現在の288〜295行目付近）を以下に変更:

```python
                articles.append({
                    "url": url,
                    "title": title,
                    "summary": summary,
                    "source": feed_info["name"],
                    "priority": is_prio,
                    "trusted": trusted,
                    "is_new": _is_new_product(title),
                    "is_sale": _is_sale(title),
                })
```

- [ ] **Step 7: 全テストが壊れていないことを確認**

```bash
pytest tests/test_bot.py -v
```

Expected: 全テスト PASS

- [ ] **Step 8: コミット**

```bash
git add bot.py tests/test_bot.py
git commit -m "feat: 記事タイプ検出ヘルパー (_is_new_product / _is_sale) を追加"
```

---

## Task 3: `_build_prompt()` タイプ別分岐追加

**Files:**
- Modify: `bot.py`（`_build_prompt()` シグネチャ変更・分岐追加、`generate_article()` 呼び出し変更）
- Test: `tests/test_bot.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_bot.py` の末尾に追加:

```python
# ===== 17. _build_prompt — is_new=True でいいね訴求文が含まれる =====
def test_build_prompt_new_product_contains_like_cta():
    article = {
        "title": "山崎実業から新発売のラック登場",
        "summary": "山崎実業から新しいラックが登場しました。",
    }
    prompt = bot._build_prompt(article, is_new=True)
    assert "いいね" in prompt


# ===== 18. _build_prompt — is_sale=True でセール訴求文が含まれる =====
def test_build_prompt_sale_contains_sale_hook():
    article = {
        "title": "ホットクックが3000円引きセール",
        "summary": "ホットクックが期間限定セール中です。",
    }
    prompt = bot._build_prompt(article, is_sale=True)
    assert "セール" in prompt or "お得" in prompt or "いいね" in prompt


# ===== 19. _build_prompt — is_new=False, is_sale=False で保存訴求文が含まれる =====
def test_build_prompt_lifestyle_contains_save_cta():
    article = {
        "title": "リビングのインテリアをすっきり見せるコツ",
        "summary": "インテリアをすっきり見せる方法をご紹介します。",
    }
    prompt = bot._build_prompt(article, is_new=False, is_sale=False)
    assert "保存" in prompt or "ブックマーク" in prompt
```

- [ ] **Step 2: テストが失敗することを確認**

```bash
pytest tests/test_bot.py::test_build_prompt_new_product_contains_like_cta tests/test_bot.py::test_build_prompt_sale_contains_sale_hook tests/test_bot.py::test_build_prompt_lifestyle_contains_save_cta -v
```

Expected: FAIL（`_build_prompt` に `is_new`/`is_sale` 引数がない、または prompt に各文言が含まれない）

- [ ] **Step 3: `_build_prompt()` のシグネチャと分岐を実装する**

`bot.py` の `_build_prompt()` 関数定義（現在の423行目付近）のシグネチャを変更:

```python
def _build_prompt(article: dict, perplexity_info: str = "", lessons: list = None,
                  image_analysis: str = "", is_new: bool = False, is_sale: bool = False) -> str:
```

同関数内の `return f"""..."""` のルール欄末尾（`{lessons_section}` の直前）に以下の分岐ブロックを追加:

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
    format_section = f"\n【フォーマット指示】\n{format_instruction}\n{last_lines}"
```

同関数の `return f"""..."""` 内のルール欄（`{lessons_section}` の直前）に `{format_section}` を挿入:

```python
    return f"""あなたは暮らし・インテリア・収納の専門家として X（旧Twitter）に投稿するアカウントです。
...（既存テキストそのまま）...
{format_section}
{lessons_section}

以下のJSON形式のみで出力してください...
```

- [ ] **Step 4: `generate_article()` の呼び出しを変更する**

`bot.py` の `generate_article()` 内の `_build_prompt()` 呼び出し（現在の584行目付近）を変更:

```python
    prompt = _build_prompt(
        article, perplexity_info, recent_lessons, image_analysis,
        is_new=article.get("is_new", False),
        is_sale=article.get("is_sale", False),
    )
```

- [ ] **Step 5: テストが通ることを確認**

```bash
pytest tests/test_bot.py::test_build_prompt_new_product_contains_like_cta tests/test_bot.py::test_build_prompt_sale_contains_sale_hook tests/test_bot.py::test_build_prompt_lifestyle_contains_save_cta -v
```

Expected: PASS（3/3）

- [ ] **Step 6: 全テストが通ることを確認**

```bash
pytest tests/test_bot.py -v
```

Expected: 全テスト PASS（19/19）

- [ ] **Step 7: コミット**

```bash
git add bot.py tests/test_bot.py
git commit -m "feat: _build_prompt に記事タイプ別フォーマット分岐と行動促進文を追加"
```

- [ ] **Step 8: プッシュ**

```bash
git push
```
