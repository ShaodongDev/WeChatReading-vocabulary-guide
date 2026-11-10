"""Show where a book stands and refresh the pending batches (run after editing sentences.json by hand).

usage: status.py <workdir>
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, write_batches  # noqa: E402

wd = sys.argv[1]
terms = load(os.path.join(wd, "terms.json"), [])
sent = load(os.path.join(wd, "sentences.json"), {})
ipa = load(os.path.join(wd, "ipa.json"), {})
src = lambda v: v.get("source") if isinstance(v, dict) else "written"  # noqa: E731
ids = {t["bookmarkId"] for t in terms}
pending, batches = write_batches(wd)
print(json.dumps({
    "terms": len(terms),
    "sentences_from_book": sum(1 for b in ids if b in sent and src(sent[b]) == "book"),
    "sentences_written": sum(1 for b in ids if b in sent and src(sent[b]) == "written"),
    "terms_needing_sentences": len(pending), "batches": batches,
    "ipa": ("not run yet: python3 scripts/ipa.py <workdir>" if not ipa else
            {"unresolved_terms": [t["term"] for t in terms if "?" in ipa.get(t["bookmarkId"], "?")][:20]}),
    "uncertain": list(load(os.path.join(wd, "uncertain.json"), {}).values()),
}, ensure_ascii=False, indent=1))
