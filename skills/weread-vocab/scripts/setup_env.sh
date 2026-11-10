#!/usr/bin/env bash
# Create a private dependency environment and index the user's own OALD9 file.
set -euo pipefail
cache_dir="${WEREAD_VOCAB_HOME:-$HOME/.cache/weread-vocab}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$cache_dir"
chmod 700 "$cache_dir"
if [ ! -s "$cache_dir/oald9_index.json" ]; then
  if [ -z "${WEREAD_VOCAB_OALD:-}" ]; then
    echo "Set WEREAD_VOCAB_OALD to your own OALD9.mdx file before the first setup." >&2
    exit 1
  fi
  if [ ! -x "$cache_dir/venv/bin/python" ]; then
    python3 -m venv "$cache_dir/venv"
  fi
  "$cache_dir/venv/bin/python" -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('readmdict') else 1)" \
    || "$cache_dir/venv/bin/python" -m pip install --disable-pip-version-check -q -r "$script_dir/requirements.txt"
  "$cache_dir/venv/bin/python" "$script_dir/ipa.py" --build-index
fi
command -v pandoc >/dev/null || echo "NOTE: install pandoc before building the EPUB."
echo "ready: $cache_dir"
