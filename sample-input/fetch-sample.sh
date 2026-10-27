#!/bin/sh
# Fills data/input/ with four CC0 images from OpenNeuro (about 75 MB), or from
# a local copy when SAMPLE_DIR is set. Run from the repo root.
set -eu
cd "$(dirname "$0")/.."
FILES="ds000117/sub-01/anat/sub-01_T1w.nii.gz
ds000117/sub-01/anat/sub-01_T1w.json
ds000117/sub-02/anat/sub-02_T1w.nii.gz
ds000221/sub-010200/anat/sub-010200_T1w.nii.gz
ds000221/sub-010200/anat/sub-010200_FLAIR.nii.gz
ds000221/sub-010200/anat/sub-010200_FLAIR.json"
for f in $FILES; do
  mkdir -p "data/input/$(dirname "$f")"
  if [ -n "${SAMPLE_DIR:-}" ]; then
    cp "$SAMPLE_DIR/$f" "data/input/$f"
  else
    aws s3 cp --no-sign-request "s3://openneuro.org/$f" "data/input/$f"
  fi
done
echo "data/input/ ready:"
find data/input -type f | sort
