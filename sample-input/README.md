# Sample input

NIfTI files are not committed (they are big, and `*.nii*` is gitignored).
`fetch-sample.sh` downloads four CC0 images from OpenNeuro into `data/input/`,
in their BIDS layout, with the sidecars that exist there:

| Dataset | File | Role |
|---|---|---|
| ds000117 | `sub-01/anat/sub-01_T1w.nii.gz` (+ `.json`) | T1w, 1 mm, int16 |
| ds000117 | `sub-02/anat/sub-02_T1w.nii.gz` | T1w, second subject |
| ds000221 | `sub-010200/anat/sub-010200_T1w.nii.gz` | T1w, float32 |
| ds000221 | `sub-010200/anat/sub-010200_FLAIR.nii.gz` (+ `.json`) | FLAIR for the same subject |

```sh
sh sample-input/fetch-sample.sh          # needs the AWS CLI; no credentials (--no-sign-request)
make test                                # Python, over data/input/
make run                                 # the same in Docker
```

With `SAMPLE_DIR=/path/to/a/local/copy` the script copies from there instead
of downloading.
