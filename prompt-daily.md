napoblog の今日の新規下書きを1本作ってください。

リポジトリの CLAUDE.md のルールに従い、次の順で進めます。
1. 準備（selftest と status）。今日の下書きが既にあれば何もせず終了。
2. WebSearch で直近のニュースからテーマを1つ選ぶ（最近のタイトルとかぶらないもの）。
3. 一次情報を最低2ページ WebFetch で読んで確認する。
4. article.json を書き、`python3 napo.py validate article.json` が通るまで直す。
5. イラストを2枚（1章目と真ん中の章）SVGで描いて illustrate で登録。
6. `python3 napo.py post-daily article.json` で下書き保存。
7. 最後に、タイトル・文字数・選んだ本・編集URLを短く報告。失敗した場合は理由を報告（保存はしない）。

公開・削除は絶対にしないこと。
