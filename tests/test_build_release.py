"""The release script.

Nothing here builds anything: Gradle, PyInstaller and the interpreter probe are
all replaced. What is worth pinning is the decision-making around them — which
APK is picked, which files end up certified, which interpreter is used — because
every one of those has already been wrong once, and none of them is visible in a
successful build's output.
"""

import hashlib
import subprocess

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

    def test_an_unsigned_release_is_the_end_of_the_build(self, tmp_path, monkeypatch):
        """No phone installs one, so shipping it means the release is broken.

        Gradle produces this exact file when it cannot find the key, and it does
        so without failing — the name is the only sign.
        """
        self._outputs(tmp_path, monkeypatch, ["app-modern-release-unsigned.apk"])
        with pytest.raises(br.StepFailed, match="NEXUS_KEYSTORE"):
            br._pick_apk("modern", "release")

    def test_a_debug_build_is_not_held_to_that(self, tmp_path, monkeypatch):
        """--debug-apk exists to try the pipeline without the key."""
        self._outputs(
            tmp_path, monkeypatch, ["app-modern-debug.apk"], variant="debug"
        )
        assert br._pick_apk("modern", "debug").name == "app-modern-debug.apk"

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

        built = br.build_apks(debug_build=True)

        assert [name for _, name in built] == [
            "NexusController-debug.apk", "NexusController-legacy-debug.apk"
        ]
        assert "assembleModernDebug" in calls[0]
        assert "assembleLegacyDebug" in calls[0]

    def test_a_debug_build_never_takes_a_release_name(self, tmp_path, monkeypatch):
        """release/ really did fill up with debug APKs called NexusController.apk.

        Under the release name a debug build is indistinguishable from the real
        thing — same file name, same line in SHA256SUMS.txt — and the only way
        anybody finds out is a phone refusing to update, months later.
        """
        monkeypatch.setattr(br, "ANDROID", tmp_path)
        monkeypatch.setattr(br, "GRADLEW", tmp_path / "gradlew.bat")
        monkeypatch.setattr(br, "run", lambda *a, **k: None)
        for flavour in ("modern", "legacy"):
            write(
                tmp_path / "app" / "build" / "outputs" / "apk" / flavour / "debug"
                / f"app-{flavour}-debug.apk"
            )

        names = [name for _, name in br.build_apks(debug_build=True)]

        assert all("debug" in name for name in names)
        assert "NexusController.apk" not in names

    def test_release_is_the_default(self, tmp_path, monkeypatch):
        """A debug APK is signed with a throwaway key that differs per machine and
        per CI run, so the phone treats every build as a different app and refuses
        to update in place. Shipping one has to take a flag."""
        calls: list[list[str]] = []
        monkeypatch.setattr(br, "ANDROID", tmp_path)
        monkeypatch.setattr(br, "GRADLEW", tmp_path / "gradlew.bat")
        monkeypatch.setattr(br, "run", lambda cmd, **k: calls.append(cmd))
        for flavour in ("modern", "legacy"):
            write(
                tmp_path / "app" / "build" / "outputs" / "apk" / flavour / "release"
                / f"app-{flavour}-release.apk"
            )

        br.build_apks()

        assert "assembleModernRelease" in calls[0]
        assert "assembleLegacyRelease" in calls[0]

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

        br.build_apks(debug_build=True)

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

    def test_noise_on_stdout_is_not_mistaken_for_a_module_name(self, monkeypatch):
        """stdout belongs to the interpreter too, not only to our one print.

        A sitecustomize, a conda banner, a warning the interpreter decided to put
        on stdout — any of them used to be split on commas and looked up in the
        table of required modules, and the KeyError that followed replaced the
        careful sentence this function exists to print with a traceback.
        """
        def noisy(*args, **kwargs):
            return subprocess.CompletedProcess(
                args, 0,
                stdout=f"conda: activating, base\n{br.PROBE_MARKER}pytest\n",
                stderr="",
            )

        monkeypatch.setattr(br.subprocess, "run", noisy)
        with pytest.raises(br.StepFailed) as excinfo:
            br.check_modules("python", {"pytest": "requirements-dev.txt"})
        message = str(excinfo.value)
        assert "pytest" in message and "requirements-dev.txt" in message
        assert "conda" not in message

    def test_an_answer_that_never_arrives_is_not_silence(self, monkeypatch):
        """A probe that exits 0 while printing nothing has told us nothing."""
        def mute(*args, **kwargs):
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(br.subprocess, "run", mute)
        with pytest.raises(br.StepFailed):
            br.check_modules("python", {"pytest": "requirements-dev.txt"})

    def test_a_probe_that_crashes_is_not_read_as_success(self, monkeypatch):
        """An interpreter that ran but failed is not an interpreter that has everything.

        The probe reports what is missing on stdout, so a crash — a broken venv,
        an interpreter that cannot import its own stdlib — leaves stdout empty,
        which reads exactly like "nothing is missing". The build then went on and
        failed much later, somewhere that explained none of it.
        """
        def crashed(*args, **kwargs):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="Fatal Python error")

        monkeypatch.setattr(br.subprocess, "run", crashed)
        with pytest.raises(br.StepFailed) as excinfo:
            br.check_modules("python", {"json": "stdlib"})
        assert "Fatal Python error" in str(excinfo.value)


class TestTheWorkflowAgrees:
    """The tag build and the local build must produce the same thing.

    ``.github/workflows/release.yml`` is a second implementation of this script,
    and the two have already drifted once — the workflow's staging step copied
    paths that upload-artifact had not produced, so every tagged build failed
    while the local one was fine. These are the cheap checks that would have
    caught it, and the expensive one — actually running the workflow — cannot be
    part of a 2-second suite.
    """

    WORKFLOW = (br.ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    def test_both_build_the_release_variant(self):
        assert "assembleModernRelease assembleLegacyRelease" in self.WORKFLOW

    def test_the_workflow_stages_the_paths_that_variant_produces(self):
        for flavour, _ in br.APK_FLAVOURS:
            assert f"apk/{flavour}/release/app-{flavour}-release.apk" in self.WORKFLOW

    def test_the_workflow_ships_the_names_this_script_ships(self):
        for _, name in br.APK_FLAVOURS:
            assert f"staging/{name}" in self.WORKFLOW

    def test_the_signing_key_is_required_rather_than_assumed(self):
        """Without the secret, Gradle emits an unsigned APK and says nothing."""
        assert "NEXUS_KEYSTORE_BASE64" in self.WORKFLOW
        assert "signed by the wrong key" in self.WORKFLOW
