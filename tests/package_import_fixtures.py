"""Keep TAR inputs for the existing importer until ZIP imports are implemented."""
import tarfile
import zipfile

from asaree.services.experiment_packages import PackageWriter


class TarImportFixtureWriter(PackageWriter):
    """Transcode exporter output for import tests, without adding runtime compatibility."""

    def __init__(self, destination, staging):
        super().__init__(destination, staging)
        self.destination = destination

    def finish(self, manifest):
        result = super().finish(manifest)
        temporary = self.destination.with_suffix('.tar.part')
        with zipfile.ZipFile(self.destination) as source, tarfile.open(temporary, 'w:gz') as target:
            for entry in source.infolist():
                member = tarfile.TarInfo(entry.filename)
                member.size, member.mode, member.mtime = entry.file_size, 0o600, 0
                with source.open(entry) as stream:
                    target.addfile(member, stream)
        temporary.replace(self.destination)
        return result
