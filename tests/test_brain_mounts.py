"""BRAIN_MOUNTS limits which brain paths reach the container."""

from src.runner import _brain_volumes


def _brain(tmp_path):
    b = tmp_path / "brain"
    (b / "skills").mkdir(parents=True)
    (b / "Permanent notes").mkdir()
    (b / "Permanent notes" / "Extended Family ID Numbers.md").write_text("secret")
    (b / "JARVIS Identity.md").write_text("me")
    (b / "memory.json").write_text("{}")
    return b


def test_unset_mounts_whole_brain(tmp_path):
    b = _brain(tmp_path)
    assert _brain_volumes(b, "") == [f"{b}:/workspace/brain:rw"]


def test_listed_paths_only_with_modes(tmp_path):
    b = _brain(tmp_path)
    specs = _brain_volumes(b, "JARVIS Identity.md:ro, skills ,memory.json")
    r = b.resolve()
    assert specs == [
        f"{r}/JARVIS Identity.md:/workspace/brain/JARVIS Identity.md:ro",
        f"{r}/skills:/workspace/brain/skills:rw",
        f"{r}/memory.json:/workspace/brain/memory.json:rw",
    ]
    assert not any("Permanent notes" in s for s in specs)


def test_escape_and_missing_file_skipped_missing_dir_created(tmp_path):
    b = _brain(tmp_path)
    specs = _brain_volumes(b, "../outside,nope.md,FRIDAY")
    assert specs == [f"{b.resolve()}/FRIDAY:/workspace/brain/FRIDAY:rw"]
    assert (b / "FRIDAY").is_dir()


def test_no_brain_dir(tmp_path):
    assert _brain_volumes(None, "skills") == []
    assert _brain_volumes(tmp_path / "missing", "skills") == []
