"""Linux container tests; native Windows runs test_contract_crypto.py explicitly."""

import os
import socket
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest

from app.evidence import storage as storage_module
from app.evidence.configuration import FileKeyProvider, configured_storage, storage_configuration
from app.evidence.crypto import ciphertext_digest
from app.evidence.errors import EvidenceUnavailable, ObjectExists, StorageInterrupted, UnsafeStorage
from app.evidence.filesystem import PrivateDirectory
from app.evidence.storage import LocalEvidenceStorage, PreparedObject


@pytest.fixture
def store(sandbox, codec):
    instance = LocalEvidenceStorage(
        sandbox / "storage", codec, repository_root=sandbox / "repository"
    )
    yield instance
    instance.close()


def test_durable_roundtrip_private_and_no_plaintext(store, document, context, sandbox):
    prepared = store.prepare(context, document)
    receipt = store.write(prepared)
    assert store.read(receipt) == document
    assert store.recover(receipt) == "published"
    for file in (sandbox / "storage").iterdir():
        assert stat.S_IMODE(file.stat().st_mode) == 0o600
        assert document.lab_asset_reference.encode() not in file.read_bytes()
    assert not list((sandbox / "storage").glob("*.tmp"))
    with pytest.raises(ObjectExists):
        store.write(prepared)
    with pytest.raises(ObjectExists):
        store.discard_temporary(receipt)


def test_filesystem_and_key_configuration(sandbox, keys, context, document):
    key_path = sandbox / "keys" / "lab-1.kek"
    key_path.write_bytes(keys.get_key("lab-1"))
    key_path.chmod(0o600)
    environment = {
        "LAB_EVIDENCE_ROOT": str(sandbox / "storage"),
        "LAB_EVIDENCE_KEY_ROOT": str(sandbox / "keys"),
        "LAB_EVIDENCE_ACTIVE_KEY_ID": "lab-1",
    }
    assert storage_configuration(environment) == (sandbox / "storage", sandbox / "keys", "lab-1")
    with configured_storage(environment, repository_root=sandbox / "repository") as instance:
        receipt = instance.write(instance.prepare(context, document))
        assert instance.read(receipt) == document
    with (
        pytest.raises(EvidenceUnavailable),
        configured_storage(
            {**environment, "LAB_EVIDENCE_ACTIVE_KEY_ID": "missing"},
            repository_root=sandbox / "repository",
        ),
    ):
        pytest.fail("Missing KEK must prevent storage activation")
    key_path.unlink()
    assert list((sandbox / "keys").iterdir()) == []


@pytest.mark.parametrize(
    "storage_suffix,key_suffix,ambiguous",
    [
        ("storage", "storage", "storage"),
        ("storage", "storage", "keys"),
        ("storage", "storage/keys", "storage"),
        ("storage", "storage/keys", "keys"),
        ("keys/storage", "keys", "storage"),
        ("keys/storage", "keys", "keys"),
        ("repository/storage", "keys", "storage"),
        ("storage", "repository/keys", "keys"),
        ("storage", "keys", "both"),
    ],
)
def test_ambiguous_configuration_rejected_before_open(
    sandbox, monkeypatch, storage_suffix, key_suffix, ambiguous
):
    roots = {"storage": str(sandbox / storage_suffix), "keys": str(sandbox / key_suffix)}
    for name in roots:
        if ambiguous in (name, "both"):
            roots[name] = "/" + roots[name]
    environment = {
        "LAB_EVIDENCE_ROOT": roots["storage"],
        "LAB_EVIDENCE_KEY_ROOT": roots["keys"],
        "LAB_EVIDENCE_ACTIVE_KEY_ID": "lab-1",
    }

    def forbidden(*args, **kwargs):
        pytest.fail("Ambiguous configuration must be rejected before filesystem I/O")

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", forbidden)
        with pytest.raises(UnsafeStorage, match="Ambiguous POSIX root"):
            storage_configuration(environment)
        with (
            pytest.raises(UnsafeStorage, match="Ambiguous POSIX root"),
            configured_storage(environment, repository_root=sandbox / "repository"),
        ):
            pytest.fail("Ambiguous configuration must not activate storage")
    assert list((sandbox / "storage").iterdir()) == []
    assert list((sandbox / "keys").iterdir()) == []


@pytest.mark.parametrize("ambiguous", ["storage", "checkout", "both"])
def test_direct_adapter_rejects_ambiguous_roots_before_resolve_or_open(
    sandbox, monkeypatch, codec, ambiguous
):
    root, repository = sandbox / "storage", sandbox / "repository"
    if ambiguous in ("storage", "both"):
        root = type(root)("/" + str(root))
    if ambiguous in ("checkout", "both"):
        repository = type(repository)("/" + str(repository))

    def forbidden(*args, **kwargs):
        pytest.fail("Ambiguous roots must be rejected before resolve or filesystem I/O")

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", forbidden)
        patch.setattr(type(repository), "resolve", forbidden)
        with pytest.raises(UnsafeStorage, match="Ambiguous POSIX root"):
            PrivateDirectory(root, repository_root=repository)
        with pytest.raises(UnsafeStorage, match="Ambiguous POSIX root"):
            LocalEvidenceStorage(root, codec, repository_root=repository)
        with pytest.raises(UnsafeStorage, match="Ambiguous POSIX root"):
            FileKeyProvider(root, repository_root=repository)


def test_configured_storage_rejects_ambiguous_checkout_before_open(sandbox, monkeypatch):
    environment = {
        "LAB_EVIDENCE_ROOT": str(sandbox / "storage"),
        "LAB_EVIDENCE_KEY_ROOT": str(sandbox / "keys"),
        "LAB_EVIDENCE_ACTIVE_KEY_ID": "lab-1",
    }
    repository = type(sandbox)("/" + str(sandbox / "repository"))

    def forbidden(*args, **kwargs):
        pytest.fail("Ambiguous checkout must be rejected before opening keys or storage")

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", forbidden)
        patch.setattr(type(repository), "resolve", forbidden)
        with (
            pytest.raises(UnsafeStorage, match="Ambiguous POSIX root"),
            configured_storage(environment, repository_root=repository),
        ):
            pytest.fail("Ambiguous checkout must not activate storage")


def test_file_keys_absent_wrong_mode_symlink_length_and_names(sandbox, keys):
    provider = FileKeyProvider(sandbox / "keys", repository_root=sandbox / "repository")
    try:
        for invalid in ("../bad", "unknown", "C:\\key", "/root", "", "a/../b"):
            with pytest.raises(EvidenceUnavailable):
                provider.get_key(invalid)
        path = sandbox / "keys" / "lab-1.kek"
        path.write_bytes(b"short")
        path.chmod(0o600)
        with pytest.raises(EvidenceUnavailable):
            provider.get_key("lab-1")
        path.write_bytes(keys.get_key("lab-1"))
        path.chmod(0o644)
        with pytest.raises(EvidenceUnavailable):
            provider.get_key("lab-1")
        path.chmod(0o600)
        alias = sandbox / "keys" / "alias.kek"
        alias.symlink_to(path)
        with pytest.raises(EvidenceUnavailable):
            provider.get_key("alias")
        alias.unlink()
        os.link(path, alias)
        with pytest.raises(EvidenceUnavailable):
            provider.get_key("lab-1")
    finally:
        provider.close()
        provider.close()  # explicit close is idempotent


def test_unsafe_directories_and_symlink_ancestors(sandbox):
    repository = sandbox / "repository"
    (repository / "data").mkdir(mode=0o700)
    (sandbox / "alias").symlink_to(sandbox / "storage", target_is_directory=True)
    unsafe = [
        repository,
        repository / "data",
        sandbox,
        sandbox / "alias",
        sandbox / "alias" / "child",
        sandbox / "absent",
        sandbox / "storage" / ".." / "keys",
    ]
    for root in unsafe:
        with pytest.raises(UnsafeStorage):
            PrivateDirectory(root, repository_root=repository)
    with pytest.raises(UnsafeStorage):
        PrivateDirectory(type(sandbox)("relative"), repository_root=repository)
    (sandbox / "storage").chmod(0o755)
    with pytest.raises(UnsafeStorage):
        PrivateDirectory(sandbox / "storage", repository_root=repository)
    (sandbox / "storage").chmod(0o777)
    with pytest.raises(UnsafeStorage):
        PrivateDirectory(sandbox / "storage", repository_root=repository)


def test_traversal_symlink_special_files_and_hostile_receipt(store, sandbox, context, document):
    for name in ("..", ".", "../key", "/key", "a/b", "a\\b", "bad\x00", "x" * 101):
        with pytest.raises(UnsafeStorage):
            store.directory.open_file(name, os.O_RDONLY)
    prepared = store.prepare(context, document)
    with pytest.raises(UnsafeStorage):
        store.read(replace(prepared.receipt, object_id="../forged"))
    _, final = store.names(prepared.receipt)
    (sandbox / "storage" / final).symlink_to(sandbox / "keys")
    with pytest.raises(EvidenceUnavailable):
        store.read(prepared.receipt)
    with pytest.raises(ObjectExists):
        store.write(prepared)
    (sandbox / "storage" / final).unlink()
    os.mkfifo(sandbox / "storage" / final, mode=0o600)
    with pytest.raises(EvidenceUnavailable):
        store.read(prepared.receipt)


def test_descriptor_pinning_does_not_follow_replaced_root(store, sandbox, context, document):
    moved = sandbox / "original"
    (sandbox / "storage").rename(moved)
    (sandbox / "storage").symlink_to(sandbox / "keys", target_is_directory=True)
    receipt = store.write(store.prepare(context, document))
    assert store.read(receipt) == document
    assert not list((sandbox / "keys").iterdir())
    assert list(moved.glob("*.evidence"))


def test_concurrent_exclusive_publication(store, context, document):
    prepared = store.prepare(context, document)

    def write(_):
        try:
            store.write(prepared)
            return "created"
        except ObjectExists:
            return "exists"

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sorted(pool.map(write, range(4))) == ["created", "exists", "exists", "exists"]
    assert store.read(prepared.receipt) == document


@pytest.mark.parametrize("sync_number", [1, 2, 3, 4])
def test_interrupted_sync_recovery_never_promotes_or_deletes_valid_final(
    store, context, document, monkeypatch, sandbox, sync_number
):
    prepared = store.prepare(context, document)
    original = os.fsync
    calls = 0

    def fail(fd):
        nonlocal calls
        calls += 1
        if calls == sync_number:
            raise OSError("synthetic fsync interruption")
        return original(fd)

    with monkeypatch.context() as patch:
        patch.setattr(storage_module.os, "fsync", fail)
        with pytest.raises(StorageInterrupted):
            store.write(prepared)
    state = store.recover(prepared.receipt)
    if sync_number < 3:
        assert state == "temporary_only"
        with pytest.raises(EvidenceUnavailable):
            store.read(prepared.receipt)
        assert store.discard_temporary(prepared.receipt)
        assert not store.discard_temporary(prepared.receipt)
        assert store.recover(prepared.receipt) == "missing"
    else:
        assert state == "published"
        assert store.read(prepared.receipt) == document
        with pytest.raises(ObjectExists):
            store.discard_temporary(prepared.receipt)
    assert not list((sandbox / "storage").glob("*.tmp"))


def test_partial_writes_and_interruption_before_publication(
    store, context, document, monkeypatch, sandbox
):
    prepared = store.prepare(context, document)

    def partial(fd, content):
        os.write(fd, content[:31])
        raise OSError("synthetic partial write")

    with monkeypatch.context() as patch:
        patch.setattr(store, "write_all", partial)
        with pytest.raises(StorageInterrupted):
            store.write(prepared)
    assert store.recover(prepared.receipt) == "temporary_only"
    temp, _ = store.names(prepared.receipt)
    assert document.lab_asset_reference.encode() not in (sandbox / "storage" / temp).read_bytes()
    with pytest.raises(ObjectExists):
        store.write(prepared)
    assert store.discard_temporary(prepared.receipt)
    original_write = os.write
    with monkeypatch.context() as patch:
        patch.setattr(
            storage_module.os, "write", lambda fd, content: original_write(fd, content[:13])
        )
        store.write(prepared)
    assert store.read(prepared.receipt) == document


def test_failure_before_write_and_zero_write(store, context, document, monkeypatch, sandbox):
    prepared = store.prepare(context, document)
    _, final = store.names(prepared.receipt)
    forged = b"synthetic plaintext must not persist"
    receipt = replace(prepared.receipt, envelope_sha256=ciphertext_digest(forged))
    with pytest.raises(EvidenceUnavailable):
        store.write(PreparedObject(receipt, forged))
    assert not (sandbox / "storage" / final).exists()
    with monkeypatch.context() as patch:
        patch.setattr(storage_module.os, "write", lambda fd, content: 0)
        with pytest.raises(StorageInterrupted):
            store.write(prepared)
    assert store.discard_temporary(prepared.receipt)


def test_ciphertext_truncation_digest_context_and_wrong_key(
    store, context, document, sandbox, keys
):
    receipt = store.write(store.prepare(context, document))
    with pytest.raises(EvidenceUnavailable):
        store.read(replace(receipt, context=context.model_copy(update={"asset_id": uuid4()})))
    keys.values["lab-1"] = os.urandom(32)
    with pytest.raises(EvidenceUnavailable):
        store.read(receipt)
    _, final = store.names(receipt)
    path = sandbox / "storage" / final
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(EvidenceUnavailable):
        store.read(receipt)
    with pytest.raises(EvidenceUnavailable):
        store.recover(receipt)


def test_recovery_preserves_unrelated_files_and_conflicting_temporary(
    store, context, document, sandbox
):
    prepared = store.prepare(context, document)
    store.write(prepared)
    temporary, _ = store.names(prepared.receipt)
    path = sandbox / "storage" / temporary
    path.write_bytes(b"unrelated ciphertext")
    path.chmod(0o600)
    with pytest.raises(UnsafeStorage):
        store.recover(prepared.receipt)
    assert path.exists()
    assert store.read(prepared.receipt) == document


def test_publication_failure_leaves_temporary_for_explicit_abort(
    store, context, document, monkeypatch
):
    prepared = store.prepare(context, document)

    def fail(*args, **kwargs):
        raise OSError("synthetic link failure")

    with monkeypatch.context() as patch:
        patch.setattr(storage_module.os, "link", fail)
        with pytest.raises(StorageInterrupted):
            store.write(prepared)
    assert store.recover(prepared.receipt) == "temporary_only"
    assert store.discard_temporary(prepared.receipt)


def test_recovery_fsync_failure_preserves_published_object(store, context, document, monkeypatch):
    receipt = store.write(store.prepare(context, document))

    def fail():
        raise OSError("synthetic recovery interruption")

    with monkeypatch.context() as patch:
        patch.setattr(store.directory, "sync", fail)
        with pytest.raises(StorageInterrupted):
            store.recover(receipt)
    assert store.read(receipt) == document


@pytest.mark.parametrize("phase", ["during_write", "after_publish"])
def test_actual_process_exit_releases_lock_and_preserves_recovery(store, context, document, phase):
    prepared = store.prepare(context, document)
    child = os.fork()
    if child == 0:
        if phase == "during_write":

            def exit_during_write(fd, content):
                os.write(fd, content[:31])
                os._exit(23)

            store.write_all = exit_during_write
        else:
            original_sync = store.directory.sync
            count = 0

            def exit_after_publish():
                nonlocal count
                original_sync()
                count += 1
                if count == 2:
                    os._exit(23)

            store.directory.sync = exit_after_publish
        store.write(prepared)
        os._exit(24)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 23
    if phase == "during_write":
        assert store.recover(prepared.receipt) == "temporary_only"
        assert store.discard_temporary(prepared.receipt)
    else:
        assert store.recover(prepared.receipt) == "published"
        assert store.read(prepared.receipt) == document


def test_no_network_calls(store, context, document, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Evidence primitives must not contact targets")

    for name in ("getaddrinfo", "gethostbyname", "create_connection"):
        monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    receipt = store.write(store.prepare(context, document))
    assert store.read(receipt) == document
    assert store.recover(receipt) == "published"
