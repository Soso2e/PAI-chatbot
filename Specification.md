# PAI-Chatbot 仕様書

## 1. コンセプト

Ollama（ローカルLLM）を核とし、**Discord / Slack / HTTP API** の複数インターフェースから同一のモデル・DBを参照して会話できるチャットボット。
インターフェース層とコア処理層を分離することで、Discord依存を排除し、将来的な拡張に対応する。

---

## 2. システム全体構成

```
┌──────────────────────────────────────────────────────────┐
│                     Interface Layer                      │
│                                                          │
│  Discord Bot     Slack Bot (Socket Mode)   HTTP API      │
│  (discord.py)    (slack-bolt)              (FastAPI)     │
│       │                │                      │          │
└───────┼────────────────┼──────────────────────┼──────────┘
        │                │                      │
        ▼                ▼                      ▼
┌─────────────────────────────────────────────────┐
│                  Core Layer                     │
│                                                 │
│  Chat Controller (共通エントリポイント)           │
│       │                                         │
│  Context Builder                                │
│  ├─ Memory Manager (SQLite)                     │
│  └─ RAG Manager    (ChromaDB ※後日)             │
│       │                                         │
│  LLM Client (設定可能・差し替え可能)              │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│               Storage Layer                     │
│                                                 │
│  databases/                                     │
│  └─ {db_name}/                                  │
│      ├─ memory.sqlite                           │
│      ├─ vector_db/  (ChromaDB ※後日)            │
│      └─ config.json (人格・ツール設定)           │
│                                                 │
│  config/                                        │
│  └─ llm.json       (LLM接続設定)                │
└─────────────────────────────────────────────────┘
```

---

## 3. ディレクトリ構成

```
PAI-chatbot/
├── main.py                    # エントリポイント（Discord / HTTP 両立起動）
├── config/
│   ├── llm.json               # LLM接続設定（後述）
│   └── app.json               # アプリ全体設定
├── interfaces/
│   ├── discord_bot.py         # Discord Botハンドラ
│   ├── slack_bot.py           # Slack Bot (Socket Mode)
│   └── http_api.py            # FastAPI HTTPサーバー（外部向け・APIキー認証）
├── core/
│   ├── chat_controller.py     # 共通チャット処理（両IFから呼ぶ）
│   ├── context_builder.py     # プロンプト組み立て
│   ├── memory_manager.py      # SQLiteメモリ操作
│   ├── rag_manager.py         # RAG検索（後日実装）
│   └── llm_client.py          # LLM APIクライアント（差し替え可能）
├── databases/
│   └── general/
│       ├── memory.sqlite
│       └── config.json
├── requirements.txt
└── .env                       # トークン類（git管理外）
```

---

## 4. LLM設定（`config/llm.json`）

LLMのエンドポイント・モデルはここで一元管理。コードを変えずに差し替え可能。

```json
{
  "provider": "ollama",
  "base_url": "http://localhost:11434",
  "model": "llama3",
  "api_key": "",
  "timeout": 60,
  "options": {
    "temperature": 0.7,
    "num_ctx": 4096
  }
}
```

### 対応プロバイダー

| provider値 | 接続先 | 備考 |
|---|---|---|
| `ollama` | Ollama (`/api/chat`) | **メイン。ローカルLLM推奨** |
| `openai` | OpenAI API / 互換API | `base_url`で向き先変更可 |

---

## 5. DB（人格）設定（`databases/{name}/config.json`）

```json
{
  "name": "general",
  "system_prompt": "あなたは親切なアシスタントです。",
  "style": "friendly",
  "memory_policy": {
    "auto_save": true,
    "importance_threshold": 0.7,
    "max_context_messages": 20
  },
  "allowed_tools": []
}
```

---

## 6. アプリ設定（`config/app.json`）

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
    "port": 8000,
    "api_key": "your_api_key_here"
  },
  "slack": {
    "enabled": true
  }
}
```

---

## 7. Discord インターフェース仕様

### トリガー条件

| 条件 | 動作 |
|---|---|
| Bot へのメンション | 会話処理 |
| `!chat <メッセージ>` | 会話処理 |
| `!db <db名>` | 使用DBを切り替え（チャンネル単位） |
| `!db list` | 利用可能なDB一覧を表示 |
| `!memory clear` | 現在DBのメモリをクリア |
| `!history save [件数]` | チャンネル履歴をDBに保存（後日実装） |
| `!status` | 現在のDB・LLM設定を表示 |

### チャンネルとDBの対応

- デフォルトは `config/app.json` の `default_db`
- `!db <名前>` でチャンネルごとに切り替え（メモリ上で保持）
- 将来的に `#maya` → `maya_pipeline` のような自動マッピングも対応予定

---

## 8. HTTP API仕様

Discord以外（Slack等）からの入口。同一の `ChatController` を使う。

### エンドポイント一覧

#### `POST /chat`

汎用チャットエンドポイント。SlackはSlack側のアウトゴーイングWebhookやEvent APIから叩く。

**リクエスト:**
```json
{
  "message": "こんにちは",
  "user_id": "U12345",
  "session_id": "slack-channel-C999",
  "db_name": "general"
}
```

**レスポンス:**
```json
{
  "reply": "こんにちは！何かお手伝いできますか？",
  "db_used": "general",
  "session_id": "slack-channel-C999"
}
```

#### `GET /db/list`

利用可能なDB一覧を返す。

#### `POST /db/switch`

セッションのDB切り替え。

**リクエスト:**
```json
{
  "session_id": "slack-channel-C999",
  "db_name": "maya_pipeline"
}
```

#### `GET /status`

LLM接続状態・現在設定を返す。

### 認証

外部公開を前提とするため **APIキー認証は必須**。
リクエストヘッダー `X-API-Key` で検証。`.env` の `HTTP_API_KEY` を使用。

---

## 9. Slack インターフェース仕様

### 接続方式

`slack-bolt` ライブラリの **Socket Mode** で常駐起動。
WebSocketでSlackサーバーと接続するため、外部からのinbound通信は不要（ファイアウォール不要）。

### 必要トークン（`.env`）

```env
SLACK_BOT_TOKEN=xoxb-...       # Bot User OAuth Token
SLACK_APP_TOKEN=xapp-...       # App-Level Token（Socket Mode用）
```

### トリガー条件

| 条件 | 動作 |
|---|---|
| Botへのメンション | 会話処理 |
| `/pai <メッセージ>` | スラッシュコマンドで会話 |
| `/pai-db <db名>` | チャンネルのDB切り替え |
| `/pai-status` | 現在のDB・LLM設定表示 |

### セッションID

`slack-{channel_id}` 形式。チャンネルごとに会話履歴を分離。

---

## 10. コア処理フロー

```
1. Interface層がメッセージを受信
2. ChatController.process(message, user_id, session_id, db_name) を呼ぶ
3. Context Builder が以下を組み立て:
   - DBのconfig.jsonからsystem_prompt取得
   - SQLiteから過去会話メモリ取得（直近N件）
   - （後日）ChromaDBからRAG検索結果追加
4. LLM Client にプロンプトを送信
5. 応答を受け取り、SQLiteに会話を保存
6. 応答テキストをInterface層に返す
```

---

## 11. メモリ設計（SQLite）

### テーブル: `messages`

```sql
CREATE TABLE messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,  -- 'user' or 'assistant'
    content     TEXT NOT NULL,
    timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### セッションID規則

| インターフェース | session_id |
|---|---|
| Discord | `discord-{channel_id}` |
| HTTP | リクエストで指定した `session_id`（Slack: `slack-{channel_id}` 推奨）|

---

## 12. 環境変数（`.env`）

```env
DISCORD_TOKEN=your_discord_bot_token
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
HTTP_API_KEY=your_api_key_here
```

LLMのAPIキーは `config/llm.json` の `api_key` で管理。

---

## 13. MVP 実装順序

- [ ] 1. プロジェクト基盤（ディレクトリ・依存関係・設定ファイル雛形）
- [ ] 2. LLM Client（Ollama接続）
- [ ] 3. Memory Manager（SQLite 読み書き）
- [ ] 4. Context Builder（プロンプト組み立て）
- [ ] 5. Chat Controller（コア処理統合）
- [ ] 6. Discord Bot（メンション・コマンド対応）
- [ ] 7. HTTP API（FastAPI・APIキー認証）
- [ ] 8. Slack Bot（Socket Mode）
- [ ] 9. 動作確認・整合テスト

---

## 14. 将来的な拡張（MVP後）

- ChromaDB による RAG 検索
- `!history save` によるチャンネル履歴の自動ベクトル化
- DB自動切り替え（入力意図分類）
- Notionなど外部ツール連携
- Dockerによる環境分離

---

## 15. 決定済み事項

| # | 項目 | 決定内容 |
|---|---|---|
| 1 | LLM | Ollama（ローカル）。OpenAI互換も設定で切替可 |
| 2 | Slack連携方式 | slack-bolt Socket Mode（Slack Bot として常駐） |
| 3 | HTTP認証 | APIキー必須（`X-API-Key` ヘッダー） |
| 4 | DBの保存場所 | サーバーローカル（`databases/` 以下）|
| 5 | MVP段階のDB数 | `general` 1つから開始 |
