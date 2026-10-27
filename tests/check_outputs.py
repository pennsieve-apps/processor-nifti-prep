"""Checks a run over the sample data (make test / make run): the expected files
exist, are RAS, keep their dtype, and images.json + provenance.json describe them."""
import json
import sys
from pathlib import Path

import nibabel as nib

EXPECTED = {
    "t1w/sub-01_T1w.nii.gz": ((192, 256, 256), "int16"),
    "t1w/sub-02_T1w.nii.gz": ((192, 256, 256), "int16"),
    "t1w/sub-010200_T1w.nii.gz": ((176, 240, 256), "float32"),
    "flair/sub-010200_FLAIR.nii.gz": ((192, 512, 512), "int16"),
}


def main(out: Path) -> int:
    problems = []
    for name in ("t1w", "flair", "other", "metadata"):
        if not (out / name).is_dir():
            problems.append(f"missing output folder {name}/")
    for rel, (shape, dtype) in EXPECTED.items():
        p = out / rel
        if not p.is_file():
            problems.append(f"missing {rel}")
            continue
        img = nib.load(str(p))
        axes = "".join(nib.aff2axcodes(img.affine))
        if axes != "RAS":
            problems.append(f"{rel}: orientation {axes}, not RAS")
        if tuple(img.shape) != shape:
            problems.append(f"{rel}: shape {tuple(img.shape)}, expected {shape}")
        if str(img.get_data_dtype()) != dtype:
            problems.append(f"{rel}: dtype {img.get_data_dtype()}, expected {dtype}")
        print(f"ok  {rel}  {axes}  {tuple(img.shape)}  {img.get_data_dtype()}")
    for rel in ("metadata/sub-01_T1w.json", "metadata/sub-010200_FLAIR.json"):
        if not (out / rel).is_file():
            problems.append(f"missing sidecar copy {rel}")
    images = json.loads((out / "metadata" / "images.json").read_text())
    if sorted(r["output"] for r in images) != sorted(EXPECTED):
        problems.append(f"images.json lists {sorted(r['output'] for r in images)}")
    if any(r["orientation"] != "RAS" for r in images):
        problems.append("images.json: an image is not RAS")
    prov = json.loads((out / "t1w" / "provenance.json").read_text())
    if sorted(s["subject"] for s in prov["subjects"]) != ["sub-01", "sub-010200", "sub-02"]:
        problems.append(f"provenance subjects: {[s['subject'] for s in prov['subjects']]}")
    if prov["failed"] or prov["skipped"]:
        problems.append(f"provenance reports failed={prov['failed']} skipped={prov['skipped']}")
    for p in problems:
        print("FAIL", p)
    print("all checks passed" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "data/output")))
