"""Sealing the secrets of a configuration document under a passphrase (ADR 0099 point 8)."""

import copy
from dataclasses import replace

import pytest

from app.adapters.configuration_seal import (
    DocumentSealer,
    PassphraseSealing,
    drop_secrets,
    open_document,
    seal_document,
)
from app.domain.configuration_document import Document, Mode, Scope
from app.domain.configuration_tree import Entity
from app.domain.configuration_values import Sealed, Secret
from app.domain.errors import DomainError, ErrorCode
from tests.support.configuration import FORMAT, box, installation, shop

PASSPHRASE = "a long enough passphrase"  # noqa: S105 - a test passphrase


def document(secret: Secret | Sealed | None = None, *, slug: str = "b") -> Document:
    values = {} if secret is None else {"password": secret}
    entity = replace(box(slug, **values), fields={**box(slug, **values).fields})
    root = installation(shop("s", entity))
    return Document("test/v1", Mode.MERGE, Scope(), root, "digest", FORMAT)


def secret_of(doc: Document) -> object:
    (shops,) = (c for c in doc.root.children["shops"])
    (found,) = shops.children["boxes"]
    return found.fields["password"]


def test_a_secret_is_sealed_and_opens_again_under_the_same_passphrase() -> None:
    sealer = DocumentSealer.new(PASSPHRASE)

    sealed = seal_document(document(Secret("hunter2-hunter2")), sealer)
    opened = open_document(sealed, PASSPHRASE)

    assert isinstance(secret_of(sealed), Sealed)
    assert "hunter2" not in repr(secret_of(sealed))
    assert secret_of(opened) == Secret("hunter2-hunter2")
    assert sealed.seal is not None


def test_a_wrong_passphrase_is_told_by_the_verifier() -> None:
    sealed = seal_document(document(Secret("x")), DocumentSealer.new(PASSPHRASE))

    with pytest.raises(DomainError) as error:
        open_document(sealed, "another passphrase!!")

    assert error.value.code is ErrorCode.CONFIGURATION_PASSPHRASE_WRONG


def test_sealed_values_need_a_passphrase_and_a_header() -> None:
    sealed = seal_document(document(Secret("x")), DocumentSealer.new(PASSPHRASE))

    with pytest.raises(DomainError) as missing:
        open_document(sealed, None)
    with pytest.raises(DomainError) as headerless:
        open_document(replace(sealed, seal=None), PASSPHRASE)

    assert missing.value.code is ErrorCode.CONFIGURATION_PASSPHRASE_SHORT
    assert headerless.value.code is ErrorCode.CONFIGURATION_PACKAGE_INVALID
    assert open_document(document(), None) is not None


def test_a_sealed_value_cannot_be_moved_to_another_object() -> None:
    sealer = DocumentSealer.new(PASSPHRASE)
    sealed = seal_document(document(Secret("x"), slug="b"), sealer)
    moved = document(secret_of(sealed), slug="other")  # type: ignore[arg-type]

    with pytest.raises(DomainError) as error:
        open_document(replace(moved, seal=sealed.seal), PASSPHRASE)

    assert error.value.code is ErrorCode.CONFIGURATION_PASSPHRASE_WRONG


def test_a_sealed_value_cannot_be_moved_to_another_field() -> None:
    sealer = DocumentSealer.new(PASSPHRASE)
    token = sealer.seal("box", ("s", "b"), "password", Secret("x"))

    with pytest.raises(DomainError):
        sealer.open("box", ("s", "b"), "name", token)
    with pytest.raises(DomainError):
        sealer.open("other", ("s", "b"), "password", token)
    assert sealer.open("box", ("s", "b"), "password", token) == Secret("x")


def test_a_damaged_token_is_refused() -> None:
    sealer = DocumentSealer.new(PASSPHRASE)

    for token in ("AAAA", "", "***"):
        with pytest.raises(DomainError):
            sealer.open("box", ("s", "b"), "password", Sealed(token))


def test_the_header_is_checked_never_obeyed() -> None:
    header = DocumentSealer.new(PASSPHRASE).header()

    for key, value in (("n", 2**10), ("r", 1), ("p", 4), ("kdf", "pbkdf2")):
        with pytest.raises(DomainError) as error:
            DocumentSealer.from_header({**header, key: value}, PASSPHRASE)
        assert error.value.code is ErrorCode.CONFIGURATION_PACKAGE_INVALID
    for broken in ({**header, "salt": "AAAA"}, {**header, "verifier": "x"}, {**header, "salt": 5}):
        with pytest.raises(DomainError):
            DocumentSealer.from_header(broken, PASSPHRASE)  # type: ignore[arg-type]
    assert DocumentSealer.from_header(copy.deepcopy(header), PASSPHRASE) is not None


def test_two_seals_of_the_same_secret_differ() -> None:
    sealer = DocumentSealer.new(PASSPHRASE)

    one = sealer.seal("box", ("s", "b"), "password", Secret("x"))
    two = sealer.seal("box", ("s", "b"), "password", Secret("x"))

    assert one != two


def test_an_export_without_secrets_drops_every_secret_and_the_header() -> None:
    sealing = PassphraseSealing()
    sealed = sealing.seal(document(Secret("x")), PASSPHRASE)

    stripped = sealing.strip(sealed)

    assert stripped.seal is None
    assert "password" not in stripped.root.children["shops"][0].children["boxes"][0].fields
    assert sealing.open(sealed, PASSPHRASE) is not None


def test_dropping_secrets_leaves_everything_else() -> None:
    entity = Entity("x", fields={"a": 1, "s": Secret("x"), "t": Sealed("QQ==")})

    assert drop_secrets(entity).fields == {"a": 1}
