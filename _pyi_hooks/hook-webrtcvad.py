# Override for pyinstaller-hooks-contrib's hook-webrtcvad.py.
# We use the `webrtcvad-wheels` build (precompiled), whose distribution is named
# «webrtcvad-wheels», NOT «webrtcvad». The contrib hook hard-codes
# copy_metadata('webrtcvad') → PackageNotFoundError → build aborts. The C module
# needs no metadata at runtime, so copy the real dist's metadata if present and
# otherwise ship nothing.
from PyInstaller.utils.hooks import copy_metadata

try:
    datas = copy_metadata('webrtcvad-wheels')
except Exception:
    datas = []
