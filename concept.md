# 🧠 マルチDB対応 AIチャットボット設計

## 🎯 コンセプト
- LLM（Ollama）は1つ
- データベースを切り替えて挙動を変える
- = 「人格 / 専門性 / 用途」をDB単位で分離

---

## 🏗️ 全体構成

```txt
User (Discord / CLI)
    ↓
Chat Controller
    ↓
+-----------------------------+
| Context Builder             |
| - DB選択                    |
| - メモリ取得                |
| - RAG検索                   |
+-----------------------------+
    ↓
Ollama (LLM)
    ↓
Response + Action(JSON)
    ↓
DB更新 / 応答返却
```

---

## 🗂️ DB構成（マルチDB）

### DBディレクトリ構成

```txt
databases/
  general/
    memory.sqlite
    vector_db/
    config.json

  maya_pipeline/
    memory.sqlite
    vector_db/
    config.json

  game_dev/
    memory.sqlite
    vector_db/
    config.json
```

---

### config.json例

```json
{
  "system_prompt": "あなたはMayaのテクニカルアーティストです。",
  "allowed_tools": ["save_memory", "search_docs"],
  "style": "technical",
  "memory_policy": {
    "auto_save": true,
    "importance_threshold": 0.7
  }
}
```

---

## 🔀 DB切り替え方法

### コマンド

```txt
/db maya_pipeline
/db game_dev
```

---

### Discordチャンネル

```txt
#maya → maya_pipeline DB
#game → game_dev DB
```

---

### 自動判定（上級）

```txt
入力文から意図分類
↓
適切なDBを選択
```

---

## 🔄 リクエスト処理フロー

```txt
1. ユーザー入力受信
2. 使用DBを決定
3. DBから情報取得
   - 過去メモリ
   - RAG検索
4. プロンプト生成
5. Ollamaに投げる
6. 応答生成
7. 必要ならDB更新
8. ユーザーへ返答
```

---

## 🧩 プロンプト構成

```txt
[System Prompt]
[Memory Context]
[Knowledge Context]
[User Input]
```

---

## 🧠 メモリ設計

### 保存ルール
- 明示：「覚えて」→ 保存
- 自動：重要度スコアで判断

### 例

```json
{
  "content": "ユーザーはMayaでツール開発をしている",
  "importance": 0.85
}
```

---

## ⚙️ ツール実行

```txt
LLM
↓
JSONで行動提案
↓
Python側で検証
↓
DB更新
```

### 例

```json
{
  "action": "save_memory",
  "content": "ユーザーはTA志望",
  "reason": "将来の回答精度向上"
}
```

---

## 🚀 拡張性

- DBごとに人格を変える
- DBごとに使用ツール制限
- Notion / Maya / UE5 と連携
- プロジェクト単位でDB作成

---

## 🧩 未来像

```txt
1人 = 複数AIを使うのではなく
1つのAIが文脈によって人格を切り替える

→ DBが「人格の実体」
```

---

## ✅ MVP優先順位

1. DB切り替え機構
2. SQLiteメモリ
3. Chroma RAG
4. config.json読み込み
5. Discord連携