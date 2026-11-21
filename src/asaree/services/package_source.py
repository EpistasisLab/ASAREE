"""Read retained package evidence without permanent extracted copies."""
import uuid
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from asaree.services import managed_storage
from asaree.services.package_validation import Limits, PackageError, document_bytes, member_path


def archive_path(identifier: uuid.UUID) -> Path:
    root = managed_storage.roots()['backups']
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root / f'experiment-package-{identifier}.zip'


def scratch_path(identifier: uuid.UUID) -> Path:
    return managed_storage.roots()['scratch'] / f'experiment-package-{identifier}'


def members(identifier: uuid.UUID, prefix: str) -> list[str]:
    with zipfile.ZipFile(archive_path(identifier)) as archive:
        return [entry.filename for entry in archive.infolist()
                if entry.filename.startswith(prefix) and not entry.is_dir()]


def document(identifier: uuid.UUID, name: str, limits: Limits) -> Any:
    with zipfile.ZipFile(archive_path(identifier)) as archive, archive.open(member_path(name)) as source:
        return document_bytes(source.read(limits.document + 1), name, limits)


def records(identifier: uuid.UUID, name: str, limits: Limits) -> Iterator[dict[str, Any]]:
    with zipfile.ZipFile(archive_path(identifier)) as archive, archive.open(member_path(name)) as source:
        while line := source.readline(limits.document + 1):
            value = document_bytes(line, name, limits)
            if not isinstance(value, dict):
                raise PackageError(f'Expected object record: {name}')
            yield value


def chunks(identifier: uuid.UUID, name: str) -> Iterator[bytes]:
    with zipfile.ZipFile(archive_path(identifier)) as archive, archive.open(member_path(name)) as source:
        while block := source.read(1024 * 1024):
            yield block


def copy_member(identifier: uuid.UUID, name: str, destination: Path) -> None:
    with destination.open('wb') as output:
        for block in chunks(identifier, name):
            output.write(block)
