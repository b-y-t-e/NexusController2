"""Updating the Windows build from the GitHub releases API.

No socket is opened here and no real executable is replaced: the opener is a
fake and the swap runs on files in a tmp_path. What is worth pinning is
everything that decides — which version is newer, which URL is ours, whether the
bytes are the bytes the release says they are — plus the swap's rollback, which
is the one path where a bug leaves the user with no working app at all and no
way to be told why.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from nexus_server import updates


def release_document(tag="v2.1.0", assets=None, body="notes"):
    if assets is None:
        assets = {
            "NexusController.exe": updates.DOWNLOAD_PREFIX + f"{tag}/NexusController.exe",
            "SHA256SUMS.txt": updates.DOWNLOAD_PREFIX + f"{tag}/SHA256SUMS.txt",
        }
    return {
        "tag_name": tag,
        "body": body,
        "assets": [{"name": name, "browser_download_url": url} for name, url in assets.items()],
    }


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self, size: int = -1) -> bytes:
        return self.payload if size is None or size < 0 else self.payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Answers the URLs it was given and 404s everything else."""

    def __init__(self, routes: dict) -> None:
        self.routes = routes
        self.seen: list[str] = []

    def __call__(self, request, timeout=None):
        url = getattr(request, "full_url", request)
        self.seen.append(url)
        self.last_request = request
        answer = self.routes.get(url)
        if answer is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer)


def api_routes(document=None, exe=b"new build", extra_sums=""):
    """The two-and-a-bit URLs a successful update reads, with matching checksums."""
    import hashlib

    document = document if document is not None else release_document()
    digest = hashlib.sha256(exe).hexdigest()
    sums = f"{digest}  NexusController.exe\n{extra_sums}"
    tag = document["tag_name"]
    return {
        updates.RELEASE_API: json.dumps(document).encode(),
        updates.DOWNLOAD_PREFIX + f"{tag}/NexusController.exe": exe,
        updates.DOWNLOAD_PREFIX + f"{tag}/SHA256SUMS.txt": sums.encode(),
    }


class TestVersions:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("2.1.0", (2, 1, 0)),
            ("v2.1.0", (2, 1, 0)),
            ("V2.1.0", (2, 1, 0)),
            ("2.1", (2, 1, 0)),
            ("3", (3, 0, 0)),
            ("  2.1.0  ", (2, 1, 0)),
            ("2.1.0-legacy", (2, 1, 0)),
        ],
    )
    def test_reads_the_shapes_this_project_produces(self, text, expected):
        """The legacy flavour really does call itself "2.1.0-legacy"."""
        assert updates.parse_version(text) == expected

    @pytest.mark.parametrize("text", ["", "latest", "2.1.0.0", "two.one", None, "v", "2.x"])
    def test_refuses_anything_else(self, text):
        assert updates.parse_version(text) is None

    def test_compares_numerically(self):
        """"2.10.0" < "2.9.0" as text — which would offer a downgrade, forever."""
        assert updates.is_newer("2.9.0", "2.10.0")
        assert not updates.is_newer("2.10.0", "2.9.0")

    def test_the_same_version_is_not_an_update(self):
        assert not updates.is_newer("2.0.0", "2.0.0")

    def test_an_unreadable_version_is_never_newer(self):
        """Not knowing is not a reason to replace the running program."""
        assert not updates.is_newer("2.0.0", "nightly")
        assert not updates.is_newer("nightly", "2.0.0")


class TestParsingTheRelease:
    def test_reads_tag_assets_and_notes(self):
        release = updates.parse_release(release_document())
        assert release.tag == "v2.1.0"
        assert release.version == "2.1.0"
        assert release.url("NexusController.exe").endswith("/NexusController.exe")
        assert release.notes == "notes"

    def test_drops_assets_served_from_anywhere_else(self):
        """The URL comes out of a JSON document and decides what we then run."""
        document = release_document(
            assets={
                "NexusController.exe": "https://example.invalid/NexusController.exe",
                "SHA256SUMS.txt": updates.DOWNLOAD_PREFIX + "v2.1.0/SHA256SUMS.txt",
            }
        )
        release = updates.parse_release(document)
        assert release.url("NexusController.exe") is None
        assert release.url("SHA256SUMS.txt") is not None

    def test_a_lookalike_prefix_is_not_our_repository(self):
        document = release_document(
            assets={"NexusController.exe": "https://github.com/evil/NexusController2-x/releases/download/v9/NexusController.exe"}
        )
        assert updates.parse_release(document).url("NexusController.exe") is None

    @pytest.mark.parametrize("document", [None, [], "release", {}, {"tag_name": "latest"}])
    def test_refuses_a_document_it_cannot_trust(self, document):
        with pytest.raises(updates.UpdateError):
            updates.parse_release(document)

    def test_survives_junk_among_the_assets(self):
        document = release_document()
        document["assets"].extend([None, "text", {"name": 5}])
        assert updates.parse_release(document).url("SHA256SUMS.txt") is not None


class TestChecksums:
    def test_reads_the_format_the_release_ships(self):
        text = "a" * 64 + "  NexusController.exe\n" + "b" * 64 + "  NexusController.apk\n"
        assert updates.parse_checksums(text) == {
            "NexusController.exe": "a" * 64,
            "NexusController.apk": "b" * 64,
        }

    def test_accepts_the_binary_mode_star(self):
        assert updates.parse_checksums("c" * 64 + " *NexusController.exe") == {
            "NexusController.exe": "c" * 64
        }

    def test_ignores_lines_that_are_not_checksums(self):
        assert updates.parse_checksums("# comment\n\nnot a hash  file\n") == {}

    def test_verify_passes_the_matching_payload(self):
        import hashlib

        payload = b"payload"
        text = f"{hashlib.sha256(payload).hexdigest()}  NexusController.exe"
        updates.verify(payload, "NexusController.exe", text)

    def test_verify_refuses_a_payload_that_does_not_match(self):
        """A truncated download and a tampered one look identical from here."""
        text = "d" * 64 + "  NexusController.exe"
        with pytest.raises(updates.UpdateError, match="checksum"):
            updates.verify(b"payload", "NexusController.exe", text)

    def test_verify_refuses_a_payload_nothing_vouches_for(self):
        with pytest.raises(updates.UpdateError, match="does not mention"):
            updates.verify(b"payload", "NexusController.exe", "")


class TestFetching:
    def test_reads_the_latest_release(self):
        opener = FakeOpener(api_routes())
        release = updates.fetch_latest(opener=opener)
        assert release.version == "2.1.0"
        assert opener.last_request.get_header("User-agent") == updates.USER_AGENT

    def test_no_releases_yet_is_not_an_error(self):
        """A repository with no release answers 404, and that is just "nothing"."""
        assert updates.fetch_latest(opener=FakeOpener({})) is None

    def test_being_offline_is_reported_rather_than_raised_as_anything_else(self):
        opener = FakeOpener({updates.RELEASE_API: urllib.error.URLError("no route to host")})
        with pytest.raises(updates.UpdateError, match="could not reach GitHub"):
            updates.fetch_latest(opener=opener)

    def test_a_server_error_is_not_read_as_no_release(self):
        opener = FakeOpener(
            {updates.RELEASE_API: urllib.error.HTTPError(updates.RELEASE_API, 500, "boom", {}, None)}
        )
        with pytest.raises(updates.UpdateError, match="500"):
            updates.fetch_latest(opener=opener)

    def test_unreadable_json_is_an_error_not_a_crash(self):
        opener = FakeOpener({updates.RELEASE_API: b"<html>a proxy login page</html>"})
        with pytest.raises(updates.UpdateError, match="unreadable JSON"):
            updates.fetch_latest(opener=opener)

    def test_an_answer_that_never_stops_is_refused(self):
        """Otherwise a wrong URL streams into memory until the machine gives up."""
        opener = FakeOpener({updates.RELEASE_API: b"x" * (updates.MAX_JSON_BYTES + 10)})
        with pytest.raises(updates.UpdateError, match="larger than expected"):
            updates.fetch_latest(opener=opener)


class TestDownload:
    def test_returns_the_asset_when_it_matches_its_checksum(self):
        opener = FakeOpener(api_routes(exe=b"new build"))
        release = updates.fetch_latest(opener=opener)
        assert updates.download(release, opener=opener) == b"new build"

    def test_refuses_an_asset_that_does_not_match(self):
        routes = api_routes(exe=b"new build")
        routes[updates.DOWNLOAD_PREFIX + "v2.1.0/NexusController.exe"] = b"something else"
        opener = FakeOpener(routes)
        release = updates.fetch_latest(opener=opener)
        with pytest.raises(updates.UpdateError, match="checksum"):
            updates.download(release, opener=opener)

    def test_a_release_without_the_windows_build_is_not_installable(self):
        document = release_document(
            assets={"SHA256SUMS.txt": updates.DOWNLOAD_PREFIX + "v2.1.0/SHA256SUMS.txt"}
        )
        release = updates.parse_release(document)
        with pytest.raises(updates.UpdateError, match="no NexusController.exe"):
            updates.download(release, opener=FakeOpener({}))

    def test_a_release_without_checksums_is_refused_before_anything_is_fetched(self):
        document = release_document(
            assets={"NexusController.exe": updates.DOWNLOAD_PREFIX + "v2.1.0/NexusController.exe"}
        )
        opener = FakeOpener({})
        with pytest.raises(updates.UpdateError, match="SHA256SUMS"):
            updates.download(updates.parse_release(document), opener=opener)
        assert opener.seen == []


class TestWhereItLives:
    def test_a_source_checkout_has_nothing_to_replace(self):
        assert updates.running_executable() is None

    def test_a_frozen_build_points_at_its_own_exe(self, tmp_path, monkeypatch):
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"old")
        monkeypatch.setattr(updates.sys, "frozen", True, raising=False)
        monkeypatch.setattr(updates.sys, "executable", str(exe))
        assert updates.running_executable() == exe.resolve()

    def test_the_backup_keeps_the_extension(self, tmp_path):
        """"NexusController.old" with no .exe would not be runnable if restored."""
        assert updates.backup_for(tmp_path / "NexusController.exe").name == "NexusController.old.exe"

    def test_writable_says_yes_for_a_directory_we_own(self, tmp_path):
        assert updates.writable(tmp_path / "NexusController.exe")

    def test_writable_says_no_when_the_directory_is_not_there(self, tmp_path):
        assert not updates.writable(tmp_path / "nope" / "NexusController.exe")

    def test_the_probe_leaves_nothing_behind(self, tmp_path):
        updates.writable(tmp_path / "NexusController.exe")
        assert list(tmp_path.iterdir()) == []


class TestInstall:
    def test_puts_the_new_build_in_place_and_keeps_the_old_one(self, tmp_path):
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"old build")

        backup = updates.install(b"new build", exe)

        assert exe.read_bytes() == b"new build"
        assert backup.read_bytes() == b"old build"
        assert not (tmp_path / "NexusController.exe.new").exists()

    def test_a_leftover_backup_does_not_block_the_next_update(self, tmp_path):
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"old build")
        updates.backup_for(exe).write_bytes(b"from an update two versions ago")

        updates.install(b"new build", exe)

        assert exe.read_bytes() == b"new build"
        assert updates.backup_for(exe).read_bytes() == b"old build"

    def test_a_failure_halfway_leaves_a_working_app(self, tmp_path, monkeypatch):
        """The one bug here that cannot be recovered from by the user."""
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"old build")
        calls = []
        real_replace = updates.os.replace

        def fail_on_the_second(src, dst):
            calls.append((src, dst))
            if len(calls) == 2:
                raise OSError("antivirus grabbed it")
            return real_replace(src, dst)

        monkeypatch.setattr(updates.os, "replace", fail_on_the_second)
        with pytest.raises(updates.UpdateError):
            updates.install(b"new build", exe)

        assert exe.read_bytes() == b"old build"
        assert not (tmp_path / "NexusController.exe.new").exists()

    def test_a_directory_it_cannot_write_to_is_reported_not_raised_raw(self, tmp_path):
        exe = tmp_path / "missing" / "NexusController.exe"
        with pytest.raises(updates.UpdateError, match="could not write"):
            updates.install(b"new build", exe)


class TestClearBackup:
    def test_removes_what_the_last_update_left(self, tmp_path, monkeypatch):
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"current")
        updates.backup_for(exe).write_bytes(b"previous")
        assert updates.clear_backup(exe)
        assert not updates.backup_for(exe).exists()

    def test_nothing_to_remove_is_not_a_problem(self, tmp_path):
        assert not updates.clear_backup(tmp_path / "NexusController.exe")

    def test_a_locked_leftover_does_not_stop_the_app_starting(self, tmp_path, monkeypatch):
        """An antivirus still reading yesterday's build is not today's problem."""
        exe = tmp_path / "NexusController.exe"
        updates.backup_for(exe).write_bytes(b"previous")
        monkeypatch.setattr(
            updates.Path, "unlink", lambda self, **k: (_ for _ in ()).throw(PermissionError("in use"))
        )
        assert not updates.clear_backup(exe)

    def test_from_a_source_checkout_there_is_nothing_to_clear(self):
        assert not updates.clear_backup()


class TestRelaunch:
    def test_starts_the_new_build(self, tmp_path):
        exe = tmp_path / "NexusController.exe"
        spawned = []
        updates.relaunch(exe, spawn=lambda *a, **k: spawned.append((a, k)))
        assert spawned[0][0][0] == [str(exe)]
        assert spawned[0][1]["cwd"] == str(tmp_path)

    def test_an_update_that_cannot_be_started_says_so(self, tmp_path):
        """The swap already happened, so silence here looks like a dead app."""
        def refuse(*args, **kwargs):
            raise OSError("not executable")

        with pytest.raises(updates.UpdateError, match="could not be started"):
            updates.relaunch(tmp_path / "NexusController.exe", spawn=refuse)
