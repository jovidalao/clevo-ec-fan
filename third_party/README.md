# Third-party components and rebuilding

The application's own source is MIT licensed. Third-party files retain their own licenses; see `licenses/` and the root `THIRD_PARTY_NOTICES.md`.

## pystray 0.19.5 (LGPLv3)

`sources/pystray-0.19.5.tar.gz` contains the unmodified upstream source from:

https://github.com/moses-palmer/pystray/archive/refs/tags/v0.19.5.tar.gz

The archive and bundled InpOutx64 DLL hashes are recorded in `checksums.json`. Both GNU GPLv3 and LGPLv3 texts are provided under `licenses/pystray/`.

The portable release includes application sources under `source/`, the library source above, and the inputs needed to rebuild the application with a modified pystray. No restriction is placed on modifying the library or debugging such modifications.

On Windows x64 with Python 3.13 installed:

```powershell
# In a checkout, or the portable release's source/ directory:
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build-lock.txt

# Extract third_party/sources/pystray-0.19.5.tar.gz with tar or an archive tool.
# Edit the library source as needed, then install the extracted project:
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation --force-reinstall .\pystray-0.19.5
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe build.py
```

If a locally modified library changes its version, `requirements.txt` may also need updating for future dependency installs. The build uses the currently installed library, and `build-info.json` records actual versions. Preserve and update the corresponding library source when redistributing modifications.

`requirements-build-lock.txt` records the release's Windows Python 3.13 package environment; it is not a promise of byte-identical ZIPs. Python installers are available from https://www.python.org/downloads/windows/.

## InpOutx64 1.5.0.1 distribution

`inpoutx64.dll` is retained in the project root because the loader and build script expect that path. Its Windows file-version resource reports `1, 5, 0, 0`; its bytes match the x64 DLL in the official `InpOutBinaries_1501.zip` distribution. This upstream distribution includes the MIT license, copied unchanged into `licenses/InpOutx64-LICENSE.txt`.

Official binary distribution: https://www.highrez.co.uk/Downloads/InpOut32/

The DLL includes its port-access driver as a resource; it is not loaded by the simulated tests.

## Runtime licenses

This release includes the licenses for Pillow, six, CPython, Tcl and Tk. Pillow's license file includes its bundled image-library notices. Actual package versions are recorded in the portable release's `build-info.json`.
