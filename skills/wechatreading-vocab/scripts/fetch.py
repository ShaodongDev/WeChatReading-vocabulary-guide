"""Fetch one book's highlights and notes from WeChatReading into the work directory.

usage:
  fetch.py --title "Example Book"            # match against books that have notes
  fetch.py --book-id BOOK_ID
  fetch.py --list                            # English-titled books with highlights

Writes <work>/book.json, <work>/terms.json and <work>/batches/batch_NN.json for
terms that have no sentence yet. Prints a JSON summary on stdout.
Exit code 2 = several books matched (candidates printed); pick one and rerun with --book-id.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (MAX_TERM_WORDS, api, chapter_order, load, save, setting, word_count,  # noqa: E402
                    workdir, write_batches)


def all_notebooks():
    out, last, seen = [], None, set()
    while True:
        params = {"count": 100}
        if last is not None:
            params["lastSort"] = last
        r = api("/user/notebooks", **params)
        books = r.get("books", [])
        out += books
        if r.get("hasMore") != 1:
            return out
        if not books or books[-1].get("sort") is None or books[-1]["sort"] in seen:
            sys.exit("Notebook pagination did not advance; no complete book list is available.")
        last = books[-1]["sort"]
        seen.add(last)


def norm(s):
    return re.sub(r"[^a-z0-9一-鿿]+", " ", s.lower()).strip()


def find_book(title):
    nbs = all_notebooks()
    q = norm(title)
    exact = [b for b in nbs if norm(b["book"].get("title", "")) == q]
    if exact:
        return exact, nbs
    sub = [b for b in nbs if q and q in norm(b["book"].get("title", ""))]
    if sub:
        return sub, nbs
    qt = set(q.split())
    scored = sorted(((len(qt & set(norm(b["book"].get("title", "")).split())) / max(len(qt), 1), b) for b in nbs),
                    key=lambda x: -x[0])
    return [b for s, b in scored if s >= 0.6][:8], nbs


def all_my_reviews(book_id):
    out, synckey, seen = [], 0, {0}
    while True:
        r = api("/review/list/mine", bookid=book_id, synckey=synckey, count=20)
        out += r.get("reviews", [])
        if r.get("hasMore") != 1:
            return out
        synckey = r.get("synckey", 0)
        if synckey in seen:
            sys.exit("Personal-note pagination did not advance; no partial export was saved.")
        seen.add(synckey)


STOP = {"the", "a", "an", "of", "and", "to", "in", "on", "for", "with", "edition", "ed", "book", "z", "lib", "library",
        "sk", "1lib", "org", "epub", "pdf", "vol", "volume"}


def epub_candidates(title, author, directory=None):
    """Local .epub files whose name shares most of the title's significant words."""
    directory = directory or setting("EPUB_DIR")
    if directory:
        folder = Path(directory).expanduser()
        paths = [str(p) for p in folder.rglob("*.epub")] if folder.is_dir() else []
    else:
        try:
            paths = subprocess.run(["mdfind", "kMDItemContentTypeTree == org.idpf.epub-container"],
                                   capture_output=True, text=True, timeout=30).stdout.splitlines()
        except (OSError, subprocess.TimeoutExpired):
            return []
    def toks(s):   # drop apostrophes before splitting, and a plural -s, so Designer's ~ Designers
        return [w[:-1] if len(w) > 3 and w.endswith("s") else w
                for w in norm(s.replace("'", "").replace("’", "")).split()]
    tw = [w for w in toks(re.sub(r"\(.*?\)|:.*$", "", title)) if w not in STOP][:5]
    if not tw:
        return []
    hits = []
    for p in paths:
        if "Vocabulary in Context" in p or any(cache in p for cache in
                ("/.cache/wechatreading-vocab/", "/.cache/weread-vocab/")):
            continue                       # our own output, not the source book
        nw = set(toks(os.path.basename(p)))
        score = sum(1 for w in tw if w in nw) / len(tw)
        if score >= 0.75:
            hits.append((score, p))
    return [p for s, p in sorted(hits, key=lambda x: -x[0])[:5]]


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--title")
    g.add_argument("--book-id")
    g.add_argument("--list", action="store_true")
    ap.add_argument("--epub-dir", help="Search this directory for a matching source EPUB")
    ap.add_argument("--without-notes", action="store_true", help="Export highlights without personal thoughts")
    args = ap.parse_args()

    if args.list:
        nbs = all_notebooks()
        rows = [{"bookId": b["bookId"], "title": b["book"].get("title"), "author": b["book"].get("author"),
                 "highlights": b.get("noteCount"), "notes": b.get("reviewCount")}
                for b in nbs if re.fullmatch(r"[\x20-\x7e’‘“”–—]+", b["book"].get("title", "") or "")]
        print(json.dumps(sorted(rows, key=lambda r: -(r["highlights"] or 0)), ensure_ascii=False, indent=1))
        return

    if args.title:
        matches, _ = find_book(args.title)
        if not matches:
            sys.exit(f'No book with highlights matches "{args.title}". Try fetch.py --list.')
        if len(matches) > 1:
            print(json.dumps([{"bookId": b["bookId"], "title": b["book"].get("title"), "author": b["book"].get("author"),
                               "highlights": b.get("noteCount")} for b in matches], ensure_ascii=False, indent=1))
            sys.exit(2)
        book_id = matches[0]["bookId"]
    else:
        book_id = args.book_id

    bm = api("/book/bookmarklist", bookId=book_id)
    reviews = [] if args.without_notes else all_my_reviews(book_id)
    meta = bm.get("book") or {}
    chapters = bm.get("chapters", [])
    book = {"bookId": book_id, "title": meta.get("title", ""), "author": meta.get("author", ""),
            "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "chapters": chapters, "highlights": [], "notes": []}
    order = chapter_order(book)
    titles = {c["chapterUid"]: c.get("title", "") for c in chapters}
    start = lambda h: int(h["range"].split("-")[0])  # noqa: E731
    hs = sorted(bm.get("updated", []), key=lambda h: (order.get(h["chapterUid"], h["chapterUid"]), start(h)))
    book["highlights"] = [{"bookmarkId": h["bookmarkId"], "chapterUid": h["chapterUid"], "range": h["range"],
                           "text": h.get("markText", ""), "createTime": h.get("createTime")} for h in hs]
    for r in reviews:
        v = r.get("review", {})
        book["notes"].append({"reviewId": v.get("reviewId"), "chapterUid": v.get("chapterUid"), "range": v.get("range"),
                              "abstract": v.get("abstract", ""), "content": v.get("content", ""),
                              "createTime": v.get("createTime")})

    wd = workdir(book_id)
    prev = load(os.path.join(wd, "book.json"))
    prev_ids = {h["bookmarkId"] for h in prev["highlights"]} if prev else set()
    save(os.path.join(wd, "book.json"), book)

    notes_by_key = {(n["chapterUid"], n["range"]): n["content"] for n in book["notes"]}
    terms = []
    for h in book["highlights"]:
        if word_count(h["text"]) > MAX_TERM_WORDS or not h["text"].strip():
            continue
        longer = [x for x in book["highlights"] if x["chapterUid"] == h["chapterUid"] and word_count(x["text"]) > MAX_TERM_WORDS]
        near = sorted(longer, key=lambda x: abs(start(x) - start(h)))[:2]
        t = {"bookmarkId": h["bookmarkId"], "chapter": titles.get(h["chapterUid"], ""), "term": h["text"].strip(),
             "pos": start(h), "nearby": [f"[pos {start(x)}] {x['text'].strip()}" for x in near]}
        note = notes_by_key.get((h["chapterUid"], h["range"]))
        if note:
            t["your_note"] = note.strip()[:300]
        terms.append(t)
    save(os.path.join(wd, "terms.json"), terms)
    pending, batches = write_batches(wd)

    now_ids = {h["bookmarkId"] for h in book["highlights"]}
    term_ids = {t["bookmarkId"] for t in terms}
    print(json.dumps({
        "bookId": book_id, "title": book["title"], "author": book["author"], "workdir": wd,
        "first_run": prev is None,
        "new_highlights": len(now_ids - prev_ids) if prev else len(now_ids),
        "new_terms": len((now_ids - prev_ids) & term_ids) if prev else len(term_ids),
        "removed_highlights": len(prev_ids - now_ids) if prev else 0,
        "highlights": len(book["highlights"]), "notes": len(book["notes"]), "terms": len(terms),
        "terms_needing_sentences": len(pending), "batches": batches,
        "local_epub_candidates": epub_candidates(book["title"], book["author"], args.epub_dir),
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
