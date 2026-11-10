"""Merge sentence-writer output into sentences.json.

usage: merge_batches.py <workdir>

Reads every batches/batch_NN.json together with its batches/out_NN.json
({"<id>": "<sentence with **term**>"}), checks each id is covered and each
sentence bolds something, then stores the sentences as {"text", "source": "written"}.
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, save, write_batches  # noqa: E402


def main():
    wd = sys.argv[1]
    sent = load(os.path.join(wd, "sentences.json"), {})
    added, problems = 0, []
    for bp in sorted(glob.glob(os.path.join(wd, "batches", "batch_*.json"))):
        op = bp.replace("batch_", "out_")
        batch, out = load(bp, []), load(op, None)
        if out is None:
            problems.append(f"{os.path.basename(op)} is missing or not valid JSON")
            continue
        for t in batch:
            s = out.get(str(t["id"]))
            if not s or "**" not in s:
                problems.append(f"id {t['id']} ({t['term']!r}) in {os.path.basename(op)}: missing or no **bold**")
                continue
            sent[t["bookmarkId"]] = {"text": s.strip(), "source": "written"}
            added += 1
    save(os.path.join(wd, "sentences.json"), sent)
    # word senses the writers were unsure of, kept so they can be reported (and re-reported later)
    unsure = load(os.path.join(wd, "uncertain.json"), {})
    for bp in sorted(glob.glob(os.path.join(wd, "batches", "batch_*.json"))):
        ids = {str(t["id"]): t for t in load(bp, [])}
        for u in load(bp.replace("batch_", "unsure_"), []) or []:
            t = ids.get(str(u.get("id")))
            if t:
                unsure[t["bookmarkId"]] = {"term": t["term"], "chapter": t["chapter"], "why": u.get("why", "")}
    save(os.path.join(wd, "uncertain.json"), unsure)
    pending, batches = write_batches(wd)   # only the gaps remain
    print(json.dumps({"added": added, "problems": problems, "still_pending": len(pending), "batches": batches,
                      "uncertain_total": len(unsure)},
                     ensure_ascii=False, indent=1))
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
