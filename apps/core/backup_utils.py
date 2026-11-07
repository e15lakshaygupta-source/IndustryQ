import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from uuid import uuid4

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone


BACKUP_PREFIX = "industryos-backup-"
BACKUP_VERSION = 1


def _file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_archive_path(value):
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise ValueError("Backup contains an invalid path.")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("Backup contains an unsafe path.")
    return path


def create_backup(output_dir=None, retention_days=30):
    output_dir = Path(output_dir or settings.BASE_DIR / "backups").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    created_at = timezone.now()
    archive_path = output_dir / f"{BACKUP_PREFIX}{created_at.strftime('%Y%m%dT%H%M%SZ')}.zip"
    if archive_path.exists():
        archive_path = output_dir / f"{BACKUP_PREFIX}{created_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}.zip"

    media_root = Path(settings.MEDIA_ROOT)
    if media_root.is_symlink():
        raise ValueError("MEDIA_ROOT must not be a symbolic link.")
    media_root = media_root.resolve()
    try:
        output_dir.relative_to(media_root)
    except ValueError:
        pass
    else:
        raise ValueError("Backup output directory must be outside MEDIA_ROOT.")
    media_files = []
    if media_root.exists():
        for path in sorted(media_root.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"Media backup does not support symbolic links: {path}")
            if path.is_file():
                relative_path = path.relative_to(media_root).as_posix()
                archive_name = f"media/{relative_path}"
                _safe_archive_path(archive_name)
                media_files.append({
                    "path": archive_name,
                    "size": path.stat().st_size,
                    "sha256": _file_digest(path),
                    "source": path,
                })

    temp_archive = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output_dir,
            prefix=".industryos-backup-",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_archive = Path(temp_file.name)

        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "database.json"
            with database_path.open("w", encoding="utf-8", newline="\n") as fixture:
                call_command(
                    "dumpdata",
                    "--natural-foreign",
                    "--natural-primary",
                    stdout=fixture,
                    verbosity=0,
                )
            manifest = {
                "version": BACKUP_VERSION,
                "created_at": created_at.isoformat(),
                "database_vendor": settings.DATABASES["default"]["ENGINE"],
                "database": {
                    "path": "database.json",
                    "size": database_path.stat().st_size,
                    "sha256": _file_digest(database_path),
                },
                "media_files": [
                    {key: value for key, value in item.items() if key != "source"}
                    for item in media_files
                ],
            }
            with zipfile.ZipFile(temp_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(database_path, "database.json")
                for item in media_files:
                    archive.write(item["source"], item["path"])
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, indent=2, sort_keys=True),
                )

        os.replace(temp_archive, archive_path)
    finally:
        if temp_archive and temp_archive.exists():
            temp_archive.unlink()

    if retention_days is not None and retention_days > 0:
        cutoff = timezone.now().timestamp() - retention_days * 24 * 60 * 60
        for old_backup in output_dir.glob(f"{BACKUP_PREFIX}*.zip"):
            if old_backup.is_file() and old_backup.stat().st_mtime < cutoff:
                old_backup.unlink()
    return archive_path


def stage_backup(archive_path, staging_directory):
    archive_path = Path(archive_path)
    staging_directory = Path(staging_directory)
    database_path = staging_directory / "database.json"
    media_root = staging_directory / "media"

    with zipfile.ZipFile(archive_path, "r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Backup contains duplicate archive entries.")
        for name in names:
            _safe_archive_path(name)
        if "manifest.json" not in names or "database.json" not in names:
            raise ValueError("Backup is missing its manifest or database fixture.")
        manifest = json.loads(archive.read("manifest.json"))
        if not isinstance(manifest, dict):
            raise ValueError("Backup manifest is invalid.")
        if manifest.get("version") != BACKUP_VERSION:
            raise ValueError("Backup format version is not supported.")

        database_info = manifest.get("database", {})
        if not isinstance(database_info, dict) or database_info.get("path") != "database.json":
            raise ValueError("Backup database path is invalid.")
        media_manifest = manifest.get("media_files", [])
        if not isinstance(media_manifest, list):
            raise ValueError("Backup media manifest is invalid.")
        expected_names = {"manifest.json", "database.json"}
        file_records = {}
        for item in media_manifest:
            if not isinstance(item, dict):
                raise ValueError("Backup media manifest entry is invalid.")
            name = item.get("path", "")
            path = _safe_archive_path(name)
            if path.parts[0] != "media" or len(path.parts) < 2:
                raise ValueError("Backup media path is invalid.")
            if name in file_records:
                raise ValueError("Backup contains duplicate media paths.")
            file_records[name] = item
            expected_names.add(name)
        if set(names) != expected_names:
            raise ValueError("Backup contents do not match the manifest.")

        staging_directory.mkdir(parents=True, exist_ok=True)
        _copy_verified(
            archive,
            "database.json",
            database_path,
            database_info.get("sha256"),
            database_info.get("size"),
        )
        for name, item in file_records.items():
            relative_path = PurePosixPath(name).relative_to("media")
            destination = media_root.joinpath(*relative_path.parts)
            _copy_verified(
                archive,
                name,
                destination,
                item.get("sha256"),
                item.get("size"),
            )
    return database_path, media_root, manifest


def _copy_verified(archive, name, destination, expected_digest, expected_size):
    if not isinstance(expected_digest, str) or len(expected_digest) != 64:
        raise ValueError(f"Backup checksum is invalid for {name}.")
    if not isinstance(expected_size, int) or expected_size < 0:
        raise ValueError(f"Backup size is invalid for {name}.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with archive.open(name, "r") as source, destination.open("wb") as target:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
            target.write(chunk)
    if digest.hexdigest() != expected_digest or size != expected_size:
        destination.unlink(missing_ok=True)
        raise ValueError(f"Backup integrity check failed for {name}.")
