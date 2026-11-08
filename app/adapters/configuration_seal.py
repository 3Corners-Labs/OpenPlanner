"""Sealing the secrets of a configuration document under a passphrase (ADR 0099 point 8).

A secret in a document is ``{"sealed": "<base64>"}``: AES-256-GCM under a key derived from the
passphrase with scrypt, with the object's kind, natural key and the field name as associated
data, so a sealed value cannot be moved to another object or field. The header of the document
carries the salt and a verifier (a fixed text sealed under the same key) so a wrong passphrase
is told from a damaged file. The scrypt parameters are fixed here and the header is *checked*
against them, never obeyed: a hostile document cannot ask for a different cost.
"""

import base64
import binascii
import os
from collections.abc import Mapping
from dataclasses import replace

from cryptography.exceptions import InvalidTag

from app.adapters.crypto import SecretBox
from app.adapters.db.configuration_package import (
    SALT_BYTES,
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    package_box,
)
from app.domain.configuration_document import Document
from app.domain.configuration_tree import Address, Entity, any_field, drop_fields, map_fields
from app.domain.configuration_values import Json, Sealed, Secret, Value
from app.domain.errors import DomainError, ErrorCode

KDF = "scrypt"
VERIFIER_NAME = "document:verifier"
VERIFIER_TEXT = b"openplanner configuration document"


def _b64(data: bytes) -> str:
    """Return ``data`` as base64 text."""
    return base64.b64encode(data).decode()


def _unb64(text: object) -> bytes:
    """Return the bytes of a base64 text, or refuse the header."""
    if not isinstance(text, str):
        raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_INVALID, "seal")
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_INVALID, "seal") from None


def _name(kind: str, address: Address, field: str) -> str:
    """Return the associated data of one sealed value: where it sits and which field it is."""
    return f"doc:{kind}:{'/'.join(address)}:{field}"


class DocumentSealer:
    """Seals and opens the secrets of one document under one passphrase.

    Use ``DocumentSealer.new`` for an export and ``DocumentSealer.from_header`` for a document
    that is read.
    """

    def __init__(self, box: SecretBox, salt: bytes) -> None:
        """Keep the box derived from the passphrase and the salt it was derived with."""
        self._box = box
        self._salt = salt

    @classmethod
    def new(cls, passphrase: str) -> "DocumentSealer":
        """Return a sealer with a new random salt."""
        salt = os.urandom(SALT_BYTES)
        return cls(package_box(passphrase, salt), salt)

    @classmethod
    def from_header(cls, header: Mapping[str, Json], passphrase: str) -> "DocumentSealer":
        """Return the sealer a document's header describes, once ``passphrase`` opens it.

        Raises:
            DomainError: ``configuration_package_invalid`` for a header that is not ours or
                asks for other parameters; ``configuration_passphrase_wrong``.
        """
        pinned = {"kdf": KDF, "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P}
        if any(header.get(key) != value for key, value in pinned.items()):
            raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_INVALID, "seal")
        salt = _unb64(header.get("salt"))
        verifier = header.get("verifier")
        if len(salt) != SALT_BYTES or not isinstance(verifier, Mapping):
            raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_INVALID, "seal")
        sealer = cls(package_box(passphrase, salt), salt)
        try:
            sealer._box.open(
                VERIFIER_NAME, _unb64(verifier.get("nonce")), _unb64(verifier.get("ciphertext"))
            )
        except InvalidTag:
            raise DomainError(ErrorCode.CONFIGURATION_PASSPHRASE_WRONG, "passphrase") from None
        return sealer

    def header(self) -> dict[str, Json]:
        """Return the header that lets a reader derive the key and check the passphrase."""
        nonce, ciphertext = self._box.seal(VERIFIER_NAME, VERIFIER_TEXT)
        return {
            "kdf": KDF,
            "n": SCRYPT_N,
            "r": SCRYPT_R,
            "p": SCRYPT_P,
            "salt": _b64(self._salt),
            "verifier": {"nonce": _b64(nonce), "ciphertext": _b64(ciphertext)},
        }

    def seal(self, kind: str, address: Address, field: str, secret: Secret) -> Sealed:
        """Return the secret sealed for the field of the object at ``address``."""
        nonce, ciphertext = self._box.seal(_name(kind, address, field), secret.reveal().encode())
        return Sealed(_b64(bytes([len(nonce)]) + nonce + ciphertext))

    def open(self, kind: str, address: Address, field: str, sealed: Sealed) -> Secret:
        """Return the secret a sealed value holds.

        Raises:
            DomainError: ``configuration_passphrase_wrong`` for a value that does not open
                here: damaged, or sealed for another object or field.
        """
        raw = _unb64(sealed.token)
        try:
            size = raw[0]
            nonce, ciphertext = raw[1 : 1 + size], raw[1 + size :]
            plain = self._box.open(_name(kind, address, field), nonce, ciphertext)
        except (InvalidTag, IndexError, ValueError):
            raise DomainError(ErrorCode.CONFIGURATION_PASSPHRASE_WRONG, "passphrase") from None
        return Secret(plain.decode())


def seal_document(document: Document, sealer: DocumentSealer) -> Document:
    """Return the document with every opened secret sealed and the header written."""

    def change(kind: str, address: Address, field: str, value: Value) -> Value:
        return sealer.seal(kind, address, field, value) if isinstance(value, Secret) else value

    return replace(document, root=map_fields(document.root, change), seal=sealer.header())


def open_document(document: Document, passphrase: str | None) -> Document:
    """Return the document with every sealed secret opened.

    A document without sealed values needs no passphrase.

    Raises:
        DomainError: ``configuration_passphrase_short`` when sealed values are there and the
            passphrase is missing; ``configuration_passphrase_wrong`` when it does not open
            them; ``configuration_package_invalid`` for sealed values without a header.
    """
    if not any_field(document.root, lambda value: isinstance(value, Sealed)):
        return document
    if document.seal is None:
        raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_INVALID, "seal")
    if not passphrase:
        raise DomainError(ErrorCode.CONFIGURATION_PASSPHRASE_SHORT, "passphrase")
    sealer = DocumentSealer.from_header(document.seal, passphrase)

    def change(kind: str, address: Address, field: str, value: Value) -> Value:
        return sealer.open(kind, address, field, value) if isinstance(value, Sealed) else value

    return replace(document, root=map_fields(document.root, change))


def drop_secrets(entity: Entity) -> Entity:
    """Return ``entity`` without any secret field: an export without secrets."""
    return drop_fields(entity, lambda value: isinstance(value, Secret | Sealed))


class PassphraseSealing:
    """The ``DocumentSealing`` port on passphrase-derived keys: one sealer per document."""

    def seal(self, document: Document, passphrase: str) -> Document:
        """Return the document with its opened secrets sealed under ``passphrase``."""
        return seal_document(document, DocumentSealer.new(passphrase))

    def open(self, document: Document, passphrase: str | None) -> Document:
        """Return the document with its sealed secrets opened."""
        return open_document(document, passphrase)

    def strip(self, document: Document) -> Document:
        """Return the document without any secret."""
        return replace(document, root=drop_secrets(document.root), seal=None)
