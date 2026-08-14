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
import urllib.request

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
        """A real stream: what has been read is gone, so a loop over it ends."""
        if size is None or size < 0:
            chunk, self.payload = self.payload, b""
        else:
            chunk, self.payload = self.payload[:size], self.payload[size:]
        return chunk

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

    @pytest.mark.parametrize("text", ["9999999999.0.0", "1.2.1234567890"])
    def test_a_number_too_big_for_the_other_side_is_not_a_version_here_either(self, text):
        """Python's int has no ceiling and Kotlin's toIntOrNull() stops at 2^31,
        so without a shared cap the two would disagree about what a version is."""
        assert updates.parse_version(text) is None

    @pytest.mark.parametrize("text", ["2.²", "٢.1.0", "2.٣"])
    def test_digits_means_0_to_9_and_answers_rather_than_raises(self, text):
        """str.isdigit() is true for "²" and int() is not, so this promised an
        answer it could not give and raised ValueError out of a pure function
        instead. Kotlin has the mirror image — toIntOrNull() reads "٣" as 3 — and
        both sides now spell the range out."""
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

    def test_the_required_checksum_is_the_one_named_for_that_asset(self):
        text = "a" * 64 + "  NexusController.exe\n" + "b" * 64 + "  NexusController-legacy.apk"
        assert updates.required_checksum("NexusController.exe", text) == "a" * 64

    def test_an_asset_nothing_vouches_for_is_refused(self):
        """No line for it means nothing to check against — which is not "fine"."""
        with pytest.raises(updates.UpdateError, match="does not mention"):
            updates.required_checksum("NexusController.exe", "")


class TestUpdateState:
    """The rules about who may write what the dashboard shows.

    Every one of them was learned from something going wrong, and driving them
    here — rather than through a dashboard, a thread and a fake GitHub — is the
    difference between a test that pins the rule and one that happens to exercise
    it.
    """

    def state(self, version="2.0.0"):
        return updates.UpdateState(version)

    def release(self, tag="v99.0.0", asset=True):
        assets = (
            {"NexusController.exe": updates.DOWNLOAD_PREFIX + f"{tag}/NexusController.exe"}
            if asset else {}
        )
        return updates.Release(tag=tag, assets=assets)

    def test_it_starts_idle_and_always_names_this_build(self):
        state = self.state()
        assert state.snapshot() == {"state": "idle", "current": "2.0.0"}
        assert state.failed("x")["current"] == "2.0.0"

    def test_a_check_claims_the_state_and_gets_a_number(self):
        state = self.state()
        assert state.begin_check() == 1
        assert state.snapshot()["state"] == "checking"

    def test_only_one_check_at_a_time(self):
        state = self.state()
        state.begin_check()
        assert state.begin_check() is None

    def test_an_overtaken_check_may_not_answer(self):
        """Its answer is from before whatever happened in between."""
        state = self.state()
        first = state.begin_check()
        state.failed("something else entirely")     # the state moved on
        second = state.begin_check()
        state.check_found(first, self.release())
        assert state.snapshot()["state"] == "checking"
        state.check_found_nothing(second)
        assert state.snapshot()["state"] == "none"

    def test_a_check_may_not_write_over_an_install(self):
        state = self.state()
        generation = state.begin_check()
        assert state.begin_install() is None
        state.check_found(generation, self.release())
        assert state.snapshot()["state"] == "installing"

    def test_nothing_newer_does_not_leave_a_version_behind(self):
        """"none" with a latest attached is an offer nobody made."""
        state = self.state()
        state.check_found(state.begin_check(), self.release())
        answer = state.nothing_newer()
        assert answer["state"] == "none"
        assert answer["latest"] is None and answer["tag"] is None

    def test_whether_there_is_an_asset_is_read_off_the_release_every_time(self):
        """One question, one answer — the page draws the install button from it."""
        state = self.state()
        state.check_found(state.begin_check(), self.release(asset=False))
        assert state.snapshot()["has_asset"] is False
        state.begin_install()
        assert state.install_failed("boom", self.release(asset=False))["has_asset"] is False
        assert state.install_failed("boom", self.release())["has_asset"] is True

    def test_a_second_install_is_refused_while_one_runs(self):
        state = self.state()
        assert state.begin_install() is None
        assert "already being installed" in state.begin_install()

    def test_installed_is_terminal_for_this_process(self):
        """This build is still the old one and still reports the old version."""
        state = self.state()
        state.begin_install()
        state.installed()
        assert state.begin_check() is None
        assert "already installed" in state.begin_install()

    def test_a_failed_install_leaves_the_offer_standing(self):
        state = self.state()
        state.begin_install()
        answer = state.install_failed("does not match its checksum", self.release())
        assert answer["state"] == "available"
        assert answer["latest"] == "99.0.0"
        assert answer["has_asset"] is True
        assert "checksum" in answer["error"]

    def test_a_failure_with_no_release_known_has_no_offer_to_put_back(self):
        state = self.state()
        state.begin_install()
        assert state.install_failed("could not reach GitHub", None)["state"] == "error"

    def test_an_older_release_is_not_an_offer_either(self):
        state = self.state()
        state.begin_install()
        answer = state.install_failed("boom", self.release(tag="v0.1.0"))
        assert answer["state"] == "error"

    def test_dismissing_a_failure_leaves_a_state_that_means_something(self):
        """"error" with nothing to say is not a state the page can render."""
        state = self.state()
        state.failed("could not reach GitHub")
        answer = state.clear_error()
        assert answer["state"] == "idle"
        assert answer["error"] is None

    def test_dismissing_clears_the_message_and_nothing_else(self):
        state = self.state()
        state.check_found(state.begin_check(), self.release())
        state.begin_install()
        state.install_failed("the last attempt failed", self.release())
        answer = state.clear_error()
        assert answer["error"] is None
        assert answer["state"] == "available" and answer["latest"] == "99.0.0"


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


class TestOnlyHttps:
    """The rule that decides where the bytes that become the running .exe may come from.

    The Kotlin suite has pinned the same two rules on the phone since it was
    written; this side had them in the code and nothing holding them there.
    """

    def test_a_url_that_is_not_https_is_never_fetched(self):
        opener = FakeOpener({})
        with pytest.raises(updates.UpdateError, match="only https"):
            updates._read("http://example.invalid/x", opener=opener, timeout=1, limit=10)
        assert opener.seen == []

    def test_a_redirect_off_https_is_refused(self):
        """GitHub redirects assets to its object store, so redirects must be followed —
        but a redirect to http would put a man in the middle of the update, and the
        checksum is no defence because it travels the same road."""
        handler = updates._HttpsOnlyRedirects()
        request = urllib.request.Request(updates.RELEASE_API)
        with pytest.raises(updates.UpdateError, match="refusing a redirect"):
            handler.redirect_request(
                request, None, 302, "Found", {}, "http://objects.example.invalid/exe"
            )

    def test_a_redirect_that_stays_on_https_is_followed(self):
        handler = updates._HttpsOnlyRedirects()
        request = urllib.request.Request(updates.RELEASE_API)
        followed = handler.redirect_request(
            request, None, 302, "Found", {}, "https://objects.githubusercontent.com/exe"
        )
        assert followed.full_url == "https://objects.githubusercontent.com/exe"


class TestDownloadToFile:
    """The asset goes to disk as it arrives; 300 MB never sits in memory."""

    def test_writes_the_asset_and_returns_where_it_went(self, tmp_path):
        opener = FakeOpener(api_routes(exe=b"new build"))
        release = updates.fetch_latest(opener=opener)
        dest = tmp_path / "NexusController.exe.new"
        assert updates.download_to(release, dest, opener=opener) == dest
        assert dest.read_bytes() == b"new build"

    def test_arrives_in_pieces_and_is_still_the_same_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(updates, "CHUNK_BYTES", 7)
        payload = bytes(range(256)) * 40
        opener = FakeOpener(api_routes(exe=payload))
        release = updates.fetch_latest(opener=opener)
        dest = tmp_path / "build.new"
        updates.download_to(release, dest, opener=opener)
        assert dest.read_bytes() == payload

    def test_a_file_that_does_not_match_its_checksum_is_deleted_not_kept(self, tmp_path):
        routes = api_routes(exe=b"new build")
        routes[updates.DOWNLOAD_PREFIX + "v2.1.0/NexusController.exe"] = b"something else"
        opener = FakeOpener(routes)
        release = updates.fetch_latest(opener=opener)
        dest = tmp_path / "build.new"
        with pytest.raises(updates.UpdateError, match="checksum"):
            updates.download_to(release, dest, opener=opener)
        assert not dest.exists()

    def test_an_asset_larger_than_the_cap_is_refused_and_leaves_nothing(
        self, tmp_path, monkeypatch
    ):
        """A wrong or hostile URL must not be able to fill the disk either."""
        monkeypatch.setattr(updates, "MAX_ASSET_BYTES", 1024)
        monkeypatch.setattr(updates, "CHUNK_BYTES", 256)
        opener = FakeOpener(api_routes(exe=b"x" * 4096))
        release = updates.fetch_latest(opener=opener)
        dest = tmp_path / "build.new"
        with pytest.raises(updates.UpdateError, match="larger than expected"):
            updates.download_to(release, dest, opener=opener)
        assert not dest.exists()

    def test_the_new_build_is_on_disk_before_anyone_renames_it(self, tmp_path, monkeypatch):
        """A rename can outlive the contents behind it.

        install_staged() moves this file onto the name of the app; if the bytes
        are still in a buffer when the power goes, that name points at a short
        file and there is nothing left to start.
        """
        synced: list[int] = []
        real_fsync = updates.os.fsync
        monkeypatch.setattr(
            updates.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1]
        )
        opener = FakeOpener(api_routes(exe=b"new build"))
        release = updates.fetch_latest(opener=opener)
        dest = tmp_path / "build.new"
        updates.download_to(release, dest, opener=opener)
        assert synced, "nothing was flushed to disk"

    def test_the_checksum_is_taken_from_the_file_not_from_the_stream(self, tmp_path):
        """What gets renamed onto the name of the app is the file.

        Hashing the bytes as they go past checks something that no longer exists
        by the time it matters; a write that landed wrong would sail through it.
        The phone has always hashed the file it wrote.
        """
        opener = FakeOpener(api_routes(exe=b"new build"))
        release = updates.fetch_latest(opener=opener)
        dest = tmp_path / "build.new"

        real_open = updates.Path.open

        class WritesSomethingElse:
            def __init__(self, inner):
                self.inner = inner

            def write(self, chunk):
                return self.inner.write(b"\x00" * len(chunk))

            def flush(self):
                self.inner.flush()

            def fileno(self):
                return self.inner.fileno()

            def close(self):
                self.inner.close()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.inner.close()
                return False

        def opening(self, mode="r", *args, **kwargs):
            handle = real_open(self, mode, *args, **kwargs)
            return WritesSomethingElse(handle) if "w" in mode else handle

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(updates.Path, "open", opening)
            with pytest.raises(updates.UpdateError, match="checksum"):
                updates.download_to(release, dest, opener=opener)
        assert not dest.exists()

    def test_a_connection_that_never_opens_leaves_nothing(self, tmp_path):
        routes = api_routes()
        routes[updates.DOWNLOAD_PREFIX + "v2.1.0/NexusController.exe"] = urllib.error.URLError(
            "connection reset"
        )
        opener = FakeOpener(routes)
        release = updates.fetch_latest(opener=opener)
        dest = tmp_path / "build.new"
        with pytest.raises(updates.UpdateError, match="download failed"):
            updates.download_to(release, dest, opener=opener)
        assert not dest.exists()

    def test_a_download_that_dies_halfway_leaves_nothing(self, tmp_path, monkeypatch):
        """The failure that actually happens: the wire goes quiet partway through.

        Refusing before the first byte never touches the loop, and by then some
        of the new build is on disk — enough of a file to look like one, next to
        an .exe it is named after.
        """
        monkeypatch.setattr(updates, "CHUNK_BYTES", 8)
        opener = FakeOpener(api_routes(exe=b"x" * 4096))
        release = updates.fetch_latest(opener=opener)

        response = None
        real_call = opener.__call__

        class Dies:
            """Hands over three chunks, then the connection drops."""

            def __init__(self, inner):
                self.inner = inner
                self.reads = 0

            def read(self, size=-1):
                self.reads += 1
                if self.reads > 3:
                    raise urllib.error.URLError("connection reset")
                return self.inner.read(size)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def wrapping(request, timeout=None):
            nonlocal response
            answer = real_call(request, timeout=timeout)
            if getattr(request, "full_url", request).endswith(".exe"):
                response = Dies(answer)
                return response
            return answer

        dest = tmp_path / "build.new"
        with pytest.raises(updates.UpdateError, match="download failed"):
            updates.download_to(release, dest, opener=wrapping)

        assert response.reads == 4, "the loop should have written three chunks first"
        assert not dest.exists()

    def test_a_disk_that_fills_up_says_so_and_does_not_blame_the_network(
        self, tmp_path, monkeypatch
    ):
        """55 MB onto a machine somebody meant to clear out is the likelier of
        the two failures here, and "download failed" sends them to look at the
        wrong thing entirely."""
        monkeypatch.setattr(updates, "CHUNK_BYTES", 8)
        opener = FakeOpener(api_routes(exe=b"x" * 64))
        release = updates.fetch_latest(opener=opener)
        dest = tmp_path / "build.new"
        real_open = updates.Path.open

        class FullDisk:
            """Fails where a full disk really does: at the flush, not the write."""

            def __init__(self, inner):
                self.inner = inner

            def write(self, chunk):
                return len(chunk)

            def flush(self):
                raise OSError(28, "No space left on device")

            def fileno(self):
                return self.inner.fileno()

            def close(self):
                self.inner.close()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.inner.close()
                return False

        def opening(self, mode="r", *args, **kwargs):
            return FullDisk(real_open(self, mode, *args, **kwargs))

        monkeypatch.setattr(updates.Path, "open", opening)
        with pytest.raises(updates.UpdateError, match="could not write the new build"):
            updates.download_to(release, dest, opener=opener)
        assert not dest.exists()

    def test_a_close_that_fails_is_a_disk_error_too(self, tmp_path, monkeypatch):
        """close() writes as well, and it used to happen on the way out of a
        `with` — inside the block that turns anything OSError-shaped into
        "download failed", which sends the user to look at their router."""
        monkeypatch.setattr(updates, "CHUNK_BYTES", 8)
        opener = FakeOpener(api_routes(exe=b"x" * 64))
        release = updates.fetch_latest(opener=opener)
        dest = tmp_path / "build.new"
        real_open = updates.Path.open

        class FailsAtClose:
            def __init__(self, inner):
                self.inner = inner
                self.closes = 0

            def write(self, chunk):
                return self.inner.write(chunk)

            def flush(self):
                self.inner.flush()

            def fileno(self):
                return self.inner.fileno()

            def close(self):
                self.closes += 1
                self.inner.close()
                # Only the first one, exactly as a real handle behaves: the
                # cleanup pass afterwards must not raise a second time.
                if self.closes == 1:
                    raise OSError(28, "No space left on device")

        def opening(self, mode="r", *args, **kwargs):
            return FailsAtClose(real_open(self, mode, *args, **kwargs))

        monkeypatch.setattr(updates.Path, "open", opening)
        with pytest.raises(updates.UpdateError, match="could not write the new build"):
            updates.download_to(release, dest, opener=opener)
        assert not dest.exists(), "the half-written file has to go, closed or not"

    def test_a_release_without_checksums_is_refused_before_anything_is_fetched(self, tmp_path):
        document = release_document(
            assets={"NexusController.exe": updates.DOWNLOAD_PREFIX + "v2.1.0/NexusController.exe"}
        )
        opener = FakeOpener({})
        with pytest.raises(updates.UpdateError, match="SHA256SUMS"):
            updates.download_to(updates.parse_release(document), tmp_path / "x", opener=opener)
        assert opener.seen == []

    def test_a_release_without_the_windows_build_is_not_installable(self, tmp_path):
        document = release_document(
            assets={"SHA256SUMS.txt": updates.DOWNLOAD_PREFIX + "v2.1.0/SHA256SUMS.txt"}
        )
        release = updates.parse_release(document)
        with pytest.raises(updates.UpdateError, match="no NexusController.exe"):
            updates.download_to(release, tmp_path / "x", opener=FakeOpener({}))

    def test_a_directory_it_cannot_write_to_is_reported_not_raised_raw(self, tmp_path):
        opener = FakeOpener(api_routes())
        release = updates.fetch_latest(opener=opener)
        with pytest.raises(updates.UpdateError, match="could not write"):
            updates.download_to(release, tmp_path / "missing" / "x", opener=opener)


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
    """The swap itself, run on ordinary files: the download has already landed."""

    @staticmethod
    def staged(exe, payload=b"new build"):
        path = updates.staged_for(exe)
        path.write_bytes(payload)
        return path

    def test_puts_the_new_build_in_place_and_keeps_the_old_one(self, tmp_path):
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"old build")

        backup = updates.install_staged(self.staged(exe), exe)

        assert exe.read_bytes() == b"new build"
        assert backup.read_bytes() == b"old build"
        assert not (tmp_path / "NexusController.exe.new").exists()

    def test_a_leftover_backup_does_not_block_the_next_update(self, tmp_path):
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"old build")
        updates.backup_for(exe).write_bytes(b"from an update two versions ago")

        updates.install_staged(self.staged(exe), exe)

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
            updates.install_staged(self.staged(exe), exe)

        assert exe.read_bytes() == b"old build"
        assert not (tmp_path / "NexusController.exe.new").exists()


class TestClearLeftovers:
    def test_removes_what_the_last_update_left(self, tmp_path, monkeypatch):
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"current")
        updates.backup_for(exe).write_bytes(b"previous")
        assert updates.clear_leftovers(exe)
        assert not updates.backup_for(exe).exists()

    def test_nothing_to_remove_is_not_a_problem(self, tmp_path):
        assert not updates.clear_leftovers(tmp_path / "NexusController.exe")

    def test_the_write_probe_is_cleared_too(self, tmp_path):
        """writable() creates a file to answer honestly and removes it again — but
        not if the app was killed in between, and then it sits there for ever
        with a name that means nothing to whoever finds it."""
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"current")
        updates.probe_for(exe).touch()
        updates.clear_leftovers(exe)
        assert not updates.probe_for(exe).exists()

    def test_a_download_that_never_finished_is_cleared_too(self, tmp_path):
        """Killed mid-download, or the power cut: tens of megabytes named .new
        beside the app that nothing would ever look at again."""
        exe = tmp_path / "NexusController.exe"
        exe.write_bytes(b"current")
        updates.staged_for(exe).write_bytes(b"half a download")
        assert updates.clear_leftovers(exe)      # something really was removed
        assert not updates.staged_for(exe).exists()
        assert exe.read_bytes() == b"current"

    def test_a_locked_leftover_does_not_stop_the_app_starting(self, tmp_path, monkeypatch):
        """An antivirus still reading yesterday's build is not today's problem."""
        exe = tmp_path / "NexusController.exe"
        updates.backup_for(exe).write_bytes(b"previous")
        monkeypatch.setattr(
            updates.Path, "unlink", lambda self, **k: (_ for _ in ()).throw(PermissionError("in use"))
        )
        assert not updates.clear_leftovers(exe)

    def test_from_a_source_checkout_there_is_nothing_to_clear(self):
        assert not updates.clear_leftovers()


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
