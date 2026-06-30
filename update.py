#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L 東京喰種（スタジアム二俣川店 / PAPIMO）台別データを取得し、
アーカイブに蓄積 → index.html / data.csv を再生成 → git push する自走スクリプト。

- 機種ID・設置台・取得可能日付は毎回 PAPIMO から動的検出（機種入替にある程度耐性）。
- 差枚はスランプグラフ画像から画素復元（y軸は画像ごとに自動校正、1000枚/グリッド想定）。
- 過去に取れた日付は archive.json に永続保存し、消さずに積み上げる（14日の保持制限を超えて履歴化）。

依存: numpy, pillow（/usr/bin/python3 にインストール済み）
"""
import os, re, sys, json, time, html as ihtml, base64, io, subprocess, datetime, urllib.request

HALL = "00042031"
BASE = "https://papimo.jp"
ROOT = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = os.path.join(ROOT, "archive.json")
LOG = os.path.join(ROOT, "update.log")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ghoul-data-bot"

def log(*a):
    msg = "[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), " ".join(str(x) for x in a))
    print(msg, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass

def get(url, binary=False, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            return data if binary else data.decode("utf-8", "replace")
        except Exception as e:
            log("  retry", i + 1, url, repr(e))
            time.sleep(1.5)
    raise RuntimeError("GET failed: " + url)

# ---------- 機種・台・日付の動的検出 ----------
def discover():
    top = get("%s/P-World/hit/top/%s/" % (BASE, HALL))
    slot_cats = re.findall(r'/P-World/hit/index_machine/%s/(1-[0-9.]+-\d+)/' % HALL, top)
    slot_cats = list(dict.fromkeys(slot_cats))
    for cat in slot_cats:
        for page in range(1, 12):
            h = get("%s/P-World/hit/index_machine/%s/%s/?page=%d" % (BASE, HALL, cat, page))
            entries = re.findall(
                r'<a class="arrow_right" href="(/P-World/hit/index_sort/%s/(\d+)/[^"]+)">.*?</p>([^<]*)</a>' % HALL,
                h, re.S)
            if not entries:
                break
            for href, mid, name in entries:
                if "喰種" in name:
                    sort_url = BASE + href
                    sh = get(sort_url)
                    bans = re.findall(r'data-href="/P-World/hit/view/%s/(\d+)"' % HALL, sh)
                    bans = sorted(dict.fromkeys(bans), key=lambda x: int(x))
                    # 取得可能日付一覧（最初の台の詳細から）
                    vh = get("%s/P-World/hit/view/%s/%s" % (BASE, HALL, bans[0]))
                    dates = re.findall(r'<option value="(\d{8})"', vh)
                    dates = sorted(dict.fromkeys(dates))
                    log("discovered machine=%s name=%s 台=%s dates=%d" % (mid, name.strip(), bans, len(dates)))
                    return mid, bans, dates
    raise RuntimeError("L 東京喰種 がスロット一覧に見つかりません")

# ---------- 数値データ（詳細ページ） ----------
SUMMARY_RE = re.compile(
    r'BB回数\s+([\d,]+)\s+RB回数\s+([\d,]+)\s+ＢＢ確率\s+(\S+)\s+ART回数\s+([\d,]+)\s+'
    r'合成確率\s+(\S+)\s+総スタート\s+([\d,]+)\s+最終スタート\s+([\d,]+)\s+'
    r'ARTゲーム数\s+([\d,]+)\s+最大出メダル\s+([\d,]+)')

def parse_detail(ban, date):
    h = get("%s/P-World/hit/view/%s/%s/%s" % (BASE, HALL, ban, date))
    t = ihtml.unescape(re.sub(r"<[^>]+>", " ", h))
    m = SUMMARY_RE.search(t)
    rec = {}
    if m:
        g = [x.replace(",", "") for x in m.groups()]
        rec = dict(BB=g[0], RB=g[1], BB率=g[2], ART=g[3], 合成=g[4],
                   総スタート=g[5], 最終=g[6], ARTゲーム=g[7], 最大出=g[8])
    # スランプ画像 URL（当日＝指定日）を取得
    mm = re.search(r'<img alt="当日のスランプグラフ" src="([^"]+)"', h)
    rec["_graph"] = (BASE + mm.group(1)) if mm and mm.group(1).startswith("/") else (mm.group(1) if mm else None)
    return rec

# ---------- スランプ差枚の画素復元 ----------
import numpy as np
from PIL import Image

def extract_slump(png_bytes):
    im = np.asarray(Image.open(io.BytesIO(png_bytes)).convert("RGB")).astype(int)
    R, G, B = im[:, :, 0], im[:, :, 1], im[:, :, 2]
    H, W = R.shape
    # 校正: 横グリッド線（淡灰）からピッチ、零線（濃黒の横線）から y0 を検出
    gray = (abs(R - G) < 15) & (abs(G - B) < 15) & (R > 140) & (R < 215)
    grow = gray.sum(axis=1)
    rows = [y for y in range(H) if grow[y] > W * 0.5]
    centers = []
    for y in rows:
        if centers and y - centers[-1][-1] <= 2:
            centers[-1].append(y)
        else:
            centers.append([y])
    cen = [int(np.mean(c)) for c in centers]
    if len(cen) >= 3:
        diffs = sorted(cen[i + 1] - cen[i] for i in range(len(cen) - 1))
        pitch = diffs[len(diffs) // 2]
    else:
        pitch = 24
    dark = (R < 90) & (G < 90) & (B < 90)
    y0 = int(np.argmax(dark.sum(axis=1)))
    if pitch < 6:
        pitch = 24
    def val(y):
        return (y0 - y) / pitch * 1000.0
    red = (R > 175) & (G < 95) & (B < 115)
    xs = np.where(red.any(axis=0))[0]
    if len(xs) == 0:
        return [], 0
    x0, x1 = int(xs.min()), int(xs.max())
    span = max(1, x1 - x0)
    curve = []
    last = 0.0
    for x in range(x0, x1 + 1):
        col = np.where(red[:, x])[0]
        if len(col) == 0:
            continue
        v = val(col.mean())
        curve.append([round((x - x0) / span, 4), round(v)])
        last = v
    clip = ""
    if (red[:3, :].any() or red[-3:, :].any()):
        clip = "y軸クリップの可能性"
    return curve, round(last), clip if clip else None

# ---------- アーカイブ蓄積 ----------
def load_archive():
    if os.path.exists(ARCHIVE):
        return json.load(open(ARCHIVE, encoding="utf-8"))
    return {"machine": None, "data": {}}

def run_collect(dfrom=None, dto=None, force=False):
    """PAPIMOから収集してアーカイブに蓄積。
    - 既存の確定済み日（< 今日）はスキップして上書きしない（force=Trueで強制再取得）。
    - 当日（>= 今日）は未確定なので常に更新する。
    - dfrom/dto を YYYYMMDD で渡すと、その期間内の日付だけ収集対象にする。
    """
    arch = load_archive()
    mid, bans, dates = discover()
    arch["machine"] = mid
    arch.setdefault("data", {})
    today = datetime.date.today().strftime("%Y%m%d")
    if dfrom:
        dates = [d for d in dates if d >= dfrom]
    if dto:
        dates = [d for d in dates if d <= dto]
    log("collect target dates: %d (%s)%s" % (
        len(dates), (dates[0] + "〜" + dates[-1]) if dates else "-",
        " [force]" if force else ""))
    fetched = skipped = 0
    for ban in bans:
        slot = arch["data"].setdefault(ban, {})
        for d in dates:
            if (not force) and (d in slot) and (d < today):
                skipped += 1
                continue  # 確定済み既存データは上書きしない
            rec = parse_detail(ban, d)
            g = rec.pop("_graph", None)
            if g:
                try:
                    curve, end, note = extract_slump(get(g, binary=True))
                    rec["差枚"] = end
                    rec["curve"] = curve
                    if note:
                        rec["note"] = note
                except Exception as e:
                    log("  slump fail", ban, d, repr(e))
            slot[d] = rec
            fetched += 1
            time.sleep(0.15)
        log("台", ban, "done")
    log("collect summary: fetched=%d skipped(existing)=%d" % (fetched, skipped))
    arch["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    json.dump(arch, open(ARCHIVE, "w", encoding="utf-8"), ensure_ascii=False)
    return arch

# ---------- HTML / CSV 生成 ----------
def ci(s):
    try:
        return int(str(s).replace(",", ""))
    except Exception:
        return 0

def svg_slump(dates, per_day_curves, ends):
    PERDAY = 40
    pts, marks, cum = [], [], 0.0
    for d in dates:
        marks.append(len(pts))
        s = per_day_curves.get(d) or []
        for k in range(PERDAY):
            t = k / (PERDAY - 1)
            v = s[0][1] if s else 0
            for xn, vv in s:
                if xn <= t:
                    v = vv
                else:
                    break
            pts.append(cum + v)
        cum += ends.get(d, 0)
    W, Hh, pL, pR, pT, pB = 980, 240, 64, 14, 12, 26
    n = len(pts)
    ymin, ymax = min(pts + [0]), max(pts + [0])
    rng = (ymax - ymin) or 1
    rng *= 1.12
    ymid = (ymax + ymin) / 2
    ylo, yhi = ymid - rng / 2, ymid + rng / 2
    X = lambda i: pL + (W - pL - pR) * i / max(1, n - 1)
    Y = lambda v: pT + (Hh - pT - pB) * (yhi - v) / (yhi - ylo)
    s = ['<svg viewBox="0 0 %d %d" width="100%%" xmlns="http://www.w3.org/2000/svg" style="background:#fafafa">' % (W, Hh)]
    for k in range(5):
        v = ylo + (yhi - ylo) * k / 4
        yy = Y(v)
        s.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#ededed"/>' % (pL, yy, W - pR, yy))
        s.append('<text x="%d" y="%.1f" font-size="10" text-anchor="end" fill="#999">%s</text>' % (pL - 5, yy + 3, format(int(round(v / 100) * 100), ",")))
    if ylo < 0 < yhi:
        s.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#333"/>' % (pL, Y(0), W - pR, Y(0)))
    for j, i in enumerate(marks):
        xx = X(i)
        s.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#f0f0f0"/>' % (xx, pT, xx, Hh - pB))
        if j % max(1, len(marks) // 8) == 0:
            s.append('<text x="%.1f" y="%d" font-size="8" fill="#bbb">%s</text>' % (xx + 1, Hh - pB + 11, dates[j][4:6] + "/" + dates[j][6:]))
    pp = " ".join("%.1f,%.1f" % (X(i), Y(v)) for i, v in enumerate(pts))
    s.append('<polyline points="%s" fill="none" stroke="#e8285a" stroke-width="1.6"/>' % pp)
    s.append('</svg>')
    return "".join(s), int(round(cum))

def generate(arch, dfrom=None, dto=None, out="index"):
    data = arch["data"]
    bans = sorted(data.keys(), key=lambda x: int(x))
    all_dates = sorted({d for b in data for d in data[b]})
    if dfrom:
        all_dates = [d for d in all_dates if d >= dfrom]
    if dto:
        all_dates = [d for d in all_dates if d <= dto]
    keep = set(all_dates)  # 描画対象（期間フィルタ後）
    dlab = lambda d: "%s/%s" % (d[4:6], d[6:])
    cols = [("BB", "BB"), ("RB", "RB"), ("BB率", "BB確率"), ("合成", "合成"),
            ("総スタート", "総ｽﾀｰﾄ"), ("ART", "ART"), ("ARTゲーム", "ARTｹﾞｰﾑ"),
            ("最終", "最終ｽﾀｰﾄ"), ("最大出", "最大出メダル")]
    H = ['<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>L 東京喰種 まとめ</title>',
         '<style>body{font-family:-apple-system,"Hiragino Kaku Gothic ProN",sans-serif;margin:0;background:#f3f3f3;color:#222}',
         '.wrap{max-width:1000px;margin:0 auto;padding:16px}h1{font-size:19px;margin:6px 0}',
         '.sub{color:#777;font-size:12px;margin-bottom:12px;line-height:1.6}',
         '.idx{font-size:12px;margin:8px 0 16px}.idx a{display:inline-block;margin:2px 6px 2px 0;padding:3px 9px;background:#fff;border-radius:14px;text-decoration:none;color:#333;border:1px solid #e0e0e0}',
         '.card{background:#fff;border-radius:10px;padding:14px 16px;margin-bottom:18px;box-shadow:0 1px 4px rgba(0,0,0,.07)}',
         '.mh{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}.mh h2{font-size:17px;margin:0}',
         '.net{font-weight:700;font-size:15px}.pos{color:#0a8f3c}.neg{color:#d12a4a}',
         '.tot{font-size:12px;color:#555;margin:6px 0;line-height:1.7}.tot b{color:#111;font-size:13px}',
         'table{border-collapse:collapse;width:100%;font-size:11px;margin-top:6px}',
         'th,td{border:1px solid #ececec;padding:3px 5px;text-align:right;white-space:nowrap}',
         'th{background:#fafafa;color:#666}td:first-child,th:first-child{text-align:center}',
         'tr.tr td{background:#fff7f9;font-weight:700}details>summary{cursor:pointer;font-size:12px;color:#999;margin:4px 0}</style></head><body><div class="wrap">',
         '<h1>L 東京喰種 — 台別まとめ（スタジアム二俣川店）</h1>',
         '<div class="sub">機種ID %s／設置%d台／収集期間 %s〜%s／提供:PAPIMO（更新 %s）<br>スランプは全期間を<b>累積で連結</b>。差枚はグラフ画素から復元（精度±約150枚）。毎週自動更新。</div>'
         % (arch.get("machine"), len(bans), dlab(all_dates[0]) if all_dates else "-", dlab(all_dates[-1]) if all_dates else "-", arch.get("updated_at", "")),
         '<div class="idx">台ジャンプ：' + "".join('<a href="#m%s">%s番</a>' % (b, b) for b in bans) + '</div>']
    for b in bans:
        recs = data[b]
        ds = sorted(d for d in recs.keys() if d in keep)
        if not ds:
            continue
        ends = {d: ci(recs[d].get("差枚", 0)) for d in ds}
        curves = {d: recs[d].get("curve", []) for d in ds}
        svg, net = svg_slump(ds, curves, ends)
        cls = "pos" if net >= 0 else "neg"
        sg = "+" if net >= 0 else ""
        BB = sum(ci(recs[d].get("BB")) for d in ds)
        RB = sum(ci(recs[d].get("RB")) for d in ds)
        ART = sum(ci(recs[d].get("ART")) for d in ds)
        TS = sum(ci(recs[d].get("総スタート")) for d in ds)
        ARTG = sum(ci(recs[d].get("ARTゲーム")) for d in ds)
        MAX = max([ci(recs[d].get("最大出")) for d in ds] or [0])
        g = "1/%.0f" % (TS / (BB + RB)) if BB + RB else "-"
        H.append('<div class="card" id="m%s"><div class="mh"><h2>%s番</h2><span class="net %s">全期間累計 %s%s枚</span></div>' % (b, b, cls, sg, format(net, ",")))
        H.append(svg)
        H.append('<div class="tot">累計 → BB <b>%d</b>／RB <b>%d</b>／合成 <b>%s</b>／総スタート <b>%s</b>／ART <b>%d</b>／ARTゲーム <b>%s</b>／最大出メダル <b>%s</b>（%d日分）</div>'
                 % (BB, RB, g, format(TS, ","), ART, format(ARTG, ","), format(MAX, ","), len(ds)))
        H.append('<details><summary>日別データ（全項目・新しい順）</summary><table><tr><th>日付</th><th>差枚</th>' + "".join('<th>%s</th>' % l for _, l in cols) + '</tr>')
        for d in reversed(ds):
            r = recs[d]
            de = ends[d]
            dc = "pos" if de >= 0 else "neg"
            H.append('<tr><td>%s</td><td class="%s">%s%s</td>' % (dlab(d), dc, "+" if de >= 0 else "", format(de, ",")) + "".join('<td>%s</td>' % r.get(k, "-") for k, _ in cols) + '</tr>')
        H.append('</table></details></div>')
    H.append('</div></body></html>')
    html_path = os.path.join(ROOT, out + ".html")
    csv_path = os.path.join(ROOT, ("data" if out == "index" else out) + ".csv")
    open(html_path, "w", encoding="utf-8").write("".join(H))
    # CSV（描画期間と同じ範囲）
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("日付,台番,差枚,BB,RB,BB確率,合成確率,総スタート,ART,ARTゲーム数,最終スタート,最大出メダル\n")
        for d in all_dates:
            for b in bans:
                r = data[b].get(d)
                if not r:
                    continue
                f.write(",".join(str(x) for x in [
                    "%s/%s/%s" % (d[:4], d[4:6], d[6:]), b, r.get("差枚", ""),
                    r.get("BB", ""), r.get("RB", ""), r.get("BB率", ""), r.get("合成", ""),
                    r.get("総スタート", ""), r.get("ART", ""), r.get("ARTゲーム", ""),
                    r.get("最終", ""), r.get("最大出", "")]) + "\n")
    log("generated %s / %s (%d台, %d日%s)" % (
        os.path.basename(html_path), os.path.basename(csv_path), len(bans), len(all_dates),
        " 期間 %s〜%s" % (all_dates[0], all_dates[-1]) if all_dates else ""))

def git_push():
    def gx(*args):
        return subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True, text=True)
    gx("add", "-A")
    st = gx("status", "--porcelain").stdout.strip()
    if not st:
        log("no changes; skip push")
        return
    gx("-c", "user.email=freeeeeellc@gmail.com", "-c", "user.name=freeeeeellc",
       "commit", "-m", "weekly update %s" % datetime.date.today().isoformat())
    r = gx("push", "origin", "main")
    log("git push:", (r.stdout + r.stderr).strip()[-300:])

def norm_date(s):
    """'2026-07-01' / '2026/07/01' / '20260701' を 'YYYYMMDD' に正規化。"""
    if not s:
        return None
    d = re.sub(r"\D", "", s)
    if len(d) != 8:
        raise SystemExit("日付は YYYY-MM-DD か YYYYMMDD 形式で指定してください: %r" % s)
    return d

def main():
    import argparse
    p = argparse.ArgumentParser(description="L 東京喰種データ収集・ページ生成")
    p.add_argument("--from", dest="dfrom", help="期間開始 YYYY-MM-DD（収集・生成の両方に適用）")
    p.add_argument("--to", dest="dto", help="期間終了 YYYY-MM-DD")
    p.add_argument("--force", action="store_true", help="既存の確定済みデータも再取得して上書き")
    p.add_argument("--no-collect", action="store_true", help="取得せずアーカイブから再生成のみ")
    p.add_argument("--no-push", action="store_true", help="git push しない（ローカル確認用）")
    p.add_argument("--out", default="index", help="出力名（既定 index → index.html/data.csv）。期間別ページ作成に使用")
    a = p.parse_args()
    dfrom, dto = norm_date(a.dfrom), norm_date(a.dto)
    log("=== run start === args=%s" % vars(a))
    try:
        if a.no_collect:
            arch = load_archive()
            log("skip collect (--no-collect)")
        else:
            arch = run_collect(dfrom=dfrom, dto=dto, force=a.force)
        generate(arch, dfrom=dfrom, dto=dto, out=a.out)
        if a.no_push:
            log("skip push (--no-push)")
        else:
            git_push()
        log("=== run done ===")
    except SystemExit:
        raise
    except Exception as e:
        log("FATAL", repr(e))
        sys.exit(1)

if __name__ == "__main__":
    main()
