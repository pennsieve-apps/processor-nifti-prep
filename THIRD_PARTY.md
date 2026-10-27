# Third-party software

| Component | Version | Licence | Link |
|---|---|---|---|
| nibabel | 5.3.2 | MIT | https://nipy.org/nibabel/ |
| numpy | 2.2.6 | BSD-3-Clause | https://numpy.org/ |
| dcm2niix (pip package, ships the `dcm2niix` binary) | 1.0.20250506 | BSD-2-Clause (dcm2niix); MPL-2.0 (the pip wrapper) | https://github.com/rordenlab/dcm2niix, https://github.com/casperdcl/dcm2niix-pypi |
| awslambdaric | 2.2.1 | Apache-2.0 | https://github.com/aws/aws-lambda-python-runtime-interface-client |
| python:3.12-slim (base image) | 3.12 | PSF-2.0 (Python); Debian packages under their own free licences | https://hub.docker.com/_/python |

## Citations

dcm2niix: Li X, Morgan PS, Ashburner J, Smith J, Rorden C. (2016). The first
step for neuroimaging data analysis: DICOM to NIfTI conversion. *J Neurosci
Methods* 264:47-56. doi:10.1016/j.jneumeth.2016.03.001

nibabel: Brett M, Markiewicz CJ, Hanke M, et al. nipy/nibabel. Zenodo.
doi:10.5281/zenodo.591597
