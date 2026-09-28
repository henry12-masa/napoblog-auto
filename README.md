# napoblog 自動下書き（Claude Code版）

GASでやっていた「毎朝の新規下書き」と「13時・21時のリライト」を、Claude Code の定期実行で行うためのファイル一式です。
調査と執筆は Claude 自身が Web 検索しながら行うので、OpenAI API は不要です。イラストも Claude が描きます。

| ファイル | 役割 |
|---|---|
| CLAUDE.md | Claude が毎回読む執筆ルール（GAS v3 の指示を移植） |
| napo.py | 記事の品質チェック、WordPressブロック変換、下書き保存、保存後の検証、イラスト登録 |
| products.json | 確認済みの書籍リスト（Amazonリンクはここからだけ） |
| prompt-daily.md | 毎朝7時に実行する指示 |
| prompt-rewrite.md | 13時・21時に実行する指示 |

## 安全装置（GAS版から引き継ぎ）
- 常に下書きのみ。公開・削除はしない
- 新規は1日1本（`napoblog-daily-日付` のスラッグで重複を防ぐ。GASと同じ）
- 品質チェックに通らない記事は保存しない
- 保存後に、ブロック形式・表・Amazonリンクを読み直して検証
- 手動で編集した下書き・公開済み記事・v3形式済みの下書きはリライトしない
- 最近使った本は避けて選ぶ

## セットアップ
1. **キーの再発行**: OpenAIのAPIキーは削除（もう不要）。WordPressのアプリケーションパスワードは取り消して新しく発行。
2. **GASを止める**: Apps Script の「トリガー」から `runBlogAgent`・`generateArticleAndPostToWordPress`・`napoRewriteOldDrafts` を削除。コード中のキーとパスワードも消しておく。
3. **GitHub**: claude.ai の設定 → コネクタで GitHub を連携し、非公開リポジトリ（例: `napoblog-auto`）を作ってこのフォルダの中身をアップロード。
4. **Claude Code の環境設定**（claude.ai/code の環境設定）
   - 環境変数: `WP_USER=WordPressのログイン名`、`WP_APP_PASSWORD=新しいアプリケーションパスワード`
   - ネットワーク: 許可ドメインに `napoblog.com` を追加（パッケージ取得用の既定の許可はそのまま）
5. リポジトリ名を Claude に伝えると、7時・13時・21時の定期実行を登録し、1回テスト実行する。

## 手動で動かすとき
```
python3 napo.py selftest            # オフラインテスト
python3 napo.py status              # 接続確認と今日の状態
python3 napo.py rewrite-candidates  # リライト候補
```
