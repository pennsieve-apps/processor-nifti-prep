# processor-nifti-prep

Step 1 of the **Clinical Brain MRI Volumetrics** workflow. It takes whatever
structural MRI the user selected (NIfTI `.nii`/`.nii.gz` in any folder layout,
optionally DICOM series folders) and normalises it for the steps that follow:

- every NIfTI is read with [nibabel](https://nipy.org/nibabel/); non-3D volumes
  are rejected (a 4D file with one volume is squeezed), unreadable files are
  logged and skipped;
- each image is reoriented to RAS canonical (`nib.as_closest_canonical`): only
  the orientation changes, the dtype and intensities are kept; the output is
  always gzip `.nii.gz`;
- the modality is read from the file name and the image is sorted into
  `t1w/`, `flair/` or `other/` under a BIDS-derivatives name,
  `<sub>_T1w.nii.gz`, `<sub>_FLAIR.nii.gz`, `<sub>_T2w.nii.gz`;
- DICOM folders are converted first with
  [dcm2niix](https://github.com/rordenlab/dcm2niix) and then treated like
  NIfTI inputs;
- BIDS JSON sidecars are copied to `metadata/`, and `metadata/images.json`
  describes every output image.

## Inputs

| Input | Takes | Found in |
|---|---|---|
| `images` | NIfTI (`application/x-nifti-gz`, `application/x-nifti`) and/or DICOM (`application/dicom`) | `INPUT_DIR/images/` if that folder exists, else all of `INPUT_DIR`, searched recursively |

Any layout works: a BIDS tree, a flat folder, a patient folder of DICOM series.
A folder counts as DICOM when it holds files with a `.dcm`/`.dicom`/`.ima`
extension or the `DICM` magic at byte 128 and no NIfTI anywhere beneath; one
`dcm2niix` run covers the whole folder (`dcm2niix -z y -b y -f %p_%s`), and the
folder's name is the subject label, since the protocol name dcm2niix puts in
the file name is not a subject. Wrapper folders with no files and a single
child are stepped through, so `INPUT_DIR/PATIENT7/{T1_MPRAGE,FLAIR}/` yields
`PATIENT7_T1w.nii.gz` and `PATIENT7_FLAIR.nii.gz`.

## Outputs

With four outputs, each is written to its own folder:

| Output | Holds |
|---|---|
| `t1w` | `<sub>_T1w.nii.gz`, RAS; and `provenance.json` for the run |
| `flair` | `<sub>_FLAIR.nii.gz`, RAS |
| `other` | `<sub>_T2w.nii.gz` and anything that is neither T1w nor FLAIR, RAS |
| `metadata` | `images.json` (one record per output image) and the copied BIDS sidecars, `<sub>_<Suffix>.json` |

Every folder is created, even when it stays empty.

### Naming

The **subject label** is the text before the first `_` of the file name
(`sub-01_T1w.nii.gz` → `sub-01`); a name without `_` is used whole
(`mprage.nii.gz` → `mprage`). Either way it is reduced to `[A-Za-z0-9-]`. When
two files map to the same output name the run is appended to the subject
label: `sub-01_T1w.nii.gz`, `sub-01run2_T1w.nii.gz`, ... and the log says so.

The **modality** comes from the BIDS suffix when there is one (`_T1w`,
`_FLAIR`, `_T2w`, case-insensitive), else from a clue anywhere in the stem:
`t1`/`T1`/`mprage`/`mp2rage` → T1w, `flair` → FLAIR, `t2` → T2w (FLAIR wins
over `t2`, since FLAIR series are often called `t2_flair`). A file with no clue
gets `DEFAULT_MODALITY`, and the log says so.

### `metadata/images.json`

A list with, per output image: `subject`, `modality`, `modalityFrom`
(`suffix`, `name` or `default`), `output` (relative to `OUTPUT_DIR`), `source`
(`name`, `path` relative to `INPUT_DIR`, `origin` nifti/dicom), `shape`,
`voxelSizeMm`, `dtype`, `originalOrientation` and `orientation` (axcodes, e.g.
`LPS` → `RAS`), `intensity` (`min`, `max`, `mean`), `sidecar` (the copied
sidecar's path, or null) and `sidecarFields` (MagneticFieldStrength,
Manufacturer, ManufacturersModelName, RepetitionTime, EchoTime, InversionTime,
FlipAngle, when the sidecar has them).

### `t1w/provenance.json`

`app`, `appVersion`, `tool`/`toolVersion` (nibabel) plus `toolVersions`
(nibabel, numpy, dcm2niix when used), `startedAt`/`finishedAt`, `parameters`,
`subjects` (per subject: inputs, outputs, command, seconds), and the files
`skipped` (by `DEFAULT_MODALITY=skip`) or `failed` (unreadable, not 3D,
dcm2niix errors), each with a reason.

## Parameters

| Parameter | Default | |
|---|---|---|
| `DEFAULT_MODALITY` | `T1w` | What to do with an image whose name carries no modality clue: `T1w`, `FLAIR`, `T2w`, or `skip` to drop it |

## Exit codes

`0` when at least one image was written (a file that fails is logged and the
others continue; failures are listed in `provenance.json`); `1` when no input
file was found (the log lists what was there) or every file failed; `2` on a
bad `DEFAULT_MODALITY` or a missing `INPUT_DIR`/`OUTPUT_DIR`.

## Runtimes

`runtime`: 1 vCPU, 4 GiB, 900 s timeout, `computeTypes: [standard, lambda]`.
The image runs either way:

- **standard** (the default): an ECS task. `INPUT_DIR`, `OUTPUT_DIR` and the
  parameters are environment variables, and `main()` runs once.
- **lambda**: a workflow step set to run as a Lambda function. Lambda sets
  `AWS_LAMBDA_RUNTIME_API`, so `processor/entry.py` hands over to the Lambda
  runtime interface client, and `processor/handler.py` takes `inputDir`,
  `outputDir` and `DEFAULT_MODALITY` from the payload and runs the same
  `main()`. A failed run raises, so the step fails.

The four OpenNeuro sample images (three T1w, one FLAIR, up to 192×512×512)
take about 3 s and under 500 MB in the container (amd64 emulation on an arm64 Mac).

## Running it locally

```sh
sh sample-input/fetch-sample.sh   # four CC0 images from OpenNeuro into data/input/ (gitignored)
make test                         # pytest, then Python over data/input/, then checks on data/output/
make run                          # the same in Docker (amd64; DOCKER_DEFAULT_PLATFORM is set by the Makefile)
```

`make test` needs Python 3.11+ with `requirements.txt` and pytest installed
(`PYTHON=/path/to/venv/bin/python make test` picks an interpreter). The unit
tests in `tests/test_main.py` build synthetic volumes with an LPS affine and
check the reorientation, naming, sidecars, collisions, `DEFAULT_MODALITY`,
the Lambda handler and the DICOM folder detection; they need no sample data.

As a Lambda function, with the [runtime interface emulator](https://github.com/aws/aws-lambda-runtime-interface-emulator)
(`aws-lambda-rie` for your machine's architecture) in the image's place:

```sh
docker build -t processor-nifti-prep .
docker run --rm -p 9000:8080 -v "$PWD/data/input:/in:ro" -v "$PWD/aws-lambda-rie:/rie:ro" \
  --entrypoint /rie processor-nifti-prep python3 -m processor.entry
curl -XPOST localhost:9000/2015-03-31/functions/function/invocations \
  -d '{"inputDir": "/in", "outputDir": "/tmp/out", "DEFAULT_MODALITY": "T1w"}'
```

## Citations

If DICOM input was converted, cite dcm2niix:

Li X, Morgan PS, Ashburner J, Smith J, Rorden C. (2016). The first step for
neuroimaging data analysis: DICOM to NIfTI conversion. *J Neurosci Methods*
264:47-56. doi:10.1016/j.jneumeth.2016.03.001

See `THIRD_PARTY.md` for the licences of nibabel, numpy, dcm2niix and the
base image.

## Licence

Apache-2.0, copyright 2026 Pennsieve.
