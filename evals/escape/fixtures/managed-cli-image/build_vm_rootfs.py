#!/usr/bin/env python3
"""Build a local managed-CLI VM test image from an already provisioned Docker image.

No container is started, no image is pulled, and no host filesystem is mounted.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def export_filter(root, member, destination):
    # Convert absolute guest symlinks to equivalent relative links. The data
    # filter still rejects traversal, escaping links and special files.
    if member.issym() and member.linkname.startswith('/'):
        target = root / member.linkname.lstrip('/')
        member = member.replace(linkname=os.path.relpath(target, (root / member.name).parent))
    return tarfile.data_filter(member, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, help='Already cached native CLI/Python/Git image')
    parser.add_argument('--guest-binary', type=Path, required=True, help='Matching static guest executable')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--program', action='append', default=[], help='Additional local static fixture NAME=PATH')
    args = parser.parse_args()
    guest = args.guest_binary.resolve(strict=True)
    output = args.output.absolute()
    record = output.with_suffix(output.suffix + '.json')
    if output.exists() or output.is_symlink() or record.exists() or record.is_symlink():
        parser.error('output and provenance paths must not exist')
    programs = {'sbin/symbi-sandbox-guest': guest}
    for item in args.program:
        name, separator, source = item.partition('=')
        if not separator or not re.fullmatch(r'[a-zA-Z0-9_-]+', name) or 'usr/bin/' + name in programs:
            parser.error('program must have a unique simple NAME=PATH')
        programs['usr/bin/' + name] = Path(source).resolve(strict=True)
    image = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', args.image], text=True, timeout=15).strip()
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
        parser.error('Docker did not return a content-addressed local image')
    container = subprocess.check_output(['docker', 'create', '--name', 'symbi-vm-image-' + uuid.uuid4().hex[:12], image], text=True, timeout=30).strip()
    try:
        with tempfile.TemporaryDirectory(prefix='.managed-rootfs-', dir=output.parent) as temporary:
            root = Path(temporary) / 'tree'
            root.mkdir()
            export = subprocess.Popen(['docker', 'export', container], stdout=subprocess.PIPE)
            try:
                with tarfile.open(fileobj=export.stdout, mode='r|') as archive:
                    archive.extractall(root, filter=lambda member, destination: export_filter(root, member, destination))
            finally:
                export.stdout.close()
                if export.wait(timeout=30):
                    raise RuntimeError('container export failed')
            for name in ['proc', 'sys', 'dev', 'tmp', 'root', 'sbin', 'etc']:
                (root / name).mkdir(exist_ok=True)
            (root / 'root').chmod(0o700)
            (root / 'root/protected-canary').write_text('synthetic-root-only-guest-fixture\n')
            (root / 'root/protected-canary').chmod(0o600)
            (root / 'tmp').chmod(0o1777)
            for name, data in [('hosts', '127.0.0.1 localhost\n'), ('resolv.conf', ''), ('hostname', 'synthetic-managed-guest\n')]:
                path = root / 'etc' / name
                if path.exists() or path.is_symlink():
                    path.unlink()
                path.write_text(data)
            hashes = {}
            for destination, source in programs.items():
                target = root / destination
                if not target.resolve().is_relative_to(root.resolve()):
                    raise ValueError('program destination escapes the image')
                shutil.copyfile(source, target)
                target.chmod(0o755)
                hashes[destination] = digest(source)
            for name in ['python3', 'claude']:
                target = root / 'usr/bin' / name
                if not target.exists() and not target.is_symlink():
                    target.symlink_to('../local/bin/' + name)
                if not target.resolve().is_relative_to(root.resolve()) or not target.is_file():
                    raise ValueError('image must contain executable ' + name)
                hashes['usr/bin/' + name] = digest(target)
            with output.open('xb') as stream:
                stream.truncate(1024 * 1024 * 1024)
            subprocess.run(['mkfs.ext4', '-q', '-F', '-d', str(root), str(output)], check=True, timeout=120)
            report = dict(rootfs=str(output), rootfs_sha256=digest(output), docker_image=image,
                programs=hashes, guest_binary_sha256=digest(guest), size_bytes=output.stat().st_size,
                purpose='local real-CLI regression image; no downloads or provider credentials')
            with record.open('x') as stream:
                stream.write(json.dumps(report, indent=2) + '\n')
            print(json.dumps(report, indent=2))
    finally:
        subprocess.run(['docker', 'rm', container], check=True, stdout=subprocess.DEVNULL, timeout=30)


if __name__ == '__main__':
    main()
