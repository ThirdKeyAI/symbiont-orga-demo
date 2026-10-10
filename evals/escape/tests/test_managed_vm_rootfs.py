"""Archive extraction must remain inside the disposable VM fixture tree."""
import importlib.util
from pathlib import Path
import tarfile

import pytest

path = Path(__file__).resolve().parents[1] / 'fixtures/managed-cli-image/build_vm_rootfs.py'
spec = importlib.util.spec_from_file_location('managed_vm_rootfs', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_absolute_guest_link_becomes_an_equivalent_local_link(tmp_path):
    entry = tarfile.TarInfo('usr/bin/tool')
    entry.type = tarfile.SYMTYPE
    entry.linkname = '/usr/local/bin/tool'
    filtered = module.export_filter(tmp_path, entry, str(tmp_path))
    assert (tmp_path / 'usr/bin' / filtered.linkname).resolve() == tmp_path / 'usr/local/bin/tool'


@pytest.mark.parametrize('name,kind,target', [
    ('../outside', tarfile.REGTYPE, ''),
    ('escape', tarfile.SYMTYPE, '../outside'),
    ('device', tarfile.CHRTYPE, ''),
])
def test_escaping_archive_members_and_devices_are_refused(tmp_path, name, kind, target):
    entry = tarfile.TarInfo(name)
    entry.type, entry.linkname = kind, target
    with pytest.raises(tarfile.FilterError):
        module.export_filter(tmp_path, entry, str(tmp_path))
