"""The release script.

Nothing here builds anything: Gradle, PyInstaller and the interpreter probe are
all replaced. What is worth pinning is the decision-making around them — which
APK is picked, which files end up certified, which interpreter is used — because
every one of those has already been wrong once, and none of them is visible in a
successful build's output.
"""

import hashlib

import pytest

import build_release as br


@pytest.fixture()
def release_dir(tmp_path, monkeypatch):
    target = tmp_path / "release"
    monkeypatch.setattr(br, "RELEASE", target)
    return target


def write(path, text: str = "x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestApkBuilds:
    """Two flavours from one source tree, and the right file picked from each."""

    def _outputs(self, tmp_path, monkeypatch, names, flavour="modern", variant="release"):
        monkeypatch.setattr(br, "ANDROID", tmp_path)
        monkeypatch.setattr(br, "GRADLEW", tmp_path / "gradlew.bat")
        monkeypatch.setattr(br, "run", lambda *a, **k: None)
        outputs = tmp_path / "app" / "build" / "outputs" / "apk" / flavour / variant
        for name in names:
            write(outputs / name)
        return outputs

    def test_prefers_the_signed_apk(self, tmp_path, monkeypatch):
        """"-" sorts before ".", so plain alphabetical order picks the unsigned one."""
        self._outputs(
            tmp_path, monkeypatch, ["app-modern-release-unsigned.apk", "app-modern-release.apk"]
        )
        assert br._pick_apk("modern", "release").name == "app-modern-release.apk"

    def test_falls_back_to_unsigned_when_that_is_all_there_is(self, tmp_path, monkeypatch, capsys):
        self._outputs(tmp_path, monkeypatch, ["app-modern-release-unsigned.apk"])
        chosen = br._pick_apk("modern", "release")
        assert chosen.name == "app-modern-release-unsigned.apk"
        assert "unsigned" in capsys.readouterr().out

    def test_no_apk_is_an_error_not_a_crash(self, tmp_path, monkeypatch):
        self._outputs(tmp_path, monkeypatch, [])
        with pytest.raises(br.StepFailed):
            br._pick_apk("modern", "release")

    def test_both_flavours_are_built_and_named_apart(self, tmp_path, monkeypatch):
        """One phone-shaped APK is not a release when half the phones are older."""
        calls: list[list[str]] = []
        monkeypatch.setattr(br, "ANDROID", tmp_path)
        monkeypatch.setattr(br, "GRADLEW", tmp_path / "gradlew.bat")
        monkeypatch.setattr(br, "run", lambda cmd, **k: calls.append(cmd))
        for flavour in ("modern", "legacy"):
            write(
                tmp_path / "app" / "build" / "outputs" / "apk" / flavour / "debug"
                / f"app-{flavour}-debug.apk"
            )

        built = br.build_apks(release_build=False)

        assert [name for _, name in built] == [
            "NexusController.apk", "NexusController-legacy.apk"
        ]
        assert "assembleModernDebug" in calls[0]
        assert "assembleLegacyDebug" in calls[0]

    def test_the_apk_is_built_clean(self, tmp_path, monkeypatch):
        """An incremental build once shipped a dex missing a class, with Gradle
        reporting success and the app dying on launch."""
        calls: list[list[str]] = []
        monkeypatch.setattr(br, "ANDROID", tmp_path)
        monkeypatch.setattr(br, "GRADLEW", tmp_path / "gradlew.bat")
        monkeypatch.setattr(br, "run", lambda cmd, **k: calls.append(cmd))
        for flavour in ("modern", "legacy"):
            write(
                tmp_path / "app" / "build" / "outputs" / "apk" / flavour / "debug"
                / f"app-{flavour}-debug.apk"
            )

        br.build_apks(release_build=False)

        assert "clean" in calls[0]
        assert "--no-build-cache" in calls[0]


class TestCollect:
    def test_checksums_cover_what_is_there(self, release_dir, tmp_path):
        source = write(tmp_path / "src" / "NexusController.exe", "payload")
        br.collect([(source, "NexusController.exe")])

        expected = hashlib.sha256(b"payload").hexdigest()
        text = (release_dir / "SHA256SUMS.txt").read_text(encoding="utf-8")
        assert text == f"{expected}  NexusController.exe\n"

    def test_uses_lf_so_sha256sum_can_read_it(self, release_dir, tmp_path):
        """A CRLF makes "sha256sum -c" treat the \\r as part of the file name."""
        br.collect([(write(tmp_path / "a.exe"), "NexusController.exe")])
        raw = (release_dir / "SHA256SUMS.txt").read_bytes()
        assert b"\r" not in raw

    def test_artefacts_from_an_earlier_run_are_kept(self, release_dir, tmp_path):
        """--exe-only must not quietly delete the APK someone built yesterday."""
        write(release_dir / "NexusController.apk", "old apk")
        br.collect([(write(tmp_path / "new.exe", "new exe"), "NexusController.exe")])

        assert (release_dir / "NexusController.apk").read_text(encoding="utf-8") == "old apk"
        listed = (release_dir / "SHA256SUMS.txt").read_text(encoding="utf-8")
        assert "NexusController.apk" in listed and "NexusController.exe" in listed

    def test_kept_artefacts_are_called_out(self, release_dir, tmp_path, capsys):
        """They may come from another commit and the file cannot say so."""
        write(release_dir / "NexusController.apk", "old apk")
        br.collect([(write(tmp_path / "new.exe"), "NexusController.exe")])
        assert "not rebuilt now: NexusController.apk" in capsys.readouterr().out

    def test_a_rebuild_replaces_the_old_file(self, release_dir, tmp_path):
        write(release_dir / "NexusController.exe", "stale")
        br.collect([(write(tmp_path / "fresh.exe", "fresh"), "NexusController.exe")])
        assert (release_dir / "NexusController.exe").read_text(encoding="utf-8") == "fresh"


    def test_no_checksums_when_something_could_not_be_put_in_place(self, release_dir, tmp_path, monkeypatch):
        """The file claims a set that was never assembled."""
        write(release_dir / "NexusController.exe", "locked")
        monkeypatch.setattr(br, "remove", _refuse_to_remove(release_dir / "NexusController.exe"))

        with pytest.raises(br.StepFailed):
            br.collect([(write(tmp_path / "new.exe"), "NexusController.exe")])

        assert not (release_dir / "SHA256SUMS.txt").exists()


def _refuse_to_remove(locked):
    """Stand-in for a file Windows will not let go of."""
    def remove(path):
        if path == locked:
            raise br.StepFailed(f"{path} is in use")
        real = path
        if real.exists():
            real.unlink()
    return remove


class TestInterpreter:
    def test_prefers_the_project_venv(self, tmp_path, monkeypatch):
        """`python build_release.py` otherwise dies on a pytest plugin it lacks."""
        venv = write(tmp_path / "python.exe")
        monkeypatch.setattr(br, "VENV_PYTHON", venv)
        monkeypatch.setattr(br.sys, "executable", str(tmp_path / "other.exe"))
        assert br.interpreter() == str(venv)

    def test_falls_back_to_the_running_one(self, tmp_path, monkeypatch):
        monkeypatch.setattr(br, "VENV_PYTHON", tmp_path / "absent.exe")
        assert br.interpreter() == br.sys.executable


class TestModuleCheck:
    def test_names_what_is_missing_and_where_from(self):
        with pytest.raises(br.StepFailed) as excinfo:
            br.check_modules(br.sys.executable, {"definitely_not_installed": "requirements.txt"})
        message = str(excinfo.value)
        assert "definitely_not_installed" in message
        assert "requirements.txt" in message

    def test_passes_when_everything_is_present(self):
        br.check_modules(br.sys.executable, {"json": "stdlib"})

    def test_an_unusable_interpreter_is_reported(self):
        with pytest.raises(br.StepFailed):
            br.check_modules("no-such-python-anywhere", {"json": "stdlib"})
