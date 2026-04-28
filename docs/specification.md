# PAI-Chatbot 仕様書

**バージョン:** 1.0  
**作成日:** 2026-04-28  
**リポジトリ:** soso2e/pai-chatbot

---

## 目次

1. [コンセプト](#1-コンセプト)
2. [システム全体構成](#2-システム全体構成)
3. [ディレクトリ構成](#3-ディレクトリ構成)
4. [設定ファイル](#4-設定ファイル)
5. [コア処理](#5-コア処理)
6. [メモリ設計（SQLite）](#6-メモリ設計sqlite)
7. [DB管理（DB Registry）](#7-db管理db-registry)
8. [インターフェース仕様](#8-インターフェース仕様)
9. [環境変数](#9-環境変数)
10. [依存ライブラリ](#10-依存ライブラリ)
11. [実装状況](#11-実装状況)
12. [今後の拡張予定](#12-今後の拡張予定)

---

## 1. コンセプト

LLM（デフォルト: Ollama ローカルモデル）を核とし、**Discord / Slack / HTTP API** の複数インターフェースから同一のモデル・DBを参照して会話できるチャットボット。

- インターフェース層とコア処理層を分離し、Discord依存を排除
- **マルチDB構成**により、DB単位で「人格・専門性・記憶」を分離できる
- DBを切り替えることで、同一のLLMが複数の用途・文脈に対応する

---

## 2. システム全体構成

```
┌────────────────────────────────────────────────────────────┐
│                      Interface Layer                       │
│                                                            │
│   Discord Bot        Slack Bot           HTTP API          │
│   (discord.py)       (slack-bolt         (FastAPI)         │
│                       Socket Mode)                         │
│       │                   │                  │             │
└───────┼───────────────────┼──────────────────┼────────────┘
        │                   │                  │
        ▼                   ▼                  ▼
┌──────────────────────────────────────────────────────┐
│                      Core Layer                      │
│                                                      │
│  Chat Controller（共通エントリポイント）              │
│       │                                              │
│  Context Builder                                     │
│  ├─ Memory Manager（SQLite + JSONフォールバック）     │
│  └─ RAG Manager（未実装）                            │
│       │                                              │
│  LLM Client（設定で切り替え可能）                    │
│       │                                              │
│  DB Registry（Discordサーバー↔DBの紐付け管理）       │
└──────────────────────────────────────────────────────┘
        │
┌───────▼──────────────────────────────────────────────┐
│                    Storage Layer                     │
│                                                      │
│  databases/{db_name}/                                │
│  ├─ memory.sqlite    （会話履歴・長期記憶）           │
│  └─ config.json      （人格・ツール設定）             │
│                                                      │
│  config/                                             │
│  ├─ llm.json         （LLM接続設定）                 │
│  ├─ app.json         （アプリ全体設定）               │
│  └─ discord_state.json（Discordサーバー状態）         │
└──────────────────────────────────────────────────────┘
```

---

## 3. ディレクトリ構成

```
PAI-chatbot/
├── main.py                      # エントリポイント（全インターフェース同時起動）
├── config/
│   ├── llm.json                 # LLM接続設定
│   ├── app.json                 # アプリ全体設定
│   └── discord_state.json       # Discordサーバー↔DBの紐付け（自動生成）
├── interfaces/
│   ├── discord_bot.py           # Discord Botハンドラ
│   ├── slack_bot.py             # Slack Bot（Socket Mode）
│   └── http_api.py              # FastAPI HTTPサーバー
├── core/
│   ├── chat_controller.py       # 共通チャット処理
│   ├── context_builder.py       # プロンプト組み立て
│   ├── memory_manager.py        # SQLiteメモリ操作
│   ├── db_registry.py           # DBとDiscordサーバーの紐付け管理
│   └── llm_client.py            # LLM APIクライアント
├── databases/
│   └── general/
│       ├── memory.sqlite        # 会話履歴・長期記憶
│       └── config.json          # DB設定（人格・ポリシー）
├── docs/
│   └── specification.md         # 本ドキュメント
├── requirements.txt
└── .env                         # トークン類（git管理外）
```

---

## 4. 設定ファイル

### 4-1. LLM設定（`config/llm.json`）

LLMのエンドポイント・モデルを一元管理。コードを変えずに差し替え可能。

```json
{
  "provider": "ollama",
  "base_url": "http://10.192.98.115:11434",
  "model": "qwen3:8b",
  "api_key": "",
  "timeout": 60,
  "options": {
    "temperature": 0.3,
    "num_ctx": 2048
  }
}
```

**対応プロバイダー**

| provider値 | 接続先 | エンドポイント |
|---|---|---|
| `ollama` | Ollama（ローカルLLM） | `{base_url}/api/chat` |
| `openai` | OpenAI API / 互換API | `{base_url}/v1/chat/completions` |

### 4-2. アプリ設定（`config/app.json`）

```json
{
  "default_db": "general",
  "discord": {
    "enabled": true,
    "prefix": "!",
    "mention_reply": true
  },
  "http": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 8000
  },
  "slack": {
    "enabled": true
  }
}
```

- `discord.guild_id` または `discord.dev_guild_id` を設定すると、スラッシュコマンドをそのGuildに即時同期する（未設定時はグローバル同期）

### 4-3. DB設定（`databases/{name}/config.json`）

```json
{
  "name": "general",
  "system_prompt": "You are a helpful assistant. Use saved memories when they are relevant and avoid inventing facts.",
  "style": "friendly",
  "memory_policy": {
    "auto_save": true,
    "max_context_messages": 20
  },
  "allowed_tools": ["save_memory"]
}
```

---

## 5. コア処理

### 5-1. 処理フロー

```
1. Interface層がメッセージを受信
2. chat_controller.process(message, session_id, db_name) を呼ぶ
3. Context Builder がプロンプトを組み立て
   a. DBのconfig.json から system_prompt 取得
   b. SQLiteから過去会話履歴を取得（直近 max_context_messages 件）
   c. 長期記憶からキーワード検索で関連メモリを取得（上位5件）
4. LLM Client にプロンプトを送信
5. 応答を受け取り、SQLiteに会話（user / assistant）を保存
6. 応答テキストをInterface層に返す
```

### 5-2. プロンプト構成

```
[system] system_prompt（DB config.json から）
[system] Relevant long-term memories: ... （関連メモリがある場合のみ）
[user / assistant] 過去会話履歴（直近N件）
[user] 今回の入力
```

### 5-3. メモリキャプチャ処理

「覚えておいて」などのトリガーフレーズを含むメッセージ、または `/memory capture` コマンドで発動。

```
1. チャンネルの直近メッセージ（最大100件）を取得
2. ルールベース抽出（自己紹介パターン等）で候補を生成
3. LLM に抽出させた候補と合算（上限5件）
4. 既存の長期記憶と重複チェック（正規化後の小文字比較）
5. 新規のみ memory_entries テーブルに保存
```

**メモリトリガーフレーズ（Discord）:**

| フレーズ | 言語 |
|---|---|
| `覚えておいて` | 日本語 |
| `覚えといて` | 日本語 |
| `remember this` | 英語 |
| `remember that` | 英語 |
| `save this to memory` | 英語 |

---

## 6. メモリ設計（SQLite）

SQLiteをメインストレージとし、SQLiteが利用不可の場合はJSONファイル（`memory.json`）にフォールバックする。

### テーブル: `messages`（会話履歴）

```sql
CREATE TABLE messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    role       TEXT    NOT NULL,  -- 'user' or 'assistant'
    content    TEXT    NOT NULL,
    timestamp  DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_session ON messages(session_id);
```

### テーブル: `memory_entries`（長期記憶）

```sql
CREATE TABLE memory_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT    NOT NULL,
    author_id  TEXT,
    source     TEXT    NOT NULL DEFAULT 'manual',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_memory_created_at ON memory_entries(created_at DESC);
```

**source の値:**

| 値 | 説明 |
|---|---|
| `manual` | デフォルト（コードから直接） |
| `discord_manual` | `/memory save` コマンド |
| `discord_auto` | メンション時の自動キャプチャ |
| `discord_manual_capture` | `/memory capture` コマンド |
| `discord_capture` | Discordチャンネル履歴からのキャプチャ |

### セッションID規則

| インターフェース | session_id |
|---|---|
| Discord | `discord-{channel_id}` |
| Slack | `slack-{channel_id}` |
| HTTP | リクエストで任意に指定 |

### 関連メモリ検索

ベクトル検索は未実装。クエリをスペース分割してキーワードマッチ（2文字以上のトークン）し、ヒット数でスコアリングする。

---

## 7. DB管理（DB Registry）

DiscordサーバーとDBの紐付けを `config/discord_state.json` で管理。

### データ構造

```json
{
  "guilds": {
    "{guild_id}": {
      "db_name": "general",
      "bound_at": "2026-04-28T00:00:00+00:00"
    }
  },
  "db_credentials": {
    "{db_name}": {
      "salt": "...",
      "password_hash": "...",
      "created_by_guild_id": "{guild_id}",
      "created_at": "2026-04-28T00:00:00+00:00"
    }
  }
}
```

- パスワードは `SHA-256(salt:password)` でハッシュ化して保存
- DBの作成・切り替えには `サーバーの管理` 権限が必要
- DB名は英数字・`_`・`-` のみ、3〜32文字

---

## 8. インターフェース仕様

### 8-1. Discord Bot

**起動:** `bot.run(DISCORD_TOKEN)` を別スレッドで起動

**トリガー・コマンド一覧:**

| 種別 | コマンド / 条件 | 動作 | 権限 |
|---|---|---|---|
| イベント | Botへのメンション | 会話処理（メモリトリガーも判定） | 全員 |
| プレフィックスコマンド | `!chat <メッセージ>` | 会話処理 | 全員 |
| プレフィックスコマンド | `!status` | 現在のDB・LLM設定を表示 | 全員 |
| スラッシュコマンド | `/chat <text>` | 会話処理 | 全員 |
| スラッシュコマンド | `/status` | 現在のDB・LLM設定を表示（ephemeral） | 全員 |
| スラッシュコマンド | `/db list` | 利用可能なDB一覧（現在使用中を `*` で表示） | 全員 |
| スラッシュコマンド | `/db current` | このサーバーが使用中のDB名 | 全員 |
| スラッシュコマンド | `/db create <db_name> <password>` | 新規DB作成 & このサーバーに紐付け | サーバーの管理 |
| スラッシュコマンド | `/db use <db_name> <password>` | 既存DBへ切り替え | サーバーの管理 |
| スラッシュコマンド | `/memory save <text>` | 長期記憶を手動保存 | 全員 |
| スラッシュコマンド | `/memory list` | 最近の長期記憶5件を表示 | 全員 |
| スラッシュコマンド | `/memory capture [limit]` | チャンネル履歴からLLMで記憶を抽出・保存 | 全員 |
| スラッシュコマンド | `/memory clear` | 現在チャンネルの会話履歴を削除 | 全員 |

**DBの決定ロジック:**
1. `discord_state.json` にサーバーの紐付けがあればそのDB
2. なければ `config/app.json` の `default_db`（デフォルト: `general`）

### 8-2. Slack Bot

**起動:** `slack-bolt` の Socket Mode で `AsyncSocketModeHandler` を使用

**トリガー・コマンド一覧:**

| 種別 | コマンド / 条件 | 動作 |
|---|---|---|
| イベント | Botへのメンション | 会話処理（スレッドに返信） |
| スラッシュコマンド | `/pai <メッセージ>` | 会話処理 |
| スラッシュコマンド | `/pai-db [db名 \| list]` | DB一覧表示 or チャンネルのDB切り替え |
| スラッシュコマンド | `/pai-status` | 現在のDB・LLM設定表示 |

- DB切り替えはチャンネル単位でメモリ上に保持（再起動でリセット）
- セッションID: `slack-{channel_id}`

### 8-3. HTTP API

**起動:** `uvicorn` を別スレッドで起動（デフォルト: `0.0.0.0:8000`）

**認証:** `X-API-Key` ヘッダー（環境変数 `HTTP_API_KEY` と照合）  
　　　　`HTTP_API_KEY` 未設定の場合は認証スキップ（開発用）

**エンドポイント一覧:**

| メソッド | パス | 説明 |
|---|---|---|
| `POST` | `/chat` | 会話処理 |
| `GET` | `/db/list` | 利用可能なDB一覧 |
| `POST` | `/db/switch` | セッションのDB切り替え（レスポンスのみ、状態は保持しない） |
| `GET` | `/status` | LLM設定・利用可能DB一覧 |

**`POST /chat` リクエスト:**
```json
{
  "message": "こんにちは",
  "user_id": "anonymous",
  "session_id": "slack-channel-C999",
  "db_name": "general"
}
```

**`POST /chat` レスポンス:**
```json
{
  "reply": "こんにちは！何かお手伝いできますか？",
  "db_used": "general",
  "session_id": "slack-channel-C999"
}
```

---

## 9. 環境変数

`.env` ファイル（`python-dotenv` で読み込み）またはシステム環境変数で設定。

| 変数名 | 必須 | 説明 |
|---|---|---|
| `DISCORD_TOKEN` | Discord利用時 | Discord Bot Token |
| `SLACK_BOT_TOKEN` | Slack利用時 | Slack Bot User OAuth Token（`xoxb-...`） |
| `SLACK_APP_TOKEN` | Slack利用時 | Slack App-Level Token（`xapp-...`、Socket Mode用） |
| `HTTP_API_KEY` | 任意 | HTTP APIのAPIキー（未設定時は認証スキップ） |

LLMのAPIキーは `config/llm.json` の `api_key` フィールドで管理。

---

## 10. 依存ライブラリ

| ライブラリ | バージョン | 用途 |
|---|---|---|
| `discord.py` | `>=2.3.0` | Discord Bot |
| `slack-bolt` | `>=1.18.0` | Slack Bot（Socket Mode） |
| `fastapi` | `>=0.111.0` | HTTP API |
| `uvicorn[standard]` | `>=0.30.0` | ASGIサーバー |
| `httpx` | `>=0.27.0` | LLM API への非同期HTTPリクエスト |
| `python-dotenv` | `>=1.0.0` | `.env` ファイル読み込み |

---

## 11. 実装状況

| 機能 | 状態 | 備考 |
|---|---|---|
| Discord Bot（メンション・プレフィックスコマンド） | ✅ 実装済み | |
| Discord Bot（スラッシュコマンド） | ✅ 実装済み | `/db`・`/memory` グループコマンド含む |
| Slack Bot（Socket Mode） | ✅ 実装済み | |
| HTTP API（FastAPI） | ✅ 実装済み | |
| Chat Controller | ✅ 実装済み | |
| Context Builder | ✅ 実装済み | |
| Memory Manager（SQLite） | ✅ 実装済み | JSONフォールバック付き |
| LLM Client（Ollama） | ✅ 実装済み | |
| LLM Client（OpenAI互換） | ✅ 実装済み | |
| DB Registry（Discordサーバー↔DB紐付け・パスワード管理） | ✅ 実装済み | |
| 長期記憶の自動キャプチャ（LLM抽出） | ✅ 実装済み | |
| 長期記憶のキーワード検索 | ✅ 実装済み | ベクトル検索ではなくキーワードマッチ |
| RAG（ChromaDB によるベクトル検索） | ❌ 未実装 | |
| `!history save` | ❌ 未実装 | |

---

## 12. 今後の拡張予定

- **ChromaDB による RAG 検索:** ベクトルDBを用いた意味検索で関連メモリの精度向上
- **チャンネル履歴の自動ベクトル化:** `!history save` コマンドの実装
- **DB自動切り替え:** 入力意図をLLMで分類し、最適なDBを自動選択
- **Notion等の外部ツール連携:** ドキュメント読み込み・書き込み
- **Docker対応:** 環境分離・デプロイの簡略化
- **Discordチャンネル↔DB自動マッピング:** `#maya` → `maya_pipeline` のような規則ベースの自動切り替え
