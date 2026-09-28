napoblog の既存の下書きを1本リライトしてください。

リポジトリの CLAUDE.md のルールに従い、次の順で進めます。
1. 準備（selftest と status）。
2. `python3 napo.py rewrite-candidates` で候補を確認。候補がなければ「対象なし」と報告して終了。
3. 先頭の候補のタイトルをテーマにする。WebSearch と WebFetch で、公式情報で確認できる範囲にテーマを絞って調べる。
   確認できる一次情報が見つからない場合は、次の候補に進む（最大3候補まで）。
4. article.json を書き、`python3 napo.py validate article.json` が通るまで直す。
5. イラストを2枚（1章目と真ん中の章）SVGで描いて illustrate で登録。
6. `python3 napo.py rewrite 投稿ID article.json` で上書き保存（下書きのまま）。
7. 最後に、元タイトル→新タイトル・文字数・編集URLを短く報告。失敗した場合は理由を報告。

公開・削除は絶対にしないこと。手動で編集された下書きはツールが自動で対象外にするので、無理に書き換えないこと。
