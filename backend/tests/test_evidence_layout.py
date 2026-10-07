"""Exercise the API's actual storage factory, with private disposable Linux roots."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from starlette.requests import Request

from app.api.v1 import evidence as api
from app.evidence import layout
from app.evidence.errors import UnsafeStorage


@pytest.fixture
def tree(monkeypatch):
    with TemporaryDirectory(prefix="sentinelai-api-layout-") as temporary:
        root = Path(temporary)
        marker = root / "image-root"
        monkeypatch.setattr(layout, "IMAGE_ROOT_FILE", marker)
        for name in ("storage", "keys"):
            (root / name).mkdir(mode=0o700)
        key = root / "keys" / "lab-1.kek"
        key.write_bytes(os.urandom(32))
        key.chmod(0o600)
        monkeypatch.setenv("LAB_EVIDENCE_ROOT", str(root / "storage"))
        monkeypatch.setenv("LAB_EVIDENCE_KEY_ROOT", str(root / "keys"))
        monkeypatch.setenv("LAB_EVIDENCE_ACTIVE_KEY_ID", "lab-1")
        try:
            yield root, marker
        finally:
            monkeypatch.undo()
    assert not root.exists()


def arrange(tree, monkeypatch, kind):
    root, marker = tree
    protected = root / ("platform" if kind == "checkout" else "app")
    application = protected / "backend" if kind == "checkout" else protected
    source = application / "app/api/v1/evidence.py"
    source.parent.mkdir(parents=True)
    source.touch()
    (application / "alembic.ini").touch()
    if kind == "checkout":
        for name in ("compose.yaml", "frontend/package.json", "scripts/backend-quality.ps1"):
            path = protected / name
            path.parent.mkdir(exist_ok=True)
            path.touch()
    elif kind == "image":
        marker.write_text(str(application), encoding="utf-8")
    monkeypatch.setattr(api, "__file__", str(source))
    return protected, application


def factory():
    request = Request({"type": "http", "method": "POST", "headers": []})
    request.state.request_id = "layout-regression"
    return api.get_access(request, csrf_token=None).storage_factory()


def forbid_open(*args, **kwargs):
    pytest.fail("Rejected configuration must not open keys or storage")


@pytest.mark.parametrize("kind", ["checkout", "image"])
@pytest.mark.parametrize("variable", ["LAB_EVIDENCE_ROOT", "LAB_EVIDENCE_KEY_ROOT"])
@pytest.mark.parametrize("inside_backend", [False, True])
def test_api_rejects_entire_source_tree(tree, monkeypatch, kind, variable, inside_backend):
    protected, application = arrange(tree, monkeypatch, kind)
    target = (application if inside_backend else protected) / "private-data"
    monkeypatch.setenv(variable, str(target))
    monkeypatch.setattr(os, "open", forbid_open)
    with pytest.raises(UnsafeStorage, match="outside the repository"), factory():
        pytest.fail("Source tree accepted")


@pytest.mark.parametrize("kind", ["checkout", "image"])
@pytest.mark.parametrize("git_kind", ["absent", "file", "directory"])
def test_api_external_roots_work_without_cwd_or_git(tree, monkeypatch, kind, git_kind):
    protected, _ = arrange(tree, monkeypatch, kind)
    if git_kind == "file":
        (protected / ".git").write_text("gitdir: /irrelevant", encoding="utf-8")
    elif git_kind == "directory":
        (protected / ".git").mkdir()
    monkeypatch.chdir(tree[0] / "storage")
    with factory() as storage:
        assert storage.directory.fd >= 0


@pytest.mark.parametrize(
    "failure", ["missing", "wrong_marker", "ambiguous_marker", "source", "incomplete_checkout"]
)
def test_api_unknown_layout_fails_before_open(tree, monkeypatch, failure):
    protected, application = arrange(tree, monkeypatch, "unknown")
    if failure == "wrong_marker":
        tree[1].write_text(str(tree[0] / "elsewhere"), encoding="utf-8")
    elif failure == "ambiguous_marker":
        tree[1].write_text("/" + str(application), encoding="utf-8")
    elif failure == "source":
        source = protected / "other.py"
        source.touch()
        monkeypatch.setattr(api, "__file__", str(source))
    elif failure == "incomplete_checkout":
        protected, _ = arrange(tree, monkeypatch, "checkout")
        (protected / "compose.yaml").unlink()
    monkeypatch.setattr(os, "open", forbid_open)
    with pytest.raises(UnsafeStorage), factory():
        pytest.fail("Unknown layout accepted")


@pytest.mark.parametrize(
    "case",
    [
        "storage",
        "keys",
        "source",
        "equal",
        "nested_keys",
        "nested_storage",
        "checkout",
        "traversal",
        "windows",
    ],
)
def test_api_preserves_location_guards(tree, monkeypatch, case):
    protected, _ = arrange(tree, monkeypatch, "checkout")
    root, _ = tree
    if case in ("storage", "equal", "nested_keys"):
        monkeypatch.setenv("LAB_EVIDENCE_ROOT", "/" + str(root / "storage"))
    if case in ("keys", "nested_storage"):
        monkeypatch.setenv("LAB_EVIDENCE_KEY_ROOT", "/" + str(root / "keys"))
    if case == "equal":
        monkeypatch.setenv("LAB_EVIDENCE_KEY_ROOT", str(root / "storage"))
    elif case == "nested_keys":
        monkeypatch.setenv("LAB_EVIDENCE_KEY_ROOT", str(root / "storage/keys"))
    elif case == "nested_storage":
        monkeypatch.setenv("LAB_EVIDENCE_ROOT", str(root / "keys/storage"))
    elif case == "source":
        monkeypatch.setattr(api, "__file__", "/" + api.__file__)
    elif case == "checkout":
        monkeypatch.setenv("LAB_EVIDENCE_ROOT", "/" + str(protected / "private-data"))
    elif case == "traversal":
        monkeypatch.setenv("LAB_EVIDENCE_ROOT", str(root / "storage/../platform/private"))
    elif case == "windows":
        from app.evidence import filesystem

        monkeypatch.setattr(filesystem.sys, "platform", "win32")
    monkeypatch.setattr(os, "open", forbid_open)
    with pytest.raises(UnsafeStorage), factory():
        pytest.fail("Unsafe configuration accepted")


@pytest.mark.parametrize(
    "variable,name", [("LAB_EVIDENCE_ROOT", "storage"), ("LAB_EVIDENCE_KEY_ROOT", "keys")]
)
def test_api_still_rejects_symlinks(tree, monkeypatch, variable, name):
    arrange(tree, monkeypatch, "checkout")
    link = tree[0] / "linked"
    link.symlink_to(tree[0] / name, target_is_directory=True)
    monkeypatch.setenv(variable, str(link))
    with pytest.raises(UnsafeStorage), factory():
        pytest.fail("Symlink accepted")
