"""Build the WeChatReading-friendly EPUB (and optionally a Markdown copy) from the work directory.

usage: build.py <workdir> [--out-dir ~/Desktop] [--md] [--preview]

Needs book.json, terms.json, sentences.json and ipa.json in <workdir>.
--preview renders the first chapters in WeChatReading-like green and dark themes to
<workdir>/preview/*.png so the result can be checked before handing it over.
"""
import argparse
import datetime
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import SKILL_DIR, browser_path, chapter_order, load, word_count, MAX_TERM_WORDS  # noqa: E402


def render_png(browser, source, destination, size, scale=1):
    browser_env = os.environ.copy()
    browser_env.pop("WEREAD_API_KEY", None)
    with tempfile.TemporaryDirectory(prefix="wechatreading-browser-") as profile:
        try:
            subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                            "--disable-background-networking", f"--user-data-dir={profile}",
                            f"--force-device-scale-factor={scale}", f"--window-size={size}",
                            f"--screenshot={destination}", Path(source).resolve().as_uri()],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45, check=True,
                           env=browser_env)
        except (OSError, subprocess.SubprocessError):
            return False
    return os.path.isfile(destination)


def esc(s):
    return html.escape(s, quote=False)


def clean_term(t):
    t = t.strip().replace("\n", " / ")
    t = re.sub(r"^[—–\-\s“\"‘'(]+", "", t)
    t = re.sub(r"[\s.,:;)\]”\"’]+$", "", t)
    return t


def sentence_html(s):
    return re.sub(r"\*\*(.+?)\*\*", r'<span class="hl">\1</span>', esc(s))


def note_html(text):
    return "<br/>".join(esc(l.strip()) for l in text.strip().splitlines() if l.strip())


def short_title(t):
    """Book title without subtitle, parenthetical or edition tag: 'X: Y (4th Edition)' -> 'X'."""
    s = re.split(r"\s*[:(]\s*", t)[0]
    s = re.sub(r"\s*[-–—]\s*[^-–—]*\b(edition|version|ed\.)\s*$", "", s, flags=re.I)
    return s.strip() or t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--out-dir", default=os.path.expanduser("~/Desktop"))
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--preview", action="store_true")
    a = ap.parse_args()
    wd = a.workdir
    chrome = browser_path()
    book = load(os.path.join(wd, "book.json"))
    terms = {t["bookmarkId"]: t for t in load(os.path.join(wd, "terms.json"), [])}
    sent = load(os.path.join(wd, "sentences.json"), {})
    ipa = load(os.path.join(wd, "ipa.json"), {})
    if not book:
        sys.exit("book.json missing - run fetch.py first")
    missing = [t["term"] for b, t in terms.items() if b not in sent]
    if missing:
        sys.exit(f"{len(missing)} terms still have no sentence (e.g. {missing[:5]}). Finish the sentence step first.")
    bad_ipa = [t["term"] for b, t in terms.items() if "?" in ipa.get(b, "?")]
    if bad_ipa:
        sys.exit(f"{len(bad_ipa)} terms have unresolved IPA; resolve ipa_report.json before building.")
    report = load(os.path.join(wd, "ipa_report.json"), {})
    if report.get("unresolved"):
        sys.exit("Pronunciation choices are still unresolved; review ipa_report.json and rerun ipa.py.")

    title, author = book["title"], book.get("author", "")
    st = short_title(title)
    chap = {c["chapterUid"]: c for c in book["chapters"]}
    order = chapter_order(book)
    hs = book["highlights"]
    notes = {(n["chapterUid"], n["range"]): n for n in book["notes"] if n.get("range")}
    ts = [h["createTime"] for h in hs if h.get("createTime")]
    d = lambda t: datetime.date.fromtimestamp(t).isoformat()  # noqa: E731
    src = lambda b: (sent[b].get("source") if isinstance(sent[b], dict) else "written")  # noqa: E731
    text = lambda b: (sent[b].get("text") if isinstance(sent[b], dict) else sent[b])  # noqa: E731
    n_book = sum(1 for b in terms if src(b) == "book")
    n_written = len(terms) - n_book

    # ---------------- intro
    if n_written and n_book:
        src_line = (f"<p><b>Example</b>: {n_book} of the sentences are quoted from the book and marked "
                    f"<span class=\"src\">from the book</span>. The other {n_written} were written to match the chapter, "
                    f"using the word the way it most likely appears there. <b>Those are not quotes.</b></p>")
    elif n_book:
        src_line = "<p><b>Example</b>: the sentence from the book where the word appears.</p>"
    else:
        src_line = ("<p><b>Example</b>: a sentence that uses the word the way it most likely appears in that chapter. "
                    "These sentences were written to match the chapter. <b>They are not quoted from the book.</b></p>")
    when = f", made between {d(min(ts))} and {d(max(ts))}" if ts else ""
    sample = next((b for b in terms), None)
    parts = ['<h1 id="how-to-use">How to Use This Book</h1>', '<div class="intro">',
             f"<p>These are my highlights from <em>{esc(title)}</em>{' by ' + esc(author) if author else ''}: "
             f"<b>{len(hs)}</b> highlights across <b>{len({h['chapterUid'] for h in hs})}</b> chapters{when}.</p>",
             "<p>Every word or short phrase I highlighted has a card like this:</p></div>"]
    if sample:
        parts += ['<div class="entry">',
                  f'<div class="term"><span class="word">{esc(clean_term(terms[sample]["term"]))}</span> '
                  f'<span class="ipa">{esc(ipa.get(sample, ""))}</span></div>',
                  f'<div class="example"><p>{sentence_html(text(sample))}</p></div></div>']
    parts += ['<div class="intro">',
              "<p><b>Word</b>: what I highlighted, followed by its American IPA.</p>", src_line,
              "<p><b>Longer highlights</b> are shown as quotes with a vertical bar, the same as in the book.</p>",
              "<p><b>Notes</b> I wrote in WeChatReading appear in small type below the highlight they belong to.</p></div>",
              '<div class="small"><p>Pronunciation: American (NAmE) IPA from the <em>Oxford Advanced Learner’s '
              "Dictionary</em>, 9th edition. Plurals and -ed/-ing forms that the dictionary doesn’t list are built from "
              "the base word. Names are transcribed by hand in the same style. Small words such as <em>a</em>, "
              "<em>the</em> and <em>to</em> use their weak forms inside phrases.</p></div>"]

    # ---------------- chapters
    md = [f"# {title} — My Highlights", "", f"{author} · {len(hs)} highlights", ""]
    used, cur = set(), None
    for h in hs:
        if h["chapterUid"] != cur:
            if cur is not None:
                parts.append("</div>")
            cur = h["chapterUid"]
            ctitle = chap.get(cur, {}).get("title", f"Chapter {order.get(cur, cur)}")
            m = re.match(r"(\d+)\s+(.*)", ctitle)
            heading = f"{m.group(1)} · {m.group(2)}" if m else ctitle
            in_ch = [x for x in hs if x["chapterUid"] == cur]
            nw = sum(1 for x in in_ch if x["bookmarkId"] in terms)
            parts += [f'<h1 id="ch{cur}">{esc(heading)}</h1>',
                      f'<div class="chapter-meta"><p>{len(in_ch)} highlights · {nw} words &amp; phrases</p></div>',
                      '<div class="chapter">']
            md += ["", f"## {heading}", ""]
        key = (h["chapterUid"], h["range"])
        note = notes.get(key)
        if note:
            used.add(key)
        b = h["bookmarkId"]
        if b in terms:
            t = clean_term(h["text"])
            tag = ' <span class="src">from the book</span>' if src(b) == "book" else ""
            parts += ['<div class="entry">',
                      f'<div class="term"><span class="word">{esc(t)}</span> <span class="ipa">{esc(ipa.get(b, ""))}</span></div>',
                      f'<div class="example"><p>{sentence_html(text(b))}{tag}</p></div>']
            if note:
                parts.append(f'<div class="note"><p>My note: {note_html(note["content"])}</p></div>')
            parts.append("</div>")
            md += [f"> {t}  {ipa.get(b, '')}", ">", f"> {'📖' if src(b) == 'book' else '✏️'} {text(b)}"]
        else:
            parts.append(f'<div class="quote"><p>{esc(h["text"].strip())}</p></div>')
            if note:
                parts.append(f'<div class="note"><p>My note: {note_html(note["content"])}</p></div>')
            md.append("> " + h["text"].strip().replace("\n", " / "))
        if note:
            md += [">"] + [f"> 💭 {l.strip()}" for l in note["content"].strip().splitlines() if l.strip()]
        md.append("")
    if cur is not None:
        parts.append("</div>")
    left = [n for n in book["notes"] if (n["chapterUid"], n.get("range")) not in used and n.get("content", "").strip()]
    if left:
        parts += ['<h1 id="other-notes">Other Notes</h1>',
                  '<div class="chapter-meta"><p>Notes on whole chapters, the book, or highlights no longer in the list</p></div>']
        for n in left:
            label = clean_term(n.get("abstract") or "") or chap.get(n.get("chapterUid"), {}).get("title", "") or "Book"
            parts += ['<div class="entry">', f'<div class="term"><span class="word">{esc(label)}</span></div>',
                      f'<div class="note"><p>{note_html(n["content"])}</p></div></div>']

    build = os.path.join(wd, "build")
    if os.path.isdir(build):
        shutil.rmtree(build)
    os.makedirs(build)
    with open(os.path.join(build, "book.html"), "w", encoding="utf-8") as f:
        f.write('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>'
                f"<title>{esc(st + ' — Vocabulary in Context')}</title></head><body>\n" + "\n".join(parts) + "\n</body></html>")

    # ---------------- cover (Chrome renders the HTML template to PNG)
    cover = None
    if chrome:
        tpl = Path(SKILL_DIR, "assets", "cover.html").read_text(encoding="utf-8")
        size = 150 if len(st) <= 32 else 120 if len(st) <= 55 else 96
        for k, v in {"TITLE": esc(st), "AUTHOR": esc(author), "TITLE_SIZE": str(size),
                     "STATS": f"{len(hs)} highlights · {len(terms)} words with US IPA"}.items():
            tpl = tpl.replace("{{" + k + "}}", v)
        ch = os.path.join(build, "cover.html")
        Path(ch).write_text(tpl, encoding="utf-8")
        cover = os.path.join(build, "cover.png")
        if not render_png(chrome, ch, cover, "1600,2400"):
            cover = None

    # ---------------- EPUB via pandoc
    if not shutil.which("pandoc"):
        sys.exit("pandoc was not found. Install it from https://pandoc.org/installing.html before building.")
    meta = os.path.join(build, "meta.yaml")
    q = lambda s: json.dumps(s, ensure_ascii=False)  # noqa: E731
    Path(meta).write_text(f"""---
title: {q(st + " — Vocabulary in Context")}
creator:
- role: author
  text: {q(author or "Unknown")}
lang: en
date: "{datetime.date.today().isoformat()}"
description: "My WeChatReading highlights, with an example sentence and American IPA for every word."
...
""", encoding="utf-8")
    safe = re.sub(r'[\\/:*?"<>|]+', "", st).strip()
    safe = safe or "WeChatReading Vocabulary"
    os.makedirs(os.path.expanduser(a.out_dir), exist_ok=True)
    out = os.path.join(os.path.expanduser(a.out_dir), f"{safe} - Vocabulary in Context.epub")
    cmd = ["pandoc", os.path.join(build, "book.html"), "-f", "html", "-t", "epub3", "--metadata-file", meta,
           "--css", os.path.join(SKILL_DIR, "assets", "wechatreading.css"), "--split-level=1", "--toc-depth=1", "-o", out]
    if cover:
        cmd[-2:-2] = ["--epub-cover-image", cover]
    subprocess.run(cmd, check=True)
    result = {"epub": out, "cards": len(terms), "quotes": sum(1 for h in hs if h["bookmarkId"] not in terms),
              "sentences_from_book": n_book, "sentences_written": n_written, "notes": len(book["notes"]),
              "cover": bool(cover)}
    if a.md:
        mdp = os.path.join(os.path.expanduser(a.out_dir), f"{safe} - highlights.md")
        Path(mdp).write_text("\n".join(md), encoding="utf-8")
        result["md"] = mdp

    # ---------------- optional preview
    if a.preview and chrome:
        import zipfile
        pv = os.path.join(wd, "preview")
        shutil.rmtree(pv, ignore_errors=True)
        os.makedirs(pv)
        z = zipfile.ZipFile(out)
        css = z.read("EPUB/styles/stylesheet1.css").decode()
        allp = sorted(n for n in z.namelist() if re.search(r"text/ch\d+\.xhtml$", n))
        carded = [n for n in allp if z.read(n).decode().count('class="entry"') >= 3]
        pages = (allp[:1] + carded[1:3]) if carded else allp[:3]   # intro + two chapters with cards
        shots = []
        for theme, bg, fg in (("green", "#E3EDCD", "#222"), ("dark", "#1C1C1E", "#B8B8B8")):
            for n in pages:
                body = z.read(n).decode()
                body = re.sub(r'<link rel="stylesheet"[^>]*>', f"<style>{css}</style>", body)
                body = body.replace("</head>", f"<style>body{{background:{bg};color:{fg};margin:0;padding:0 18px;"
                                    f"font-family:-apple-system,'PingFang SC',sans-serif}}</style></head>")
                hp = os.path.join(pv, f"{os.path.basename(n)[:-6]}_{theme}.xhtml")
                Path(hp).write_text(body, encoding="utf-8")
                png = hp[:-6] + ".png"
                if render_png(chrome, hp, png, "500,1500", scale=2):
                    shots.append(png)
        result["preview"] = shots
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
