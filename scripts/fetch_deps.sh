#!/bin/sh
# Clone the upstream codecs at the revisions pinned in third_party/REVISIONS.
set -e
cd "$(dirname "$0")/../third_party"
while IFS="	" read -r name url rev; do
  [ -d "$name/.git" ] || git clone "$url" "$name"
  git -C "$name" fetch -q origin "$rev"
  git -C "$name" checkout -q "$rev"
done < REVISIONS
