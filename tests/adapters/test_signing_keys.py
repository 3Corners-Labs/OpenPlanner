"""EC P-256 key pairs for ``private_key_jwt`` (ADR 0083 point 6)."""

import jwt

from app.adapters.signing_keys import EcSigningKeys, thumbprint


def test_a_generated_key_signs_what_its_published_jwk_verifies() -> None:
    generated = EcSigningKeys().generate()

    token = jwt.encode({"iss": "openplanner"}, generated.private_pem, algorithm="ES256")
    public = jwt.PyJWK.from_dict({**generated.public_jwk, "kid": generated.kid})
    assert jwt.decode(token, public.key, algorithms=["ES256"])["iss"] == "openplanner"
    assert generated.public_jwk == {
        "kty": "EC",
        "crv": "P-256",
        "x": generated.public_jwk["x"],
        "y": generated.public_jwk["y"],
        "use": "sig",
        "alg": "ES256",
    }
    assert "d" not in generated.public_jwk
    assert "BEGIN PRIVATE KEY" in generated.private_pem


def test_the_key_id_is_the_rfc_7638_thumbprint_so_two_keys_never_collide() -> None:
    keys = EcSigningKeys()
    one, two = keys.generate(), keys.generate()

    assert one.kid == thumbprint(dict(one.public_jwk))
    assert one.kid != two.kid
    assert len(one.kid) == 43  # base64url of a SHA-256, without padding
    assert "private" not in repr(one)
