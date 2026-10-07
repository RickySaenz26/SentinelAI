"""Identify the protected source tree without cwd or Git metadata."""

from pathlib import Path

from app.evidence.errors import UnsafeStorage
from app.evidence.filesystem import reject_ambiguous_posix_root

IMAGE_ROOT_FILE = Path("/etc/sentinelai-application-root")
API_LOCATION = Path("app/api/v1/evidence.py")


def protected_root(api_file: Path) -> Path:
    reject_ambiguous_posix_root(api_file)
    source = api_file.resolve(strict=True)
    application = next(
        (parent for parent in source.parents if source.relative_to(parent) == API_LOCATION), None
    )
    if application is None:
        raise UnsafeStorage("Unknown application layout.")
    checkout = application.parent
    if application.name == "backend" and all(
        (checkout / marker).is_file()
        for marker in (
            "compose.yaml",
            "frontend/package.json",
            "scripts/backend-quality.ps1",
            "backend/alembic.ini",
        )
    ):
        return checkout
    # Docker writes this root-owned marker outside the source mount. A copied
    # backend directory is not itself evidence of a supported image layout.
    if IMAGE_ROOT_FILE.is_file():
        declared = Path(IMAGE_ROOT_FILE.read_text(encoding="utf-8").strip())
        reject_ambiguous_posix_root(declared)
        if declared == application and (application / "alembic.ini").is_file():
            return application
    raise UnsafeStorage("Unknown application layout.")
