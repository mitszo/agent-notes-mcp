# agent-notes-mcp

`agent-notes-mcp` は、Codex などの MCP クライアントから個人用 Markdown ノートを安全に検索・編集するための、ローカル stdio MCP サーバーです。

ファイル名、YAML frontmatter、Markdown 本文の通常の全文検索を使います。ベクトル DB や埋め込みモデルは必要ありません。

## 主な機能

- 設定した root 内の Markdown ノートの検索・一覧・読込み
- 書込み可能な root 内でのノート作成、追記、置換、移動
- 置換・移動時の SHA-256 による競合検出
- 明示的にマークした Markdown ブロックの安全な一括更新
- 任意の一括更新、更新日時の記録、activity log
- 削除操作およびネットワーク待受けは提供しない

アクセス境界と更新時の保証は [安全性モデル](docs/security-model.md) を参照してください。

## 必要条件

- Python 3.14 以上
- [uv](https://docs.astral.sh/uv/)

## インストール

ローカルの checkout から導入する場合:

```shell
uv tool install .
```

GitHub の main ブランチから導入する場合:

```shell
uv tool install git+https://github.com/mitszo/agent-notes-mcp.git
```

特定のコミットやタグを使う場合は、URL の末尾へ `@<ref>` を追加します。

## 設定

設定例をコピーし、各パスを自分が管理するディレクトリに置き換えます。

```shell
mkdir -p ~/.config/agent-notes-mcp
cp config.example.toml ~/.config/agent-notes-mcp/config.toml
```

`read_roots` は検索・読込みを許可します。`write_roots` は検索・読込み・編集を許可し、読込み可能な root の内側に置く必要があります。最初は inbox や memos のような狭い書込み root から始めてください。

## Codex への登録

導入済みのコマンドを stdio MCP サーバーとして登録します。

```toml
[mcp_servers.agent_notes]
command = "agent_notes"
```

ソース checkout を使う場合は、設定ファイルを明示します。

```toml
[mcp_servers.agent_notes]
command = "uv"
args = ["run", "agent_notes", "--config", "/absolute/path/to/config.toml"]
```

## 最初の 5 分

Codex で新しいスレッドを開き、まず次のように依頼してください。

```text
agent_notes で設定済みの root を確認し、ノートを検索できるか試してください。
```

Codex は `list_roots`、`search_notes`、`read_note` などの MCP ツールを使えます。書込みを試すときは、設定で許可した狭い `write_roots` 内に新規のテスト用 Markdown ファイルを作るよう依頼してください。

複数の開発リポジトリでノートを引継ぎに使う場合は、[Codex のプロジェクトコンテキスト例](docs/recipes/project-context-for-codex.md) と、コピーして調整できる[スキル例](examples/project-notes-context/)を参照してください。この運用は任意であり、ノートの配置や形式をサーバーが強制することはありません。

## 安全な更新

既存ノートを置換・移動する前に読込み、返された `sha256` を `expected_sha256` として指定します。その間に内容が変わった場合、サーバーは上書きせず更新を拒否します。

ノート内で更新を許可する範囲を限定するには、管理ブロックで囲みます。

```md
<!-- agent-notes:managed next-actions -->
## 次の行動

- 下書きを確認する
<!-- /agent-notes:managed next-actions -->
```

`update_managed_blocks` は、構造が検証できた管理ブロックだけを更新します。`batch_update_notes` は、対象すべての更新を検証してから書込みを開始します。

## 開発

```shell
uv sync --all-groups
uv run pytest
```

## ライセンス

[MIT](LICENSE)

## 開発について

このプロジェクトは Codex を開発支援に利用しています。公開前の内容確認と判断はメンテナーが行っています。
