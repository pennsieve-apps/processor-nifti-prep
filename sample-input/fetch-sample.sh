#!/bin/sh
# Fills data/input/ with four CC0 images from OpenNeuro (about 75 MB), or from
# a local copy when SAMPLE_DIR is set (the layout workflow-clinical-brain-mri's
# scripts/fetch-sample-data.sh writes). Run from the repo root.
# Each line: the file's local name, then its path on OpenNeuro, where the
# images sit in a session folder (ses-mri/, ses-01/).
set -eu
cd "$(dirname "$0")/.."
FILES="ds000117/sub-01/anat/sub-01_T1w.nii.gz ds000117/sub-01/ses-mri/anat/sub-01_ses-mri_T1w.nii.gz
ds000117/sub-01/anat/sub-01_T1w.json ds000117/sub-01/ses-mri/anat/sub-01_ses-mri_T1w.json
ds000117/sub-02/anat/sub-02_T1w.nii.gz ds000117/sub-02/ses-mri/anat/sub-02_ses-mri_T1w.nii.gz
ds000221/sub-010200/anat/sub-010200_T1w.nii.gz ds000221/sub-010200/ses-01/anat/sub-010200_ses-01_acq-mp2rage_T1w.nii.gz
ds000221/sub-010200/anat/sub-010200_FLAIR.nii.gz ds000221/sub-010200/ses-01/anat/sub-010200_ses-01_acq-highres_FLAIR.nii.gz
ds000221/sub-010200/anat/sub-010200_FLAIR.json ds000221/sub-010200/ses-01/anat/sub-010200_ses-01_acq-highres_FLAIR.json"
echo "$FILES" | while read -r f src; do
  mkdir -p "data/input/$(dirname "$f")"
  if [ -n "${SAMPLE_DIR:-}" ]; then
    cp "$SAMPLE_DIR/$f" "data/input/$f"
  else
    aws s3 cp --no-sign-request "s3://openneuro.org/$src" "data/input/$f"
  fi
done
echo "data/input/ ready:"
find data/input -type f | sort
