import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import bot


# ===== 1. load_state — state.json なし =====
def test_load_state_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STATE_FILE", str(tmp_path / "state.json"))
    posted, skip = bot.load_state()
    assert posted == []
    assert skip == {}


# ===== 2. load_state — 既存 state.json を正しく読む =====
def test_load_state_existing(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    from datetime import datetime, timezone, timedelta
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state_file.write_text(json.dumps({
        "posted_urls": ["https://example.com/1", "https://example.com/2"],
        "skip_urls": {"https://example.com/skip": today},
    }), encoding="utf-8")
    monkeypatch.setattr(bot, "STATE_FILE", str(state_file))

    posted, skip = bot.load_state()
    assert posted == ["https://example.com/1", "https://example.com/2"]
    assert "https://example.com/skip" in skip


# ===== 3. _is_kurashi_related — KURASHI_KEYWORDS ヒット =====
def test_is_kurashi_related_hit():
    assert bot._is_kurashi_related("リビングのインテリアを整えるコツ") is True
    assert bot._is_kurashi_related("収納上手になる5つの方法") is True


# ===== 4. _is_kurashi_related — ヒットなし =====
def test_is_kurashi_related_miss():
    assert bot._is_kurashi_related("最新スマートフォン比較2025") is False
    assert bot._is_kurashi_related("株式投資入門") is False


# ===== 5. _is_kurashi_related — ブランド名 (無印良品/IKEA) =====
def test_is_kurashi_related_brand():
    assert bot._is_kurashi_related("無印良品の新作シェルフが話題") is True
    assert bot._is_kurashi_related("IKEAの人気チェア完全ガイド") is True


# ===== 6. _is_priority — PRIORITY_KEYWORDS ヒット =====
def test_is_priority_hit():
    assert bot._is_priority("マイホーム建築で後悔しない間取り術") is True
    assert bot._is_priority("ニトリの収納グッズが最強な理由") is True


# ===== 7. _is_priority — ヒットなし =====
def test_is_priority_miss():
    assert bot._is_priority("観葉植物の育て方ガイド") is False
    assert bot._is_priority("キッチン掃除の時短テクニック") is False


# ===== 8. _is_excluded — スマホ/PC → True（除外） =====
def test_is_excluded_smartphone():
    assert bot._is_excluded("最新iPhone 16レビュー") is True
    assert bot._is_excluded("ノートPCおすすめランキング2025") is True


# ===== 9. _is_excluded — 収納記事 → False（除外されない） =====
def test_is_excluded_passes_kurashi():
    assert bot._is_excluded("収納上手になる5つのコツ") is False
    assert bot._is_excluded("インテリアをおしゃれに見せる方法") is False


# ===== 10. get_rakuten_url — URLに moshimo と MOSHIMO_A_ID が含まれる =====
def test_get_rakuten_url_contains_moshimo():
    url = bot.get_rakuten_url("収納ボックス")
    assert "moshimo.com" in url
    assert bot.MOSHIMO_A_ID in url


# ===== 11. generate_article — Claude APIモックで tweet_text と product_name を返す =====
def test_generate_article_returns_tweet_text():
    article = {
        "url": "https://example.com/article",
        "title": "無印良品の収納ボックスが人気",
        "summary": "無印良品の収納ボックスが整理整頓に最適と話題になっています。",
        "source": "暮らしニスタ",
        "priority": True,
    }

    mock_message = MagicMock()
    mock_message.content = [MagicMock(text='{"tweet_text": "無印良品の収納ボックスが整理整頓に最適！ #収納 #無印良品 #暮らし", "product_name": "無印良品 収納ボックス"}')]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message

    with patch("bot.enrich_with_perplexity", return_value=""), \
         patch("bot.load_recent_lessons", return_value=[]), \
         patch("bot.evaluate_article", return_value=[]), \
         patch("bot.save_lessons"), \
         patch("anthropic.Anthropic", return_value=mock_client):
        result = bot.generate_article(article, image_data=None)

    assert "tweet_text" in result
    assert "product_name" in result
    assert isinstance(result["tweet_text"], str)
    assert len(result["tweet_text"]) > 0


# ===== 12. save_state — 200件超えたら古いものを切り捨てる =====
def test_save_state_trims_old_urls(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(bot, "STATE_FILE", str(state_file))

    # 201件の URL を渡す
    urls = [f"https://example.com/{i}" for i in range(201)]
    bot.save_state(urls, {})

    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert len(saved["posted_urls"]) == bot.MAX_STATE_URLS
    # 古い先頭 URL が削除され、末尾の URL が残っている
    assert saved["posted_urls"][-1] == "https://example.com/200"
    assert "https://example.com/0" not in saved["posted_urls"]


# ===== 13. evaluate_article — コードブロック付きJSON出力を正しくパースできる =====
def test_evaluate_article_parses_fenced_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    article = {"title": "無印良品の収納ボックス", "summary": ""}
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text='```json\n["絵文字を減らす", "文字数を短くする"]\n```')]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = bot.evaluate_article(article, "サンプルツイート")

    assert result == ["絵文字を減らす", "文字数を短くする"]


# ===== 14. evaluate_article — 空リストを正しく返す =====
def test_evaluate_article_parses_empty_list(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    article = {"title": "無印良品の収納ボックス", "summary": ""}
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text='```json\n[]\n```')]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = bot.evaluate_article(article, "サンプルツイート")

    assert result == []


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
