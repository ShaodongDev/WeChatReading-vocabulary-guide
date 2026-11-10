"""Use the book's own EPUB to give each pending term its real sentence.

usage: epub_context.py <workdir> <book.epub> [--force]

How a term is placed. WeRead highlight positions are NOT plain-text offsets (images and
markup inflate them), and WeRead's chapters don't always match the EPUB's table of
contents, so neither is relied on.
  1. Every highlight, across the whole book, is in reading order (chapter, then position).
  2. The user's longer highlights are verbatim text. They are located in order through
     the book and act as fixed points that bound where the words between them can be.
  3. Each word takes the first body-text occurrence after the previous highlight. Headings
     and labels are skipped, and punctuation the user included in the highlight must match.
  4. If a word only appears in a heading, the next sentence using a form of it is taken.
Confidence: high = the spacing from the previous highlight in the chapter looks like normal
reading and no second candidate fits, or it's the only candidate between two fixed points.
Everything else is medium and listed in check_these. Unplaced terms stay pending, and the
batches are rewritten so only those are left for sentence writing.
"""
import argparse
import html
import json
import os
import posixpath
import re
import sys
import urllib.parse
import zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import MAX_TERM_WORDS, load, save, write_batches  # noqa: E402

QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " "})  # length-preserving


# ---------------------------------------------------------------- EPUB text
def to_text(raw):
    raw = re.sub(r"(?is)<(script|style|head)\b[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|h[1-6]|li|blockquote|tr|section|figcaption|dt|dd)>", "\n", raw)
    txt = html.unescape(re.sub(r"<[^>]+>", "", raw)).replace(" ", " ")
    txt = re.sub(r"[ \t\r\f\v]+", " ", txt)
    return re.sub(r"\s*\n\s*", "\n", txt).strip()


def load_epub(path):
    """Return (book_text, file_starts, toc) where toc = [(title, offset_in_book_text)] in reading order."""
    with zipfile.ZipFile(path) as z:
        cont = ET.fromstring(z.read("META-INF/container.xml"))
        opf_path = next(el.get("full-path") for el in cont.iter() if el.tag.endswith("rootfile"))
        opf = ET.fromstring(z.read(opf_path))
        items = {el.get("id"): el for el in opf.iter() if el.tag.split("}")[-1] == "item"}
        spine = [el.get("idref") for el in opf.iter() if el.tag.split("}")[-1] == "itemref"]
        base = posixpath.dirname(opf_path)
        full = lambda href, rel=base: posixpath.normpath(posixpath.join(rel, urllib.parse.unquote(href)))  # noqa: E731
        raws, texts, paths = [], [], []
        for idref in spine:
            it = items.get(idref)
            if it is None:
                continue
            p = full(it.get("href"))
            try:
                raw = z.read(p).decode("utf-8", "ignore")
            except KeyError:
                continue
            raws.append(raw)
            texts.append(to_text(raw))
            paths.append(p)
        starts, pos = [], 0
        for t in texts:
            starts.append(pos)
            pos += len(t) + 2
        book = "\n\n".join(texts)

        # table of contents: EPUB3 nav first, then EPUB2 ncx
        entries = []
        nav = next((it for it in items.values() if "nav" in (it.get("properties") or "").split()), None)
        ncx = next((it for it in items.values() if it.get("media-type") == "application/x-dtbncx+xml"), None)
        try:
            if nav is not None:
                np_ = full(nav.get("href"))
                raw = z.read(np_).decode("utf-8", "ignore")
                m = re.search(r'(?is)<nav[^>]*epub:type="toc"[^>]*>(.*?)</nav>', raw)
                for href, label in re.findall(r'(?is)<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', m.group(1) if m else raw):
                    entries.append((to_text(label), full(href.split("#")[0], posixpath.dirname(np_)),
                                    href.split("#")[1] if "#" in href else ""))
            if not entries and ncx is not None:
                cp = full(ncx.get("href"))
                raw = z.read(cp).decode("utf-8", "ignore")
                for label, src in re.findall(r'(?is)<navLabel>\s*<text>(.*?)</text>\s*</navLabel>\s*<content[^>]*src="([^"]+)"', raw):
                    entries.append((to_text(label), full(src.split("#")[0], posixpath.dirname(cp)),
                                    src.split("#")[1] if "#" in src else ""))
        except Exception:
            entries = []
        toc = []
        for label, p, frag in entries:
            if p not in paths:
                continue
            fi = paths.index(p)
            off = 0
            if frag:
                k = raws[fi].find(f'id="{frag}"')
                if k >= 0:
                    off = len(to_text(raws[fi][:raws[fi].rfind("<", 0, k)]))
            toc.append((label, starts[fi] + min(off, len(texts[fi]))))
        return book, starts, toc


# ---------------------------------------------------------------- matching helpers
def clean_term(t):
    t = t.strip().replace("\n", " ")
    t = re.sub(r"^[\W_]+", "", t)
    return re.sub(r"[\W_]+$", "", t)


def term_regex(term):
    words = term.translate(QUOTES).split()
    pat = r"\s+".join(re.escape(w) for w in words)
    if re.match(r"\w", words[0]):
        pat = r"(?<![\w'])" + pat
    if re.search(r"\w$", words[-1]):
        pat = pat + r"(?![\w])"
    return re.compile(pat, re.I)


def ntitle(s):
    s = s.translate(QUOTES).lower()
    return re.sub(r"[^a-z0-9一-鿿]+", " ", s).strip()


def is_body(text, i):
    ps = text.rfind("\n", 0, i) + 1
    pe = text.find("\n", i)
    para = text[ps:len(text) if pe < 0 else pe].strip()
    n = len(para.split())
    return n >= 8 or (n >= 4 and para[-1:] in ".!?;:")


def para_words(text, i):
    ps = text.rfind("\n", 0, i) + 1
    pe = text.find("\n", i)
    return len(text[ps:len(text) if pe < 0 else pe].split())


def sentence_at(text, i, j):
    """Return (sentence, start) for the sentence containing text[i:j]; short ones get a neighbour."""
    ps = text.rfind("\n", 0, i) + 1
    pe = text.find("\n", j)
    pe = len(text) if pe < 0 else pe
    para = text[ps:pe]
    spans, s = [], 0
    for m in re.finditer(r"(?<=[.!?])[\"'”’)\]]*\s+(?=[\"'“‘(\[]?[A-Z0-9])", para):
        spans.append((s, m.start() + len(m.group(0).rstrip())))
        s = m.end()
    spans.append((s, len(para)))
    rel = i - ps
    idx = next((k for k, (a, b) in enumerate(spans) if a <= rel < b + 1), len(spans) - 1)
    lo = hi = idx
    while len(para[spans[lo][0]:spans[hi][1]].split()) < 12:   # grow by whole sentences, never oscillate
        if hi + 1 < len(spans):
            hi += 1
        elif lo > 0:
            lo -= 1
        else:
            break
    a, b = spans[lo][0], spans[hi][1]
    return para[a:b].strip(), ps + a + (len(para[a:b]) - len(para[a:b].lstrip()))


def bolded(text, i, j, max_words=55):
    sent, st = sentence_at(text, i, j)
    ri, rj = i - st, j - st
    if not (0 <= ri < rj <= len(sent)):
        return None
    out = sent[:ri] + "**" + sent[ri:rj] + "**" + sent[rj:]
    words = out.split()
    if len(words) > max_words:
        k = next(n for n, w in enumerate(words) if "**" in w)
        lo, hi = max(0, k - 25), min(len(words), k + 25)
        out = ("… " if lo else "") + " ".join(words[lo:hi]) + (" …" if hi < len(words) else "")
    return out


# ---------------------------------------------------------------- placement
def related_form(book, norm, after, term, limit=2500):
    """For a heading-only term: the next body sentence using a form of its longest word."""
    w = max(re.findall(r"[A-Za-z]+", term) or [""], key=len)
    if len(w) < 4:
        return None
    stem = (w[:max(5, len(w) - 3)] if len(w) >= 7 else w).lower()
    for m in re.finditer(r"(?<![\w'])" + re.escape(stem) + r"\w*", norm[after:after + limit], re.I):
        i, j = after + m.start(), after + m.end()
        while i > 0 and re.match(r"[\w-]", norm[i - 1]):      # bold the whole word, e.g. in-depth
            i -= 1
        while j < len(norm) and re.match(r"[\w-]", norm[j]):
            j += 1
        if is_body(book, i):
            return i, j
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("epub")
    ap.add_argument("--force", action="store_true", help="redo all book quotes (written sentences are kept)")
    args = ap.parse_args()
    wd = args.workdir
    bk = load(os.path.join(wd, "book.json"))
    terms = {t["bookmarkId"]: t for t in load(os.path.join(wd, "terms.json"), [])}
    sent = load(os.path.join(wd, "sentences.json"), {})
    if args.force:
        sent = {k: v for k, v in sent.items() if not (isinstance(v, dict) and v.get("source") == "book")}
    book, _, _ = load_epub(args.epub)
    norm = book.translate(QUOTES)
    low = norm.lower()

    # every highlight in reading order: WeRead chapter order, then position in the chapter
    corder = {c["chapterUid"]: c.get("chapterIdx", c["chapterUid"]) for c in bk["chapters"]}
    pos = lambda h: int(h["range"].split("-")[0])  # noqa: E731
    hs = sorted(bk["highlights"], key=lambda h: (corder.get(h["chapterUid"], h["chapterUid"]), pos(h)))

    # fixed points: longer highlights are verbatim text; locate them in order through the whole book
    anchor_at, cursor = {}, 0
    for n, h in enumerate(hs):
        if len(h["text"].split()) <= MAX_TERM_WORDS:
            continue
        frag = re.sub(r"\s+", " ", h["text"].translate(QUOTES)).strip().lower()
        for f, back in ((frag[:70], 0), (frag[-70:], len(frag) - len(frag[-70:]))):
            if len(f) < 25:
                continue
            k = low.find(f, cursor)
            if k >= 0:
                anchor_at[n] = max(0, k - back)
                cursor = anchor_at[n] + 1
                break
    anchor_idx = sorted(anchor_at)

    placed, review, missed = 0, [], []
    last, last_h = 0, None                     # last placed location and highlight
    rates = []                                 # text chars per WeRead position unit, from confident picks
    prev_term = None
    for n, h in enumerate(hs):
        if n in anchor_at:
            if last_h is not None and last_h["chapterUid"] == h["chapterUid"] and pos(h) > pos(last_h):
                r = (anchor_at[n] - last) / (pos(h) - pos(last_h))
                if 0.05 < r < 3:
                    rates.append(r)
            last, last_h = max(last, anchor_at[n]), h
            continue
        b = h["bookmarkId"]
        if b not in terms or (b in sent and not args.force):
            continue
        ct = clean_term(h["text"])
        if not ct:
            missed.append(h["text"])
            continue
        prev_a = max((anchor_at[i] for i in anchor_idx if i < n), default=0)
        next_a = min((anchor_at[i] for i in anchor_idx if i > n), default=len(norm))
        rx = term_regex(ct)
        hi = min(next_a + 400, len(norm))
        occ = [(m.start(), m.end()) for m in rx.finditer(norm, max(last, prev_a), hi)]
        if not occ:                            # the previous pick may have overshot a little: look back a bit
            occ = [(m.start(), m.end()) for m in rx.finditer(norm, max(prev_a, last - 3000), hi)]
        if not occ:
            missed.append(h["text"])
            continue
        exact_case = [o for o in occ if book[o[0]:o[1]].translate(QUOTES) == ct.translate(QUOTES)]
        cands = exact_case or occ              # highlights are verbatim, so case is a real signal
        raw_end = h["text"].strip()[-1:]
        if raw_end in ".,;:?!":                # the user's highlight included punctuation: honour it
            cands = [o for o in cands if norm[o[1]:o[1] + 1] == raw_end] or cands
        # skip contents-page style lines unless nothing else exists
        cands = [o for o in cands if is_body(book, o[0]) or para_words(book, o[0]) <= 8] or cands
        same_ch = last_h is not None and last_h["chapterUid"] == h["chapterUid"] and pos(h) > pos(last_h)
        # reading pace: text chars per WeRead position unit (WeRead counts markup/images, so usually < 1)
        rate = sorted(rates[-15:])[len(rates[-15:]) // 2] if rates else 0.8
        if same_ch:                            # expected spot from the reading pace so far
            expected = last + (pos(h) - pos(last_h)) * rate
            tol = 0.5 * (pos(h) - pos(last_h)) * rate + 300
            order = sorted(cands, key=lambda o: abs(o[0] - expected))
            near = [o for o in cands if abs(o[0] - expected) <= tol]
            conf = "high" if (order[0] == cands[0] and len(near) <= 1) or near == order[:1] or len(cands) == 1 \
                else "medium"
            why = f"{len(near)} candidates fit the reading pace" if len(near) > 1 else "reading pace and order disagree"
        else:
            order = list(cands)                # first occurrence after the previous highlight
            conf = "high" if (len(cands) == 1 and prev_a > 0 and next_a < len(norm)) else "medium"
            why = "first word of a chapter" if last_h is None or last_h["chapterUid"] != h["chapterUid"] else \
                  f"no reading pace yet, {len(cands)} candidates"
        text, pick = None, None
        for k, o in enumerate(order[:4]):      # best candidate first; a heading hands over to the next sentence
            if is_body(book, o[0]):
                text = bolded(book, *o)
            else:
                rf = related_form(book, norm, o[1], ct)
                text = bolded(book, *rf) if rf else None
                if text:
                    conf, why = "medium", "highlighted in a heading: next sentence using the word"
                elif para_words(book, o[0]) >= 6:          # a list item or caption is still real context
                    text = bolded(book, *o)
                    conf, why = "medium", "short line (list item or caption)"
            if text:
                pick = o
                if k:
                    conf, why = "medium", "best candidate gave no usable sentence"
                break
        if text and len(text.split()) - len(ct.split()) < 5:
            text = None
        if not text:
            missed.append(h["text"])
            continue
        if same_ch:
            r = (pick[0] - last) / (pos(h) - pos(last_h))
            if conf == "high" or (pick == cands[0] and 0.2 <= r <= 2.0):
                rates.append(r)
        sent[b] = {"text": text, "source": "book", "confidence": conf}
        if conf != "high":
            review.append({"term": h["text"].strip(), "chapter": terms[b]["chapter"], "why": why, "sentence": text,
                           "previous_word": prev_term,
                           "chars_after_previous": pick[0] - last if last else None})
        last, last_h, prev_term = pick[0] + 1, h, h["text"].strip()
        placed += 1
    save(os.path.join(wd, "sentences.json"), sent)
    pending, batches = write_batches(wd)
    print(json.dumps({"from_book": placed, "fixed_points_found": f"{len(anchor_at)}/{sum(1 for h in hs if len(h['text'].split()) > MAX_TERM_WORDS)}",
                      "check_these": review, "not_placed": missed,
                      "terms_needing_sentences": len(pending), "batches": batches}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
