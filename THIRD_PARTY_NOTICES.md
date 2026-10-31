# Third-party notices

## InpOut32 / InpOutx64

Project: https://www.highrez.co.uk/Downloads/InpOut32/

Binary source used during development: https://github.com/F-19-F/PyECClevo

The package contains a signed InpOutx64 kernel driver. The driver signature observed on the development machine was issued to Red Fox UK Limited.

Copyright (c) <2003-2015> Phil Gibbons <www.highrez.co.uk>
Portions Copyright (c) <2000> <logix4u.net>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

## PyECClevo

Project: https://github.com/F-19-F/PyECClevo

The Clevo EC command layout and fan RPM conversion were used as protocol references. The implementation in this project adds timeouts, retries, validation, a GUI, safety handling and packaging.

## pystray

Project: https://github.com/moses-palmer/pystray
License: LGPLv3.

Unmodified corresponding source is included in `third_party/sources/pystray-0.19.5.tar.gz`. GNU GPLv3 and LGPLv3 texts are in `third_party/licenses/pystray/`. The portable package includes the application source and rebuild instructions; see `third_party/README.md`.

## Pillow

Project: https://python-pillow.org/
License for Pillow 12.3.0 used in this release: MIT-CMU, with bundled third-party notices. See `third_party/licenses/Pillow/LICENSE`. Older Pillow versions may identify their license as HPND.

## PyInstaller

Project: https://pyinstaller.org/
License: GPLv2-or-later with a special exception for packaged applications. PyInstaller is used as a build tool; see its license for details.

## six

Project: https://github.com/benjaminp/six
License: MIT; see `third_party/licenses/six/LICENSE`.

## Python, Tcl and Tk

CPython is distributed under the PSF license and included historical notices; Tcl/Tk retain their own upstream terms. Copies are in `third_party/licenses/`.

## Binary provenance and reproducibility

The bundled InpOutx64 DLL matches the x64 DLL in the official InpOutBinaries_1501.zip. Its exact SHA256 and the pystray source archive hash are in `third_party/checksums.json`. The release includes `build-info.json`, full application source, third-party source and rebuild instructions.
