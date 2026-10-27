"""Unit tests: synthetic 3D NIfTI volumes with an LPS affine, run through main()."""
import json
import os
import sys
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from processor import main as app  # noqa: E402
from processor.main import Names, classify, subject_of  # noqa: E402

LPS = np.array([[-1.0, 0, 0, 10], [0, -1.5, 0, 20], [0, 0, 2.0, -5], [0, 0, 0, 1]])


def volume(shape=(4, 5, 6), affine=LPS, dtype=np.int16, seed=0):
    rng = np.random.default_rng(seed)
    data = rng.integers(0, 1000, size=shape).astype(dtype)
    return nib.Nifti1Image(data, affine)


def run(tmp_path, files, default_modality=None, monkeypatch=None):
    """Writes {relative path: image-or-text} under INPUT_DIR and runs main(); returns (exit code, OUTPUT_DIR)."""
    inp, out = tmp_path / "in", tmp_path / "out"
    for rel, content in files.items():
        p = inp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            p.write_text(content)
        else:
            nib.save(content, str(p))
    monkeypatch.setenv("INPUT_DIR", str(inp))
    monkeypatch.setenv("OUTPUT_DIR", str(out))
    if default_modality is None:
        monkeypatch.delenv("DEFAULT_MODALITY", raising=False)
    else:
        monkeypatch.setenv("DEFAULT_MODALITY", default_modality)
    return app.main(), out


def images_json(out):
    return json.loads((out / "metadata" / "images.json").read_text())


# --- naming helpers ----------------------------------------------------------


@pytest.mark.parametrize(
    "stem, expected",
    [
        ("sub-01_T1w", ("T1w", "suffix")),
        ("sub-01_acq-mprage_run-2_T1w", ("T1w", "suffix")),
        ("sub-01_FLAIR", ("FLAIR", "suffix")),
        ("sub-01_t2w", ("T2w", "suffix")),
        ("MPRAGE_sag", ("T1w", "name")),
        ("patient7-mp2rage", ("T1w", "name")),
        ("T1", ("T1w", "name")),
        ("t1_mprage_sag_p2", ("T1w", "name")),
        ("t2_tirm_flair_3", ("FLAIR", "name")),
        ("axial-T2-tse", ("T2w", "name")),
        ("sub-010200_brain", (None, "none")),
        ("t12_scan", (None, "none")),
        ("sub-01", (None, "none")),
    ],
)
def test_classify(stem, expected):
    assert classify(stem) == expected


def test_subject_of():
    assert subject_of("sub-01_T1w") == "sub-01"
    assert subject_of("sub-010200_desc-x_FLAIR") == "sub-010200"
    assert subject_of("Patient 7 (T1)") == "Patient7T1"
    assert subject_of("___") == "image"


def test_names_disambiguate():
    names = Names()
    assert names.claim("sub-01", "T1w") == ("sub-01", "sub-01_T1w")
    assert names.claim("sub-01", "T1w") == ("sub-01run2", "sub-01run2_T1w")
    assert names.claim("sub-01", "T1w") == ("sub-01run3", "sub-01run3_T1w")
    assert names.claim("sub-01", "FLAIR") == ("sub-01", "sub-01_FLAIR")


# --- end to end --------------------------------------------------------------


def test_reorients_lps_to_ras_and_names_outputs(tmp_path, monkeypatch):
    img = volume()
    code, out = run(tmp_path, {"ds/sub-01/anat/sub-01_T1w.nii": img}, monkeypatch=monkeypatch)
    assert code == 0
    for name in ("t1w", "flair", "other", "metadata"):
        assert (out / name).is_dir(), f"output folder {name} must exist even when empty"
    written = out / "t1w" / "sub-01_T1w.nii.gz"
    assert written.is_file()
    res = nib.load(str(written))
    assert nib.aff2axcodes(res.affine) == ("R", "A", "S")
    assert res.get_data_dtype() == np.int16
    # LPS -> RAS flips the first two axes; the voxel values travel with their world coordinates.
    expected = np.flip(np.asanyarray(img.dataobj), axis=(0, 1))
    np.testing.assert_array_equal(np.asanyarray(res.dataobj), expected)
    assert np.allclose(res.header.get_zooms(), (1.0, 1.5, 2.0))
    # World position of the first voxel of the output equals that of the last x/y voxel of the input.
    np.testing.assert_allclose(res.affine @ [0, 0, 0, 1], img.affine @ [3, 4, 0, 1])

    records = images_json(out)
    assert len(records) == 1
    rec = records[0]
    assert rec["subject"] == "sub-01"
    assert rec["modality"] == "T1w"
    assert rec["modalityFrom"] == "suffix"
    assert rec["output"] == "t1w/sub-01_T1w.nii.gz"
    assert rec["source"]["path"] == "ds/sub-01/anat/sub-01_T1w.nii"
    assert rec["shape"] == [4, 5, 6]
    assert rec["voxelSizeMm"] == [1.0, 1.5, 2.0]
    assert rec["dtype"] == "int16"
    assert rec["originalOrientation"] == "LPS"
    assert rec["orientation"] == "RAS"
    assert rec["intensity"]["min"] >= 0 and rec["intensity"]["max"] < 1000
    assert rec["sidecar"] is None

    prov = json.loads((out / "t1w" / "provenance.json").read_text())
    assert prov["app"] == "processor-nifti-prep"
    assert prov["appVersion"] == "0.1.0"
    assert prov["tool"] == "nibabel"
    assert prov["parameters"] == {"DEFAULT_MODALITY": "T1w"}
    assert prov["subjects"][0]["subject"] == "sub-01"
    assert prov["subjects"][0]["outputs"] == ["t1w/sub-01_T1w.nii.gz"]
    assert prov["startedAt"] <= prov["finishedAt"]


def test_modalities_sidecars_and_default(tmp_path, monkeypatch):
    sidecar = json.dumps({"MagneticFieldStrength": 3, "Manufacturer": "Siemens", "RepetitionTime": 2.3, "Other": 1})
    files = {
        "sub-01_T1w.nii.gz": volume(seed=1),
        "sub-01_T1w.json": sidecar,
        "sub-01_FLAIR.nii.gz": volume(seed=2),
        "sub-01_T2w.nii.gz": volume(seed=3),
        "scans/sub-02/mprage.nii.gz": volume(seed=4),
        "scans/sub-02/mystery.nii.gz": volume(seed=5),
    }
    code, out = run(tmp_path, files, default_modality="FLAIR", monkeypatch=monkeypatch)
    assert code == 0
    assert (out / "t1w" / "sub-01_T1w.nii.gz").is_file()
    assert (out / "flair" / "sub-01_FLAIR.nii.gz").is_file()
    assert (out / "other" / "sub-01_T2w.nii.gz").is_file()
    assert (out / "t1w" / "mprage_T1w.nii.gz").is_file()  # no '_' in the stem: the whole stem is the subject
    assert (out / "flair" / "mystery_FLAIR.nii.gz").is_file()  # DEFAULT_MODALITY
    assert json.loads((out / "metadata" / "sub-01_T1w.json").read_text())["Manufacturer"] == "Siemens"
    by_out = {r["output"]: r for r in images_json(out)}
    assert by_out["t1w/sub-01_T1w.nii.gz"]["sidecar"] == "metadata/sub-01_T1w.json"
    assert by_out["t1w/sub-01_T1w.nii.gz"]["sidecarFields"] == {"MagneticFieldStrength": 3, "Manufacturer": "Siemens", "RepetitionTime": 2.3}
    assert by_out["flair/mystery_FLAIR.nii.gz"]["modalityFrom"] == "default"
    assert by_out["t1w/mprage_T1w.nii.gz"]["modalityFrom"] == "name"
    assert all(r["orientation"] == "RAS" for r in by_out.values())


def test_default_modality_skip_and_collisions(tmp_path, monkeypatch):
    files = {
        "a/sub-01_T1w.nii.gz": volume(seed=1),
        "b/sub-01_run-2_T1w.nii.gz": volume(seed=2),
        "c/sub-01_acq-hires_T1w.nii.gz": volume(seed=3),
        "unknown.nii.gz": volume(seed=4),
    }
    code, out = run(tmp_path, files, default_modality="skip", monkeypatch=monkeypatch)
    assert code == 0
    names = sorted(p.name for p in (out / "t1w").glob("*.nii.gz"))
    assert names == ["sub-01_T1w.nii.gz", "sub-01run2_T1w.nii.gz", "sub-01run3_T1w.nii.gz"]
    assert not list((out / "other").iterdir())
    prov = json.loads((out / "t1w" / "provenance.json").read_text())
    assert [s["subject"] for s in prov["subjects"]] == ["sub-01", "sub-01run2", "sub-01run3"]
    assert prov["skipped"] == [{"path": "unknown.nii.gz", "reason": "no modality in the file name (DEFAULT_MODALITY=skip)"}]


def test_4d_single_volume_squeezed_and_bad_files_skipped(tmp_path, monkeypatch):
    four_d = nib.Nifti1Image(np.asanyarray(volume().dataobj)[..., np.newaxis], LPS)
    multi = nib.Nifti1Image(np.zeros((4, 5, 6, 3), dtype=np.int16), LPS)
    files = {
        "sub-01_T1w.nii.gz": four_d,
        "sub-02_T1w.nii.gz": multi,
        "sub-03_T1w.nii.gz": "this is not a nifti file",
    }
    code, out = run(tmp_path, files, monkeypatch=monkeypatch)
    assert code == 0  # a partial cohort is still useful
    res = nib.load(str(out / "t1w" / "sub-01_T1w.nii.gz"))
    assert res.shape == (4, 5, 6)
    assert nib.aff2axcodes(res.affine) == ("R", "A", "S")
    assert not (out / "t1w" / "sub-02_T1w.nii.gz").exists()
    assert not (out / "t1w" / "sub-03_T1w.nii.gz").exists()
    prov = json.loads((out / "t1w" / "provenance.json").read_text())
    failed = {f["path"]: f["reason"] for f in prov["failed"]}
    assert "not a 3D volume" in failed["sub-02_T1w.nii.gz"]
    assert "sub-03_T1w.nii.gz" in failed


def test_input_subfolder_is_preferred(tmp_path, monkeypatch):
    files = {"images/sub-01_T1w.nii.gz": volume(seed=1), "stray_T1w.nii.gz": volume(seed=2)}
    code, out = run(tmp_path, files, monkeypatch=monkeypatch)
    assert code == 0
    assert [p.name for p in (out / "t1w").glob("*.nii.gz")] == ["sub-01_T1w.nii.gz"]


def test_all_failed_exits_1(tmp_path, monkeypatch):
    code, out = run(tmp_path, {"sub-01_T1w.nii.gz": "garbage"}, monkeypatch=monkeypatch)
    assert code == 1


def test_nothing_found_exits_1(tmp_path, monkeypatch):
    code, out = run(tmp_path, {"notes.txt": "hello"}, monkeypatch=monkeypatch)
    assert code == 1
    assert (out / "metadata").is_dir()


def test_bad_default_modality_exits_2(tmp_path, monkeypatch):
    code, _ = run(tmp_path, {"sub-01_T1w.nii.gz": volume()}, default_modality="PD", monkeypatch=monkeypatch)
    assert code == 2


def test_handler_sets_env_and_runs(tmp_path, monkeypatch):
    from processor import handler

    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    nib.save(volume(), str(inp / "sub-01_T1w.nii.gz"))
    monkeypatch.setenv("DEFAULT_MODALITY", "skip")  # left over from a warm invocation; must be cleared
    result = handler.handler({"inputDir": str(inp), "outputDir": str(out)}, None)
    assert result["status"] == "ok"
    assert os.environ.get("DEFAULT_MODALITY") is None
    assert (out / "t1w" / "sub-01_T1w.nii.gz").is_file()
    with pytest.raises(RuntimeError):
        handler.handler({"inputDir": str(tmp_path / "missing"), "outputDir": str(out)}, None)


def test_scan_finds_nifti_and_dicom_trees(tmp_path):
    from processor.main import scan

    def dicom_file(p: Path):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\0" * 128 + b"DICM" + b"\0" * 64)

    nib.save(volume(), str(tmp_path / "bids" / "sub-01" / "anat" / "sub-01_T1w.nii.gz")) if (tmp_path / "bids" / "sub-01" / "anat").mkdir(parents=True) is None else None
    dicom_file(tmp_path / "wrapper" / "PATIENT7" / "T1_MPRAGE" / "IM0001")  # DICM magic, no extension
    dicom_file(tmp_path / "wrapper" / "PATIENT7" / "FLAIR" / "IM0001")
    dicom_file(tmp_path / "bids" / "sub-02" / "dicom" / "a.dcm")  # a DICOM folder inside a tree that also holds NIfTI
    (tmp_path / "notes.txt").write_text("not an image")
    niftis, trees = scan(tmp_path)
    assert [p.name for p in niftis] == ["sub-01_T1w.nii.gz"]
    assert sorted(str(t.relative_to(tmp_path)) for t in trees) == ["bids/sub-02/dicom", "wrapper/PATIENT7"]


def test_scaled_input_keeps_dtype_and_intensities(tmp_path, monkeypatch):
    raw = np.random.default_rng(7).integers(-100, 1000, size=(4, 5, 6)).astype(np.int16)
    img = nib.Nifti1Image(raw, LPS)
    img.header.set_slope_inter(2.5, 10.0)
    code, out = run(tmp_path, {"sub-01_T1w.nii.gz": img}, monkeypatch=monkeypatch)
    assert code == 0
    src = nib.load(str(tmp_path / "in" / "sub-01_T1w.nii.gz"))
    res = nib.load(str(out / "t1w" / "sub-01_T1w.nii.gz"))
    assert res.get_data_dtype() == np.int16
    assert (res.dataobj.slope, res.dataobj.inter) == (2.5, 10.0)
    np.testing.assert_array_equal(res.dataobj.get_unscaled(), np.flip(raw, axis=(0, 1)))
    np.testing.assert_allclose(res.get_fdata(), np.flip(src.get_fdata(), axis=(0, 1)))
    rec = images_json(out)[0]
    assert rec["intensity"]["min"] == pytest.approx(raw.min() * 2.5 + 10)
    assert rec["intensity"]["max"] == pytest.approx(raw.max() * 2.5 + 10)
    assert rec["intensity"]["mean"] == pytest.approx(raw.mean() * 2.5 + 10)
