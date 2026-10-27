"""
nifti-prep: step 1 of the Clinical Brain MRI Volumetrics workflow.

Takes whatever structural MRI the user selected (NIfTI .nii/.nii.gz in any
folder layout, optionally DICOM series folders), and normalises it for the
steps that follow:

  * every NIfTI is read with nibabel; non-3D volumes are rejected (a 4D file
    with a single volume is squeezed), unreadable files are logged and skipped
  * images are reoriented to RAS canonical (nib.as_closest_canonical); dtype
    and intensities are kept, the output is always gzip .nii.gz
  * modality is read from the file name (BIDS suffix, or a clue such as
    mprage/flair/t2 anywhere in the stem); DEFAULT_MODALITY covers the rest
  * DICOM folders are converted with dcm2niix first, when it is available
  * BIDS JSON sidecars are copied along, and metadata/images.json describes
    every output image

Outputs (OUTPUT_DIR/<name>/):
  t1w/<sub>_T1w.nii.gz        flair/<sub>_FLAIR.nii.gz
  other/<sub>_T2w.nii.gz      metadata/<sub>_<Suffix>.json, metadata/images.json
  t1w/provenance.json         (the first output holds the provenance)
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import nibabel as nib
import numpy as np

from processor.bids_names import NIFTI_EXTS, output_dir_for, strip_nifti_ext

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("processor-nifti-prep")
# The Lambda runtime configures the root logger before basicConfig can, at
# WARNING, so the per-subject INFO lines would be lost without this.
log.setLevel(logging.INFO)

APP = "processor-nifti-prep"
INPUT = "images"
OUTPUTS = ["t1w", "flair", "other", "metadata"]
MODALITIES = ("T1w", "FLAIR", "T2w")
MODALITY_OUTPUT = {"T1w": "t1w", "FLAIR": "flair", "T2w": "other"}
DEFAULT_MODALITY_VALUES = MODALITIES + ("skip",)
SIDECAR_FIELDS = (
    "MagneticFieldStrength",
    "Manufacturer",
    "ManufacturersModelName",
    "RepetitionTime",
    "EchoTime",
    "InversionTime",
    "FlipAngle",
)
DICOM_EXTS = (".dcm", ".dicom", ".ima")
# dcm2niix flags, as documented; the output folder and input folder follow.
DCM2NIIX_ARGS = ["-z", "y", "-b", "y", "-f", "%p_%s"]


def version() -> str:
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
    except OSError:
        return "unknown"


# --- naming ----------------------------------------------------------------

_T1_CLUES = re.compile(r"mp2rage|mprage|(?<![a-z0-9])t1(?![0-9])")
_T2_CLUES = re.compile(r"(?<![a-z0-9])t2(?![0-9])")
_FLAIR_CLUES = re.compile(r"flair")


def classify(stem: str) -> tuple[str | None, str]:
    """Modality from a file stem: ('T1w', 'suffix') when the BIDS suffix says so,
    ('FLAIR', 'name') when a clue sits anywhere in the stem, (None, 'none') otherwise.
    FLAIR wins over a 't2' clue, since FLAIR files are often named t2_flair."""
    suffix = stem.split("_")[-1].lower()
    for modality in MODALITIES:
        if suffix == modality.lower():
            return modality, "suffix"
    low = stem.lower()
    if _FLAIR_CLUES.search(low):
        return "FLAIR", "name"
    if _T1_CLUES.search(low):
        return "T1w", "name"
    if _T2_CLUES.search(low):
        return "T2w", "name"
    return None, "none"


def sanitize(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "", label)


def subject_of(stem: str) -> str:
    """Text before the first '_'; a stem without '_' is used whole. Either way
    sanitised to [A-Za-z0-9-], and 'image' when nothing is left."""
    return sanitize(stem.split("_")[0]) or "image"


class Names:
    """Hands out output names, disambiguating collisions by appending the run
    to the subject label: sub-01_T1w, sub-01run2_T1w, sub-01run3_T1w, ..."""

    def __init__(self):
        self.taken: set[str] = set()

    def claim(self, subject: str, modality: str) -> tuple[str, str]:
        label, run = subject, 1
        while f"{label}_{modality}" in self.taken:
            run += 1
            label = f"{subject}run{run}"
        stem = f"{label}_{modality}"
        self.taken.add(stem)
        return label, stem


# --- finding inputs ----------------------------------------------------------


def is_dicom_file(path: Path) -> bool:
    if path.name.lower().endswith(DICOM_EXTS):
        return True
    if path.name.lower().endswith(NIFTI_EXTS + (".json", ".txt", ".csv", ".tsv", ".md")):
        return False
    try:
        if path.stat().st_size < 132:
            return False
        with open(path, "rb") as fh:
            fh.seek(128)
            return fh.read(4) == b"DICM"
    except OSError:
        return False


def scan(base: Path) -> tuple[list[Path], list[Path]]:
    """All NIfTI files below base, and the DICOM trees: the highest folders that
    hold DICOM files (anywhere beneath) and no NIfTI at all, so one dcm2niix run
    per tree (dcm2niix recurses into series subfolders itself)."""
    niftis: list[Path] = []
    nifti_below: dict[Path, int] = {}
    dicom_below: dict[Path, int] = {}
    for dirpath, dirnames, filenames in os.walk(base, topdown=False, followlinks=True):
        d = Path(dirpath)
        dirnames[:] = [x for x in dirnames if not x.startswith(".")]
        n_nifti = n_dicom = 0
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            p = d / name
            if not p.is_file():
                continue
            if name.endswith(NIFTI_EXTS):
                niftis.append(p)
                n_nifti += 1
            elif is_dicom_file(p):
                n_dicom += 1
        for sub in dirnames:
            n_nifti += nifti_below.get(d / sub, 0)
            n_dicom += dicom_below.get(d / sub, 0)
        nifti_below[d] = n_nifti
        dicom_below[d] = n_dicom

    roots: list[Path] = []

    def subdirs(d: Path) -> list[Path]:
        return sorted(x for x in d.iterdir() if x.is_dir() and not x.name.startswith("."))

    def pick(d: Path):
        if dicom_below.get(d, 0) == 0:
            return
        if nifti_below.get(d, 0) == 0:
            # A pure DICOM tree. Step down through wrapper folders (no files, one
            # child) so the folder that names the tree is the patient's, not the
            # input root around it.
            while not any(x.is_file() for x in d.iterdir()) and len(subdirs(d)) == 1:
                d = subdirs(d)[0]
            roots.append(d)
            return
        for sub in subdirs(d):
            pick(sub)

    pick(base)
    return sorted(niftis), roots


def dcm2niix_binary() -> tuple[str | None, str | None]:
    """The dcm2niix binary shipped by the pip package, and its version; (None, None)
    when the package is not installed."""
    try:
        import dcm2niix  # type: ignore
    except ImportError:
        return None, None
    binary = getattr(dcm2niix, "bin", None)
    if not binary or not os.path.exists(binary):
        return None, None
    return binary, getattr(dcm2niix, "__version__", "unknown")


def convert_dicom(binary: str, tree: Path, workdir: Path) -> tuple[list[Path], list[str]]:
    """Runs dcm2niix over a DICOM tree into its own folder under workdir; returns
    the NIfTI files it wrote and the argv used."""
    out = Path(tempfile.mkdtemp(prefix="dcm_", dir=workdir))
    argv = [binary, *DCM2NIIX_ARGS, "-o", str(out), str(tree)]
    log.info("dcm2niix: %s", " ".join(argv[1:]))
    result = subprocess.run(argv, check=True, capture_output=True, text=True)
    for line in result.stdout.splitlines():
        log.info("  dcm2niix: %s", line)
    return sorted(p for p in out.rglob("*") if p.is_file() and p.name.endswith(NIFTI_EXTS)), argv


# --- the work ----------------------------------------------------------------


def load_raw(path: Path) -> tuple[np.ndarray, nib.Nifti1Image, float, float]:
    """The image's on-disk array (unscaled, so the dtype is the file's), the
    image, and the scl_slope/scl_inter that turn it into intensities. Trailing
    singleton dimensions are squeezed; anything else non-3D raises ValueError.
    Working on the unscaled array keeps memory at the file's size, where the
    scaled read would go through float64."""
    img = nib.load(str(path))
    proxy = img.dataobj
    raw = proxy.get_unscaled() if hasattr(proxy, "get_unscaled") else np.asanyarray(proxy)
    slope = getattr(proxy, "slope", None)
    inter = getattr(proxy, "inter", None)
    slope = 1.0 if slope is None or not np.isfinite(slope) or slope == 0 else float(slope)
    inter = 0.0 if inter is None or not np.isfinite(inter) else float(inter)
    while raw.ndim > 3 and raw.shape[-1] == 1:
        raw = raw[..., 0]
    if raw.ndim != 3:
        raise ValueError(f"not a 3D volume: shape {tuple(img.shape)}")
    return raw, img, slope, inter


def sidecar_for(path: Path) -> Path | None:
    cand = path.parent / (strip_nifti_ext(path.name) + ".json")
    return cand if cand.is_file() else None


def read_sidecar(path: Path) -> dict | None:
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        log.warning("sidecar %s is not readable JSON (%s); not copied", path, exc)
        return None
    return data if isinstance(data, dict) else None


def prepare(source: Path, modality: str, label: str, stem: str, out_dir: Path) -> tuple[Path, dict]:
    """Reorient one image to RAS and write it as <stem>.nii.gz under out_dir;
    returns the output path and the images.json record (minus the naming fields)."""
    raw, img, slope, inter = load_raw(source)
    original = "".join(nib.aff2axcodes(img.affine))
    # A header copy keeps the file's datatype; the scaling is put back after
    # the reorientation, so the written voxels and intensities are the file's.
    canon = nib.as_closest_canonical(nib.Nifti1Image(raw, img.affine, img.header))
    canon.header.set_data_dtype(raw.dtype)
    if slope != 1.0 or inter != 0.0:
        canon.header.set_slope_inter(slope, inter)
    out = out_dir / f"{stem}.nii.gz"
    nib.save(canon, str(out))
    data = np.asanyarray(canon.dataobj)
    if np.issubdtype(data.dtype, np.floating):
        lo, hi, mean = float(np.nanmin(data)), float(np.nanmax(data)), float(np.nanmean(data))
    else:
        lo, hi, mean = float(data.min()), float(data.max()), float(data.mean())
    lo, hi, mean = (lo * slope + inter, hi * slope + inter, mean * slope + inter)
    if slope < 0:
        lo, hi = hi, lo
    record = {
        "shape": [int(x) for x in canon.shape],
        "voxelSizeMm": [round(float(z), 6) for z in canon.header.get_zooms()[:3]],
        "dtype": str(canon.get_data_dtype()),
        "originalOrientation": original,
        "orientation": "".join(nib.aff2axcodes(canon.affine)),
        "intensity": {"min": lo, "max": hi, "mean": mean},
    }
    return out, record


def main() -> int:
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    input_dir = os.environ.get("INPUT_DIR")
    output_dir = os.environ.get("OUTPUT_DIR")
    default_modality = (os.environ.get("DEFAULT_MODALITY") or "T1w").strip()
    if not input_dir or not output_dir:
        log.error("INPUT_DIR and OUTPUT_DIR must be set")
        return 2
    if not os.path.isdir(input_dir):
        log.error("INPUT_DIR %s is not a folder", input_dir)
        return 2
    if default_modality not in DEFAULT_MODALITY_VALUES:
        log.error("DEFAULT_MODALITY must be one of %s, not %r", ", ".join(DEFAULT_MODALITY_VALUES), default_modality)
        return 2

    out_dirs = {name: output_dir_for(output_dir, OUTPUTS, name) for name in OUTPUTS}
    metadata_dir = out_dirs["metadata"]

    base = Path(input_dir) / INPUT
    if not base.is_dir():
        base = Path(input_dir)
    log.info("running as a %s; looking for images under %s", "lambda" if os.environ.get("AWS_LAMBDA_FUNCTION_NAME") else "container", base)

    niftis, dicom_trees = scan(base)
    log.info("found %d NIfTI file(s) and %d DICOM folder(s)", len(niftis), len(dicom_trees))

    # Candidates: (file, origin dict, dcm2niix argv or None)
    candidates: list[tuple[Path, dict, list[str] | None]] = [
        (p, {"name": p.name, "path": str(p.relative_to(Path(input_dir))), "origin": "nifti"}, None) for p in niftis
    ]
    tool_versions = {"nibabel": nib.__version__, "numpy": np.__version__}
    workdir = Path(tempfile.mkdtemp(prefix="nifti-prep-"))
    failures: list[dict] = []
    if dicom_trees:
        binary, dcm_version = dcm2niix_binary()
        if binary is None:
            log.warning("dcm2niix is not installed; skipping %d DICOM folder(s): %s", len(dicom_trees), ", ".join(str(t) for t in dicom_trees))
            for tree in dicom_trees:
                failures.append({"path": str(tree.relative_to(Path(input_dir))) or ".", "reason": "DICOM folder, dcm2niix not available"})
        else:
            tool_versions["dcm2niix"] = dcm_version
            for tree in dicom_trees:
                rel = str(tree.relative_to(Path(input_dir))) or "."
                # The protocol name dcm2niix puts in the file name is not a subject,
                # so DICOM-derived images take their subject from the folder name.
                subject = sanitize(tree.name) if tree != base else "dicom"
                try:
                    files, argv = convert_dicom(binary, tree, workdir)
                except (subprocess.CalledProcessError, OSError) as exc:
                    detail = getattr(exc, "stderr", "") or getattr(exc, "stdout", "") or str(exc)
                    log.error("dcm2niix failed on %s: %s", rel, str(detail).strip()[-500:])
                    failures.append({"path": rel, "reason": f"dcm2niix failed: {str(detail).strip()[-200:]}"})
                    continue
                if not files:
                    log.warning("dcm2niix wrote no NIfTI for %s", rel)
                    failures.append({"path": rel, "reason": "dcm2niix produced no NIfTI"})
                    continue
                log.info("dcm2niix converted %s into %d image(s)", rel, len(files))
                for f in files:
                    candidates.append((f, {"name": f.name, "path": f"{rel}/{f.name}", "origin": "dicom", "dicomFolder": rel, "subject": subject or "dicom"}, argv))

    if not candidates and not failures:
        everything = sorted(str(p.relative_to(base)) for p in base.rglob("*") if p.is_file())
        log.error("no NIfTI (*.nii, *.nii.gz) or DICOM input found under %s. Files present: %s", base, ", ".join(everything) or "(none)")
        shutil.rmtree(workdir, ignore_errors=True)
        return 1

    names = Names()
    images: list[dict] = []
    per_subject: dict[str, dict] = {}
    skipped: list[dict] = []
    for path, origin, argv in candidates:
        stem = strip_nifti_ext(path.name)
        subject = origin.get("subject") or subject_of(stem)
        modality, how = classify(stem)
        if modality is None:
            if default_modality == "skip":
                log.warning("%s: no modality in the name, DEFAULT_MODALITY=skip; dropped", origin["path"])
                skipped.append({"path": origin["path"], "reason": "no modality in the file name (DEFAULT_MODALITY=skip)"})
                continue
            modality, how = default_modality, "default"
            log.info("%s: no modality in the name; using DEFAULT_MODALITY=%s", origin["path"], modality)
        label, out_stem = names.claim(subject, modality)
        if label != subject:
            log.warning("%s: %s_%s already taken; writing it as %s", origin["path"], subject, modality, out_stem)
        out_name = MODALITY_OUTPUT[modality]
        t_sub = time.monotonic()
        try:
            out_path, record = prepare(path, modality, label, out_stem, out_dirs[out_name])
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the cohort
            log.error("%s: skipped, %s: %s", origin["path"], type(exc).__name__, exc)
            failures.append({"path": origin["path"], "reason": f"{type(exc).__name__}: {exc}"})
            names.taken.discard(out_stem)
            continue
        rel_out = f"{out_name}/{out_path.name}"
        outputs = [rel_out]
        sidecar_rel = None
        sidecar_fields = None
        sidecar = sidecar_for(path)
        if sidecar is not None:
            data = read_sidecar(sidecar)
            if data is not None:
                target = metadata_dir / f"{out_stem}.json"
                shutil.copyfile(sidecar, target)
                sidecar_rel = f"metadata/{target.name}"
                outputs.append(sidecar_rel)
                sidecar_fields = {k: data[k] for k in SIDECAR_FIELDS if k in data}
        entry = {
            "subject": label,
            "modality": modality,
            "modalityFrom": how,
            "output": rel_out,
            "source": origin,
            **record,
            "sidecar": sidecar_rel,
            "sidecarFields": sidecar_fields,
        }
        images.append(entry)
        seconds = round(time.monotonic() - t_sub, 3)
        log.info(
            "%s: %s -> %s (%s, shape %s, %s -> %s, %.1fs)",
            label, origin["path"], rel_out, modality, "x".join(map(str, record["shape"])), record["originalOrientation"], record["orientation"], seconds,
        )
        ps = per_subject.setdefault(label, {"subject": label, "inputs": [], "outputs": [], "command": [], "seconds": 0.0})
        ps["inputs"].append(origin["path"])
        if sidecar is not None and sidecar_rel is not None:
            ps["inputs"].append(str(sidecar.relative_to(Path(input_dir))) if origin["origin"] == "nifti" else f"{origin['dicomFolder']}/{sidecar.name}")
        ps["outputs"].extend(outputs)
        ps["command"] = argv or ["nibabel.as_closest_canonical", origin["path"]]
        ps["seconds"] = round(ps["seconds"] + seconds, 3)

    shutil.rmtree(workdir, ignore_errors=True)

    with open(metadata_dir / "images.json", "w") as fh:
        json.dump(images, fh, indent=2)
    finished = datetime.now(timezone.utc)
    provenance = {
        "app": APP,
        "appVersion": version(),
        "tool": "nibabel",
        "toolVersion": nib.__version__,
        "toolVersions": tool_versions,
        "startedAt": started.isoformat(),
        "finishedAt": finished.isoformat(),
        "parameters": {"DEFAULT_MODALITY": default_modality},
        "subjects": list(per_subject.values()),
        "skipped": skipped,
        "failed": failures,
    }
    with open(out_dirs[OUTPUTS[0]] / "provenance.json", "w") as fh:
        json.dump(provenance, fh, indent=2)

    log.info(
        "done: %d image(s) written for %d subject(s), %d skipped, %d failed, in %.1fs",
        len(images), len(per_subject), len(skipped), len(failures), time.monotonic() - t0,
    )
    if not images:
        log.error("no image was written (%d failed, %d skipped)", len(failures), len(skipped))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
