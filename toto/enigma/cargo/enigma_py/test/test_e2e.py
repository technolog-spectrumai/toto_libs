from enigma_py import EnigmaEngine


def test_two_party_mls_roundtrip():
    alice_state = bytes(
        EnigmaEngine.create_group_state("test-room", "alice")
    )

    bob_state = bytes(
        EnigmaEngine.create_empty_state("test-room", "bob")
    )

    bob_state, bob_key_package = EnigmaEngine.key_package_from_state(
        bob_state
    )

    alice_state, welcome, commit = EnigmaEngine.add_member_from_state(
        bytes(alice_state),
        bytes(bob_key_package),
    )

    bob_state = EnigmaEngine.join_from_welcome_from_state(
        bytes(bob_state),
        bytes(welcome),
    )

    # The new member must not process the commit.
    # Welcome already places Bob into the post-add epoch.
    assert commit

    alice_state, alice_ciphertext = EnigmaEngine.encrypt_app_from_state(
        bytes(alice_state),
        b"hello bob",
    )

    bob_state, bob_plaintext = EnigmaEngine.process_message_from_state(
        bytes(bob_state),
        bytes(alice_ciphertext),
    )

    assert bytes(bob_plaintext) == b"hello bob"

    bob_state, bob_ciphertext = EnigmaEngine.encrypt_app_from_state(
        bytes(bob_state),
        b"hello alice",
    )

    alice_state, alice_plaintext = EnigmaEngine.process_message_from_state(
        bytes(alice_state),
        bytes(bob_ciphertext),
    )

    assert bytes(alice_plaintext) == b"hello alice"

    assert len(bytes(alice_state)) > 0
    assert len(bytes(bob_state)) > 0


def test_cannot_process_own_message():
    alice_state = bytes(
        EnigmaEngine.create_group_state("self-test-room", "alice")
    )

    alice_state, ciphertext = EnigmaEngine.encrypt_app_from_state(
        alice_state,
        b"hello myself",
    )

    try:
        EnigmaEngine.process_message_from_state(
            bytes(alice_state),
            bytes(ciphertext),
        )
    except RuntimeError as exc:
        assert "Cannot decrypt own messages" in str(exc)
    else:
        raise AssertionError("Expected processing own MLS message to fail")