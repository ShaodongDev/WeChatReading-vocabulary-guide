"""American IPA for every term, from the user's OALD9 (NAmE transcriptions).

usage:
  <venv python> ipa.py --build-index     # once: OALD9.mdx -> ~/.cache/weread-vocab/oald9_index.json
  python3 ipa.py <workdir>               # resolve all terms -> <workdir>/ipa.json + report

Resolution order per word: book overrides (<workdir>/ipa_overrides.json "words"),
global overrides (ipa_global.json), the OALD headword or form-specific entry, a form
derived from the base word (-s/-ed/-ing/-er/-est/-ly...), then hyphen parts.
Term-level overrides ("terms": {bookmarkId: "/.../"}) replace the whole term.

The report lists words that need the agent's judgment:
  missing    - not in OALD and not derivable: transcribe by hand in the same style
  heteronym  - OALD has several pronunciations: pick per term from its sentence
  suspicious - OALD's own data looks broken (e.g. a length mark after a consonant)
and the derived forms, so they can be scanned for bad guesses.
"""
import html
import importlib.util
import json
import os
import re
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CACHE, load, save  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(CACHE, "oald9_index.json")
STRONG = {"a": "eɪ", "an": "æn", "the": "ðiː", "are": "ɑːr", "was": "wʌz", "to": "tuː", "of": "ʌv",
          "and": "ænd", "for": "fɔːr", "at": "æt", "as": "æz", "than": "ðæn"}


# ---------------------------------------------------------------- index build
def find_oald():
    env = os.environ.get("WEREAD_VOCAB_OALD")
    if env:
        path = os.path.expanduser(env)
        if not os.path.isfile(path):
            sys.exit("WEREAD_VOCAB_OALD does not point to an existing OALD9.mdx file.")
        return path
    return None


def load_readmdict():
    spec = importlib.util.find_spec("readmdict")
    if spec is None:
        sys.exit(f"readmdict is not installed. Run scripts/setup_env.sh, then use {CACHE}/venv/bin/python.")
    base = list(spec.submodule_search_locations)[0]
    pkg = types.ModuleType("readmdict")       # skip the package __init__, which exits without python-lzo
    pkg.__path__ = [base]
    sys.modules["readmdict"] = pkg
    s = importlib.util.spec_from_file_location("readmdict.readmdict", os.path.join(base, "readmdict.py"))
    mod = importlib.util.module_from_spec(s)
    sys.modules[s.name] = mod
    s.loader.exec_module(mod)
    return mod


PRON = re.compile(r'<span class="pron-g"[^>]*geo="n_am"[^>]*>.*?<span class="wrap">/</span>(.*?)<span class="wrap">/</span>', re.S)


def strip(h):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h))).strip()


def build_index():
    path = find_oald()
    if not path:
        sys.exit("OALD9.mdx not found. Set WEREAD_VOCAB_OALD=/path/to/OALD9.mdx")
    mdx = load_readmdict().MDX(path)
    prons, links = {}, {}
    for k, v in mdx.items():
        w = k.decode("utf-8", "ignore").strip()   # keep case: "IT" and "it" are different entries
        v = v.decode("utf-8", "ignore")
        if v.startswith("@@@LINK="):
            links.setdefault(w, []).append(v[8:].strip())
            continue
        for m in PRON.finditer(v):
            pron = re.sub(r"\s+", "", strip(m.group(1)))
            ctx = strip(v[max(0, m.start() - 1500):m.start()])
            i = ctx.rfind("BrE")
            toks_ = re.findall(r"[A-Za-z’'\-]+", ctx[:i] if i >= 0 else ctx)
            while toks_ and toks_[-1] in ("BrE", "NAmE"):
                toks_.pop()
            label = " ".join(toks_[-6:])
            prons.setdefault(w, []).append([label, pron])
    if not prons:
        sys.exit("No OALD9 American (n_am) pronunciations were found. Check this dictionary's format; "
                 "the existing index was left unchanged.")
    save(INDEX, {"source": path, "prons": prons, "links": links})
    print(f"indexed {len(prons)} headwords, {len(links)} links from {path} -> {INDEX}")


# ---------------------------------------------------------------- resolution
VL = set("ptkfθsʃ") | {"tʃ"}
SIB = {"s", "z", "ʃ", "ʒ", "tʃ", "dʒ"}
VOWEL_BEFORE_LEN = "iuɑɔɜ"
# BrE-only symbols that sometimes leak into OALD's NAmE slots. aɪə/ɔɪə/eɪə (fire, royal, layer)
# and aʊə/oʊə (flower, lower) are fine in American English, hence the lookbehinds.
BRITISH = re.compile("ɒ|əʊ|(?<![aeɔ])ɪə|(?<![ɪ])eə|(?<![ao])ʊə")


def last_seg(p):
    p = p.rstrip("ː")
    return "tʃ" if p.endswith("tʃ") else "dʒ" if p.endswith("dʒ") else p[-1]


def stress1(p):
    return p if "ˈ" in p else "ˈ" + p


def add(p, kind):
    s = last_seg(p)
    if kind == "s":
        return stress1(p) + "ɪz" if s in SIB else p + ("s" if s in VL else "z")
    if kind == "ed":
        return stress1(p) + "ɪd" if s in ("t", "d") else p + ("t" if s in VL else "d")
    return stress1(p) + {"ing": "ɪŋ", "er": "ər", "est": "ɪst", "ly": "li", "ness": "nəs", "less": "ləs"}[kind]


class Resolver:
    def __init__(self, overrides):
        idx = load(INDEX)
        if not idx:
            sys.exit(f"No OALD index at {INDEX}. Run: {CACHE}/venv/bin/python {HERE}/ipa.py --build-index")
        self.P, self.L = idx["prons"], idx["links"]
        g = load(os.path.join(HERE, "ipa_global.json"), {})
        g.pop("_comment", None)
        user_global = load(os.path.join(CACHE, "ipa_global.json"), {})
        user_global.pop("_comment", None)
        self.over = {**g, **user_global, **overrides}

    def key(self, w):
        """Prefer the lowercase entry; fall back to Capitalized / UPPER (names, abbreviations)."""
        for k in (w, w.capitalize(), w.upper()):
            if k in self.P or k in self.L:
                return k
        return w

    def cands(self, w, depth=0):
        k = self.key(w) if depth == 0 else w
        out = [(w, lab, p) for lab, p in self.P.get(k, [])]
        if depth < 3:
            for t in self.L.get(k, []):
                out += [(t.lower(), lab, p) for _, lab, p in self.cands(t, depth + 1)]
        return out

    def headword(self, w):
        """OALD pronunciations for w: its own entry's headword/part-of-speech prons, or for an
        inflected form (which OALD links to its base) the pron listed for exactly that form."""
        c = [(h, lab, p) for h, lab, p in self.cands(w) if " " not in p]
        if not c:
            return None
        own = [(lab, p) for h, lab, p in c if h == w]
        if own:
            # the page's first pron is its headword's; other parts of speech (noun vs verb) are the
            # only real alternatives - idiom and comparative prons on the same page are not
            pos_ = [p for lab, p in own if re.search(r"\b(noun|verb|adjective|adverb)$", lab)]
            return list(dict.fromkeys([own[0][1]] + pos_))
        exact = [p for h, lab, p in c if lab.split() and lab.split()[-1].lower() == w and not BRITISH.search(p)]
        return list(dict.fromkeys(exact)) or None   # None -> derive from the base word instead

    def lemmas(self, w):
        cvc = lambda b: re.search(r"(^|[^aeiou])[aeiou][^aeiouwxy]$", b)  # noqa: E731
        if w.endswith("'s"):
            yield w[:-2], "s"
        for suf, kind in (("ically", "ly"),):
            if w.endswith(suf):
                yield w[:-len(suf)] + "ic", kind
        if w.endswith("ies"):
            yield w[:-3] + "y", "s"
        if w.endswith("s") and not w.endswith("ss"):
            yield w[:-1], "s"                      # corpses -> corpse before corps
        if w.endswith("es") and re.search(r"(s|x|z|ch|sh|o)es$", w):
            yield w[:-2], "s"                      # losses -> loss
        if w.endswith("ied"):
            yield w[:-3] + "y", "ed"
        if w.endswith("iest"):
            yield w[:-4] + "y", "est"
        if w.endswith("ier"):
            yield w[:-3] + "y", "er"
        if w.endswith("ily"):
            yield w[:-3] + "y", "ly"
        for suf, kind in (("ing", "ing"), ("ed", "ed"), ("est", "est"), ("er", "er")):
            if w.endswith(suf) and len(w) > len(suf) + 2:
                b = w[:-len(suf)]
                if len(b) > 2 and b[-1] == b[-2] and b[-1] not in "aeiou":
                    yield b[:-1], kind                 # stopping -> stop
                if cvc(b):
                    yield b + "e", kind                # staring -> stare, not star
                    yield b, kind
                else:
                    yield b, kind
                    yield b + "e", kind
        for suf, kind in (("ly", "ly"), ("ness", "ness"), ("less", "less")):
            if w.endswith(suf) and len(w) > len(suf) + 2:
                yield w[:-len(suf)], kind

    def resolve(self, w):
        if w in self.over:
            return self.over[w], "override", []
        hp = self.headword(w)
        if hp:
            k = self.key(w)
            if len(hp) == 1 and k not in self.P:      # an inflected form: is it a plural of a noun/verb pair?
                for lem, kind in self.lemmas(w):
                    if kind != "s" or lem not in [t.lower() for t in self.L.get(k, [])]:
                        continue
                    lp = self.headword(lem) or []
                    forms = list(dict.fromkeys(hp + [add(x, kind) for x in lp]))
                    if len(forms) > 1:
                        return hp[0], "heteronym", forms
                    break
            return hp[0], ("heteronym" if len(hp) > 1 else "oald"), hp
        for lem, kind in self.lemmas(w):
            lp = ([self.over[lem]] if lem in self.over else None) or self.headword(lem)
            if lp:
                forms = list(dict.fromkeys(add(x, kind) for x in lp))
                return forms[0], ("heteronym" if len(forms) > 1 else f"derived:{lem}"), forms
        if "-" in w:
            parts = [self.resolve(p) for p in w.split("-") if p]
            if parts and all(p[0] for p in parts):
                out = []
                for k, (pp, _, _) in enumerate(parts):
                    last = k == len(parts) - 1
                    pp = pp.replace("ˈ", "ˌ") if not last else pp
                    if not re.search("[ˈˌ]", pp):
                        pp = ("ˈ" if last else "ˌ") + pp
                    out.append(pp)
                return " ".join(out), "compound", []
        return None, "missing", []


def toks(t):
    t = t.replace("’", "'").replace("‘", "'").replace("—", " ").replace("–", " ").replace("/", " ")
    out = []
    for x in re.findall(r"[^\W_]+(?:['\-][^\W_]+)*", t):
        w = x.strip("'-").lower()
        if w and (len(w) > 1 or w in ("a", "i")):
            out.append(w)
    return out


def main():
    if "--build-index" in sys.argv:
        build_index()
        return
    wd = sys.argv[1]
    terms = load(os.path.join(wd, "terms.json"), [])
    sent = load(os.path.join(wd, "sentences.json"), {})
    bo = load(os.path.join(wd, "ipa_overrides.json"), {}) or {}
    term_over = bo.get("terms", {})
    R = Resolver(bo.get("words", {}))
    words, ipa_out = {}, {}
    for t in terms:
        if t["bookmarkId"] in term_over:
            ipa_out[t["bookmarkId"]] = term_over[t["bookmarkId"]]
            continue
        ws = toks(t["term"])
        parts = []
        for w in ws:
            if w not in words:
                words[w] = R.resolve(w)
            p = STRONG[w] if len(ws) == 1 and w in STRONG else (words[w][0] or "?")
            parts.append(p)
        ipa_out[t["bookmarkId"]] = "/" + " ".join(parts) + "/" if parts else ""
    save(os.path.join(wd, "ipa.json"), ipa_out)

    def occurrences(w):
        occ = []
        for t in terms:
            if t["bookmarkId"] in term_over or w not in toks(t["term"]):
                continue
            s = sent.get(t["bookmarkId"])
            occ.append({"bookmarkId": t["bookmarkId"], "term": t["term"],
                        "sentence": (s.get("text") if isinstance(s, dict) else s) or ""})
        return occ

    unresolved = []
    for w, (p, how, cands) in sorted(words.items()):
        reason = None
        if how == "missing":
            reason = "missing"
        elif how == "heteronym":
            reason = "heteronym"
        elif p and (re.search(f"(?<![{VOWEL_BEFORE_LEN}])ː", p) or BRITISH.search(p)):
            reason = "suspicious"
        if reason:
            occ = occurrences(w)
            if occ:
                unresolved.append({"word": w, "reason": reason, "candidates": cands, "terms": occ})
    report = {
        "terms": len(terms), "unique_words": len(words),
        "from_oald": sum(1 for v in words.values() if v[1] == "oald"),
        "derived": {w: v[0] for w, v in words.items() if v[1].startswith(("derived", "compound"))},
        "unresolved": unresolved,
    }
    save(os.path.join(wd, "ipa_report.json"), report)
    print(json.dumps({k: (v if k != "derived" else f"{len(v)} (see ipa_report.json)") for k, v in report.items()},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
