from app.gmail.crypto import decrypt_token, encrypt_token


def test_encrypt_then_decrypt_round_trips() -> None:
    plaintext = "ya29.fake-access-token"
    ciphertext = encrypt_token(plaintext)
    assert ciphertext != plaintext
    assert decrypt_token(ciphertext) == plaintext


def test_encrypted_output_does_not_contain_the_plaintext() -> None:
    plaintext = "1//fake-refresh-token-value"
    ciphertext = encrypt_token(plaintext)
    assert plaintext not in ciphertext
