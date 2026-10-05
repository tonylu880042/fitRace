from hub_server.usecases.avatar_store import AvatarStore, is_valid_avatar_id

WEBP = b"RIFF\x04\x00\x00\x00WEBPxxxx"


def test_save_returns_a_32_hex_id_and_persists_the_bytes(tmp_path):
    store = AvatarStore(tmp_path / "avatars")

    avatar_id = store.save(WEBP)

    assert is_valid_avatar_id(avatar_id)
    path = store.path_for(avatar_id)
    assert path is not None and path.read_bytes() == WEBP
    assert path.parent == tmp_path / "avatars"


def test_each_save_gets_its_own_id(tmp_path):
    store = AvatarStore(tmp_path)
    assert store.save(WEBP) != store.save(WEBP)


def test_path_for_rejects_malformed_ids(tmp_path):
    store = AvatarStore(tmp_path)
    store.save(WEBP)
    for bad in [
        "../" + "a" * 29,
        "..",
        "a" * 31,
        "a" * 33,
        "A" * 32,
        "g" * 32,
        "a" * 31 + "/",
        "",
        None,
    ]:
        assert store.path_for(bad) is None


def test_path_for_unknown_valid_id_is_none(tmp_path):
    assert AvatarStore(tmp_path).path_for("0" * 32) is None
