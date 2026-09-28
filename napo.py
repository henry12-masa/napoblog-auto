#!/usr/bin/env python3
"""napoblog 自動下書きツール（Claude Code 用）

Claude が調査・執筆した記事JSONを検証し、WordPressのブロック形式に変換して
下書き保存し、保存後に中身を検証する。GAS版 v3 の品質チェックを移植したもの。

必要な環境変数:
  WP_USER          WordPressのユーザー名
  WP_APP_PASSWORD  WordPressのアプリケーションパスワード（コードに書かない）
任意:
  WP_SITE          既定 https://napoblog.com
  AMAZON_TAG       既定 hitomi0303-22

コマンド:
  python3 napo.py status                 今日の下書き有無・最近のタイトル・商品候補を表示
  python3 napo.py validate article.json  記事JSONを品質チェック（WordPressに触れない）
  python3 napo.py illustrate article.json SECTION_INDEX image.svg
                                         SVGを画像化してメディアに保存し、記事JSONに登録
  python3 napo.py post-daily article.json   今日の下書きを新規作成（1日1本まで）
  python3 napo.py rewrite-candidates     リライト可能な既存下書きを一覧
  python3 napo.py rewrite POST_ID article.json  既存下書きを書き換え
  python3 napo.py preview article.json out.html レンダリング結果をファイルに出力
  python3 napo.py selftest               オフラインの自己テスト
"""
import base64
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.environ.get("WP_SITE", "https://napoblog.com").rstrip("/")
TAG = os.environ.get("AMAZON_TAG", "hitomi0303-22")
JST = dt.timezone(dt.timedelta(hours=9))
MIN_CHARS, MAX_CHARS = 4000, 7500
HAND_EDIT_GRACE = dt.timedelta(minutes=10)
AD_MARK = "の詳細を見る（広告リンク）</a>"

with open(os.path.join(HERE, "products.json"), encoding="utf-8") as f:
    PRODUCTS = json.load(f)
PRODUCT_BY_ID = {p["id"]: p for p in PRODUCTS}


class WpError(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


def die(msg):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(1)


def today():
    return dt.datetime.now(JST).strftime("%Y-%m-%d")


def daily_slug(day=None):
    return "napoblog-daily-" + (day or today())


# ---------------------------------------------------------------- WordPress
def _auth():
    user, pw = os.environ.get("WP_USER"), os.environ.get("WP_APP_PASSWORD")
    if not user or not pw:
        die("環境変数 WP_USER / WP_APP_PASSWORD が未設定です")
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def wp(path, method="GET", payload=None, raw=None, headers=None):
    """GETは3回まで再試行。POSTは二重作成を防ぐため1回だけ送る。"""
    url = f"{SITE}/wp-json/wp/v2/{path}"
    hdr = {"Authorization": _auth(), "User-Agent": "napoblog-claude/1.0"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        hdr["Content-Type"] = "application/json"
    if raw is not None:
        data = raw
    hdr.update(headers or {})
    tries = 3 if method == "GET" else 1
    last = None
    for attempt in range(tries):
        if attempt:
            time.sleep(3 * attempt)
        req = urllib.request.Request(url, data=data, headers=hdr, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            if method == "GET" and (e.code == 429 or e.code >= 500):
                last = WpError(f"WordPress HTTP {e.code}: {body}", e.code)
                continue
            raise WpError(f"WordPress HTTP {e.code}: {body}", e.code)
        except urllib.error.URLError as e:
            last = WpError(f"WordPress通信失敗: {e.reason}")
    raise last


# ---------------------------------------------------------------- products
ASIN_RE = re.compile(r"amazon\.co\.jp/dp/([A-Z0-9]{10})/")


def recent_product_ids(limit=4):
    posts = wp("posts?context=edit&status=draft&per_page=20&orderby=date&order=desc&_fields=id,content")
    by_asin = {p["asin"]: p["id"] for p in PRODUCTS}
    seen = []
    for p in posts:
        for asin in ASIN_RE.findall((p.get("content") or {}).get("raw", "")):
            pid = by_asin.get(asin)
            if pid and pid not in seen:
                seen.append(pid)
    return seen[:limit]


def product_candidates(recent):
    fresh = [p for p in PRODUCTS if p["id"] not in recent]
    return fresh if len(fresh) >= 3 else PRODUCTS


# ---------------------------------------------------------------- validation
def _strs(x):
    return isinstance(x, list) and all(isinstance(i, str) for i in x)


def body_text(a):
    parts = list(a["intro"])
    for s in a["sections"]:
        parts += s["paragraphs"] + s["steps"]
    parts += [q["answer"] for q in a["faq"]] + list(a["conclusion"])
    return "".join(parts)


def validate(a, allowed_products=None):
    """問題があれば例外。OKなら本文文字数を返す。"""
    need = ["title", "description", "intro", "sections", "comparison", "product", "faq", "conclusion", "sources"]
    miss = [k for k in need if k not in a]
    if miss:
        raise ValueError("項目不足: " + ",".join(miss))
    if not a["title"].strip() or not (60 <= len(a["description"]) <= 160):
        raise ValueError(f"タイトル空、またはdescriptionが60〜160字でない（{len(a['description'])}字）")
    if not _strs(a["intro"]) or len(a["intro"]) < 2:
        raise ValueError("introは2段落以上")
    if not (5 <= len(a["sections"]) <= 8):
        raise ValueError("章の数は5〜8")
    for i, s in enumerate(a["sections"]):
        for k in ("heading", "paragraphs", "steps", "sourceUrls"):
            if k not in s:
                raise ValueError(f"章{i}: {k}がない")
        if not _strs(s["paragraphs"]) or len(s["paragraphs"]) < 3:
            raise ValueError(f"章{i}: 段落は3つ以上")
        if not _strs(s["steps"]) or not _strs(s["sourceUrls"]):
            raise ValueError(f"章{i}: stepsとsourceUrlsは文字列配列")
    if len(a["faq"]) != 3:
        raise ValueError("FAQはちょうど3問")
    if not _strs(a["conclusion"]) or not a["conclusion"]:
        raise ValueError("conclusionが空")

    body = body_text(a)
    n = len(body)
    if n < MIN_CHARS:
        raise ValueError(f"本文不足: {n}字（最低{MIN_CHARS}）")
    if n > MAX_CHARS:
        raise ValueError(f"本文過多: {n}字（最大{MAX_CHARS}）")

    pr = a["product"]
    for k in ("productId", "reason", "suitableFor", "limitation"):
        if not isinstance(pr.get(k), str) or not pr[k].strip():
            raise ValueError("product." + k + " がない")
    if pr["productId"] not in PRODUCT_BY_ID:
        raise ValueError("確認済み商品リスト外: " + pr["productId"])
    if allowed_products is not None and pr["productId"] not in allowed_products:
        raise ValueError("最近使った商品です。別の商品を選ぶ: " + pr["productId"])
    ptext = "\n".join([pr["reason"], pr["suitableFor"], pr["limitation"]])

    texts = list(a["intro"]) + list(a["conclusion"])
    for s in a["sections"]:
        texts += s["paragraphs"] + s["steps"]
    texts += a["comparison"].get("headers", []) + [c for r in a["comparison"].get("rows", []) for c in r]
    if any(re.fullmatch(r"https?://\S+", t.strip()) for t in texts):
        raise ValueError("本文や表がURLだけになっている")
    both = body + "\n" + ptext
    if re.search(r"```|^#{1,6}\s|\*\*[^*]+\*\*|\[[^\]]+\]\(https?:|</?(?:p|h[1-6]|div|script|ul|ol|li|table)\b|example\.com|ここに.*リンク", both, re.M):
        raise ValueError("Markdown/HTML/仮リンクが混入")
    if re.search(r"(?:sourceUrls|paragraphs|suitableFor|amazonUrl|authorOrMaker|productId)\s*\"?\s*:", both):
        raise ValueError("本文にJSON項目名が混入")
    if re.search(r"メタディスクリプション|AISEO|検索上位を狙|文字数[:：]|H[23]見出しを", body):
        raise ValueError("本文にSEO指示が混入")
    heads = [s["heading"] for s in a["sections"]]
    if len(set(heads)) != len(heads):
        raise ValueError("章見出しの重複")
    paras = list(a["intro"]) + [p for s in a["sections"] for p in s["paragraphs"]] + [q["answer"] for q in a["faq"]] + list(a["conclusion"])
    norm = [re.sub(r"\s", "", p) for p in paras]
    if len(set(norm)) != len(norm):
        raise ValueError("段落の重複")
    cmp_ = a["comparison"]
    if not cmp_.get("heading") or not cmp_.get("headers") or len(cmp_.get("rows", [])) < 2 or any(len(r) != len(cmp_["headers"]) for r in cmp_["rows"]):
        raise ValueError("比較表が不正（見出し・2行以上・列数一致）")
    if re.search(r"[0-9０-９,，]+\s*円|在庫|星[0-9０-９]|★|評価は|レビュー[数件]|読んでみ|実際に読んだ|使ってみ|使った感想|実際に試した", ptext):
        raise ValueError("商品説明に未確認の価格・評価・体験")

    src_urls = []
    for s in a["sources"]:
        if not s.get("title") or not re.match(r"https://", s.get("url", "")):
            raise ValueError("sourcesはtitleとhttpsのurlが必要")
        if re.match(r"https://(?:[^/]*\.)?amazon\.", s["url"]):
            raise ValueError("Amazonは出典にしない")
        src_urls.append(s["url"])
    if not src_urls:
        raise ValueError("出典が1つもない")
    for i, s in enumerate(a["sections"]):
        if not s["sourceUrls"] or any(u not in src_urls for u in s["sourceUrls"]):
            raise ValueError(f"章{i}: sourceUrlsはsourcesに載せたURLから選ぶ")
    return n


# ---------------------------------------------------------------- rendering
def esc(t):
    return html.escape(str(t), quote=True)


def block(name, inner):
    close = name.split(" ")[0]
    return f"<!-- wp:{name} -->\n{inner}\n<!-- /wp:{close} -->\n\n"


def para(t):
    return block("paragraph", f"<p>{esc(t)}</p>")


def h2(t):
    return block("heading", f'<h2 class="wp-block-heading">{esc(t)}</h2>')


def image_block(img):
    attrs = '{"id":%d,"sizeSlug":"large","linkDestination":"none"}' % int(img["id"])
    return block("image " + attrs, f'<figure class="wp-block-image size-large"><img src="{esc(img["url"])}" alt="{esc(img["alt"])}" class="wp-image-{int(img["id"])}"/></figure>')


def render(a):
    title_of = {s["url"]: s["title"] for s in a["sources"]}
    out = "".join(para(t) for t in a["intro"])
    for s in a["sections"]:
        out += h2(s["heading"])
        if s.get("image"):
            out += image_block(s["image"])
        out += "".join(para(t) for t in s["paragraphs"])
        if s["steps"]:
            items = "".join(f"<!-- wp:list-item -->\n<li>{esc(t)}</li>\n<!-- /wp:list-item -->" for t in s["steps"])
            out += block('list {"ordered":true}', f'<ol class="wp-block-list">{items}</ol>')
        links = " ／ ".join(f'<a href="{esc(u)}">{esc(title_of.get(u, "一次情報"))}</a>' for u in s["sourceUrls"])
        out += block("paragraph", f"<p>参考：{links}</p>")
    c = a["comparison"]
    out += h2(c["heading"])
    cells = lambda r, tag: "".join(f"<{tag}>{esc(x)}</{tag}>" for x in r)
    rows = "".join(f"<tr>{cells(r, 'td')}</tr>" for r in c["rows"])
    out += block("table", f'<figure class="wp-block-table"><table class="has-fixed-layout"><thead><tr>{cells(c["headers"], "th")}</tr></thead><tbody>{rows}</tbody></table></figure>')
    p = PRODUCT_BY_ID[a["product"]["productId"]]
    out += h2("理解を深めるための一冊")
    out += block("paragraph", f'<p>{esc(p["name"] + "（" + p["authorOrMaker"] + "／" + p["publisher"] + "／" + p["format"] + "）")}。<a href="{esc(p["publisherUrl"])}">出版社・版元の書誌情報</a></p>')
    out += para("選んだ理由：" + a["product"]["reason"])
    out += para("向いている人：" + a["product"]["suitableFor"])
    out += para("購入前の注意：" + a["product"]["limitation"])
    out += block("paragraph", f'<p><a href="https://www.amazon.co.jp/dp/{p["asin"]}/?tag={TAG}" rel="sponsored nofollow">Amazonで「{esc(p["name"])}」{AD_MARK}</p>')
    out += h2("よくある質問")
    for q in a["faq"]:
        out += block('heading {"level":3}', f'<h3 class="wp-block-heading">{esc(q["question"])}</h3>') + para(q["answer"])
    out += h2("まとめ") + "".join(para(t) for t in a["conclusion"]) + h2("参考資料")
    for s in a["sources"]:
        out += block("paragraph", f'<p><a href="{esc(s["url"])}">{esc(s["title"])}</a></p>')
    return out


def verify_saved(saved, a):
    raw = saved["content"]["raw"]
    probs = []
    if saved["status"] != "draft":
        probs.append("status=" + saved["status"])
    if raw.count("<!-- wp:paragraph -->") < 20:
        probs.append("段落ブロック不足")
    if len(re.findall(r"<!-- wp:heading\b", raw)) < 8:
        probs.append("見出しブロック不足")
    if "<!-- wp:table -->" not in raw:
        probs.append("表ブロックなし")
    if re.search(r"<!-- wp:(?:html|freeform)\b|```|^#{1,6}\s", raw, re.M):
        probs.append("HTML/クラシック/Markdown混入")
    if re.sub(r"<!-- wp:([a-z0-9-]+)(?: [^>]*)? -->[\s\S]*?<!-- /wp:\1 -->", "", raw).strip():
        probs.append("ブロック外のテキスト")
    asin = PRODUCT_BY_ID[a["product"]["productId"]]["asin"]
    if f'href="https://www.amazon.co.jp/dp/{asin}/?tag={TAG}" rel="sponsored nofollow"' not in raw:
        probs.append("Amazonリンク不一致")
    if probs:
        raise ValueError(f"保存後検証NG（投稿ID {saved['id']}）: " + ", ".join(probs))
    return raw


def report(saved, a, chars):
    raw = saved["content"]["raw"]
    print(json.dumps({
        "result": "OK", "postId": saved["id"], "status": saved["status"],
        "editUrl": f"{SITE}/wp-admin/post.php?post={saved['id']}&action=edit",
        "title": a["title"], "bodyChars": chars,
        "headings": len(re.findall(r"<!-- wp:heading\b", raw)),
        "paragraphs": raw.count("<!-- wp:paragraph -->"),
        "images": len(re.findall(r"<!-- wp:image\b", raw)),
        "product": PRODUCT_BY_ID[a["product"]["productId"]]["name"],
    }, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- illustration
def illustrate(article_path, index, svg_path):
    import cairosvg  # pip install --break-system-packages cairosvg
    a = load(article_path)
    s = a["sections"][index]
    svg = open(svg_path, encoding="utf-8").read()
    if re.search(r"<text\b|<tspan\b|<image\b|xlink:href=\"http|href=\"http|<script\b|<foreignObject\b", svg):
        die("SVGに文字・外部画像・スクリプトは入れない")
    png = cairosvg.svg2png(bytestring=svg.encode(), output_width=1536, output_height=1024)
    name = f"napo-{uuid.uuid4().hex[:8]}.png"
    media = wp("media", "POST", raw=png, headers={"Content-Type": "image/png", "Content-Disposition": f'attachment; filename="{name}"'})
    alt = s["heading"] + "のイメージイラスト"
    try:
        wp(f"media/{media['id']}", "POST", payload={"alt_text": alt})
    except WpError:
        print("代替テキストの設定に失敗（画像は保存済み）", file=sys.stderr)
    large = ((media.get("media_details") or {}).get("sizes") or {}).get("large")
    s["image"] = {"id": media["id"], "url": large["source_url"] if large else media["source_url"], "alt": alt}
    save(article_path, a)
    print(json.dumps({"result": "OK", "section": index, "mediaId": media["id"], "url": s["image"]["url"]}, ensure_ascii=False))


# ---------------------------------------------------------------- commands
def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, a):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False, indent=1)


def cmd_status():
    day = today()
    existing = wp(f"posts?context=edit&status=draft,publish,pending,future,private&slug={daily_slug(day)}&_fields=id,status,title")
    recent_titles = [p["title"]["raw"] for p in wp("posts?context=edit&status=draft,publish&per_page=40&orderby=date&order=desc&_fields=title")]
    recent = recent_product_ids()
    print(json.dumps({
        "today": day,
        "todaysDailyDraft": existing[0]["id"] if existing else None,
        "recentTitles": recent_titles,
        "recentProducts": recent,
        "productCandidates": [{k: p[k] for k in ("id", "name", "authorOrMaker", "publisher", "format", "summary")} for p in product_candidates(recent)],
    }, ensure_ascii=False, indent=1))


def cmd_validate(path):
    a = load(path)
    n = validate(a, [p["id"] for p in product_candidates(recent_product_ids())] if os.environ.get("WP_USER") else None)
    print(json.dumps({"result": "OK", "bodyChars": n}, ensure_ascii=False))


def cmd_post_daily(path):
    a = load(path)
    chars = validate(a, [p["id"] for p in product_candidates(recent_product_ids())])
    slug = daily_slug()
    if wp(f"posts?context=edit&status=draft,publish,pending,future,private&slug={slug}&_fields=id"):
        die("今日の下書きは作成済みです（重複防止のため作りません）")
    post = wp("posts", "POST", payload={"title": a["title"], "content": render(a), "excerpt": a["description"], "status": "draft", "slug": slug})
    saved = wp(f"posts/{post['id']}?context=edit")
    verify_saved(saved, a)
    report(saved, a, chars)


def rewritable(post):
    if post["status"] != "draft":
        return "対象外: " + post["status"]
    if (post.get("slug") or "").startswith("napoblog-daily-"):
        return "対象外: 毎日の新規下書き"
    if dt.datetime.fromisoformat(post["modified"]) - dt.datetime.fromisoformat(post["date"]) > HAND_EDIT_GRACE:
        return "対象外: 手動編集あり、またはリライト済み"
    raw = (post.get("content") or {}).get("raw", "")
    if AD_MARK in raw and "<!-- wp:table -->" in raw:
        return "対象外: v3形式済み"
    if not (post.get("title") or {}).get("raw", "").strip():
        return "対象外: タイトルなし"
    return None


def cmd_rewrite_candidates():
    found = []
    for page in range(1, 21):
        try:
            batch = wp(f"posts?context=edit&status=draft&per_page=50&orderby=id&order=desc&page={page}&_fields=id,title,slug,status,date,modified,content")
        except WpError as e:
            if e.code == 400:
                break
            raise
        for p in batch:
            if not rewritable(p):
                found.append({"postId": p["id"], "title": p["title"]["raw"]})
        if len(found) >= 5 or len(batch) < 50:
            break
    print(json.dumps({"candidates": found[:5]}, ensure_ascii=False, indent=1))


def cmd_rewrite(post_id, path):
    a = load(path)
    chars = validate(a, [p["id"] for p in product_candidates(recent_product_ids())])
    post = wp(f"posts/{post_id}?context=edit")
    reason = rewritable(post)
    if reason:
        die(reason)
    wp(f"posts/{post_id}", "POST", payload={"title": a["title"], "content": render(a), "excerpt": a["description"], "status": "draft"})
    saved = wp(f"posts/{post_id}?context=edit")
    verify_saved(saved, a)
    report(saved, a, chars)


def sample_article():
    para_ = "これはテスト用の段落です。読者が次に取る行動と判断基準を具体的に説明し、失敗しやすい点とその対処も添えています。"
    secs = []
    for i in range(6):
        secs.append({"heading": f"見出し{i}", "paragraphs": [f"{para_}章{i}の段落{j}。" * 2 for j in range(5)],
                     "steps": ["手順A", "手順B"] if i == 1 else [], "sourceUrls": ["https://help.openai.com/ja/"]})
    return {"title": "テスト記事のタイトル", "description": "説明" * 40, "intro": [para_ + "導入1" + para_, para_ + "導入2" + para_],
            "sections": secs, "comparison": {"heading": "比較", "headers": ["方法", "向く人"], "rows": [["A", "初心者 <安全>"], ["B", "中級者"]]},
            "product": {"productId": "biz-zero", "reason": "考えを言語化する練習に役立つため。", "suitableFor": "考えがまとまらない人", "limitation": "AIの操作解説ではない"},
            "faq": [{"question": f"質問{k}", "answer": para_ + f"回答{k}"} for k in range(3)],
            "conclusion": [para_ + "まとめ1", para_ + "まとめ2"],
            "sources": [{"title": "OpenAI ヘルプセンター", "url": "https://help.openai.com/ja/"}]}


def cmd_selftest():
    a = sample_article()
    n = validate(a)
    out = render(a)
    assert "&lt;安全&gt;" in out
    assert re.sub(r"<!-- wp:([a-z0-9-]+)(?: [^>]*)? -->[\s\S]*?<!-- /wp:\1 -->", "", out).strip() == ""
    fake = {"id": 1, "status": "draft", "content": {"raw": out}}
    verify_saved(fake, a)
    for bad, key in [("ここにリンク", "intro"), ("**強調**", "conclusion")]:
        b = json.loads(json.dumps(a)); b[key][0] += bad
        try:
            validate(b); raise AssertionError("不正を検出できない: " + bad)
        except ValueError:
            pass
    b = json.loads(json.dumps(a)); b["product"]["productId"] = "unknown"
    try:
        validate(b); raise AssertionError("商品リスト外を検出できない")
    except ValueError:
        pass
    print(f"PASS: 検証・ブロック変換・HTMLエスケープ・保存後検証（本文{n}字）")


def main(argv):
    if len(argv) < 2:
        print(__doc__); return
    c = argv[1]
    try:
        if c == "status": cmd_status()
        elif c == "validate": cmd_validate(argv[2])
        elif c == "illustrate": illustrate(argv[2], int(argv[3]), argv[4])
        elif c == "post-daily": cmd_post_daily(argv[2])
        elif c == "rewrite-candidates": cmd_rewrite_candidates()
        elif c == "rewrite": cmd_rewrite(int(argv[2]), argv[3])
        elif c == "preview":
            a = load(argv[2]); validate(a)
            open(argv[3], "w", encoding="utf-8").write(render(a)); print("OK")
        elif c == "selftest": cmd_selftest()
        else: print(__doc__)
    except (ValueError, WpError) as e:
        die(str(e))


if __name__ == "__main__":
    main(sys.argv)
