"""The release-cutting script.

Nothing here pushes anything: git and the build are replaced throughout. What is
worth pinning is what the script refuses to do — release a version that is not
newer, tag a dirty tree without being told to, leave a bumped version behind
after a failed build — because every one of those would only be discovered as a
published release that should not exist.
"""

import pytest

import publish_release as pr
from build_release import StepFailed

PYPROJECT = '[project]\nname = "x"\nversion = "2.0.0"\ndescription = "y"\n'
INIT = '"""Doc."""\n\n__version__ = "2.0.0"\n\n__all__ = ["__version__"]\n'
GRADLE = (
    "android {\n"
    "    defaultConfig {\n"
    "        versionCode = 2\n"
    '        versionName = "2.0"\n'
    "    }\n"
    "}\n"
)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A tree with the three version files, standing in for the real one."""
    (tmp_path / "pyproject.toml").write_bytes(PYPROJECT.encode())
    init = tmp_path / "server" / "nexus_server"
    init.mkdir(parents=True)
    (init / "__init__.py").write_bytes(INIT.encode())
    gradle = tmp_path / "android" / "app"
    gradle.mkdir(parents=True)
    (gradle / "build.gradle.kts").write_bytes(GRADLE.encode())
    monkeypatch.setattr(pr, "ROOT", tmp_path)
    return tmp_path


def read(path):
    return path.read_bytes().decode()


class TestVersion:
    def test_parses_and_prints(self):
        assert str(pr.Version.parse("2.10.3")) == "2.10.3"
        assert pr.Version.parse("2.0.0").tag == "v2.0.0"

    @pytest.mark.parametrize("bad", ["2.0", "v2.0.0", "2.0.0-rc1", "", "two.0.0"])
    def test_rejects_anything_else(self, bad):
        with pytest.raises(StepFailed):
            pr.Version.parse(bad)

    @pytest.mark.parametrize(
        ("part", "expected"),
        [("major", "3.0.0"), ("minor", "2.5.0"), ("patch", "2.4.8")],
    )
    def test_bumps_and_resets_what_is_below(self, part, expected):
        assert str(pr.Version(2, 4, 7).bumped(part)) == expected

    def test_orders_numerically_not_alphabetically(self):
        """"2.10.0" < "2.9.0" as strings, which would let a release go backwards."""
        assert pr.Version(2, 9, 0) < pr.Version(2, 10, 0)


class TestAndroidVersionCode:
    def test_grows_with_every_part(self):
        codes = [
            pr.android_version_code(v)
            for v in (pr.Version(2, 0, 0), pr.Version(2, 0, 1), pr.Version(2, 1, 0), pr.Version(3, 0, 0))
        ]
        assert codes == sorted(codes) and len(set(codes)) == len(codes)

    def test_beats_the_code_in_the_tree_today(self, repo):
        """A phone refuses an upgrade whose code did not increase."""
        pr.check_version_code(repo, pr.Version(2, 0, 1))

    def test_refuses_a_code_that_would_not_increase(self, repo):
        (repo / "android" / "app" / "build.gradle.kts").write_bytes(
            GRADLE.replace("versionCode = 2", "versionCode = 999999").encode()
        )
        with pytest.raises(StepFailed, match="versionCode"):
            pr.check_version_code(repo, pr.Version(2, 0, 1))


class TestReadingTheCurrentVersion:
    def test_reads_the_one_the_files_agree_on(self, repo):
        assert pr.current_version(repo) == pr.Version(2, 0, 0)

    def test_two_part_version_name_counts_as_agreement(self, repo):
        """android/app/build.gradle.kts ships "2.0" for 2.0.0 and that is fine.

        The fixture is already written that way, so asserting the version again
        would only repeat the test above. What is worth pinning is the reason it
        agrees: the Android file really does carry the short form.
        """
        gradle = (repo / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
        assert 'versionName = "2.0"' in gradle
        assert pr.current_version(repo) == pr.Version(2, 0, 0)

    def test_disagreement_is_an_error_not_a_guess(self, repo):
        (repo / "pyproject.toml").write_bytes(PYPROJECT.replace("2.0.0", "2.1.0").encode())
        with pytest.raises(StepFailed, match="disagree"):
            pr.current_version(repo)

    def test_a_second_version_line_stops_the_release(self, repo):
        (repo / "pyproject.toml").write_bytes(
            (PYPROJECT + '\n[tool.other]\nversion = "9.9.9"\n').encode()
        )
        with pytest.raises(StepFailed, match="refusing to guess"):
            pr.current_version(repo)

    def test_a_missing_line_is_reported_by_file(self, repo):
        (repo / "pyproject.toml").write_bytes(b'[project]\nname = "x"\n')
        with pytest.raises(StepFailed, match="pyproject.toml"):
            pr.current_version(repo)


class TestRewriting:
    def test_writes_every_place_the_version_lives(self, repo):
        pr.rewrite_all(pr.Version(2, 1, 0))
        assert 'version = "2.1.0"' in read(repo / "pyproject.toml")
        assert '__version__ = "2.1.0"' in read(repo / "server" / "nexus_server" / "__init__.py")
        gradle = read(repo / "android" / "app" / "build.gradle.kts")
        assert 'versionName = "2.1.0"' in gradle
        assert f"versionCode = {pr.android_version_code(pr.Version(2, 1, 0))}" in gradle
        assert pr.current_version(repo) == pr.Version(2, 1, 0)

    def test_leaves_crlf_files_as_crlf(self, repo):
        """Otherwise a one-line bump arrives as a whole-file diff."""
        path = repo / "pyproject.toml"
        path.write_bytes(PYPROJECT.replace("\n", "\r\n").encode())
        pr.rewrite_all(pr.Version(2, 1, 0))
        assert path.read_bytes().count(b"\r\n") == PYPROJECT.count("\n")
        assert 'version = "2.1.0"' in read(path)

    def test_touches_nothing_but_the_version(self, repo):
        before = read(repo / "server" / "nexus_server" / "__init__.py")
        pr.rewrite_all(pr.Version(2, 1, 0))
        after = read(repo / "server" / "nexus_server" / "__init__.py")
        assert after == before.replace("2.0.0", "2.1.0")

    def test_restore_puts_everything_back_byte_for_byte(self, repo):
        touched = [entry.path(repo) for entry in pr.VERSION_FILES]
        before = {path: path.read_bytes() for path in touched}
        pr.restore(pr.rewrite_all(pr.Version(2, 1, 0)))
        assert {path: path.read_bytes() for path in touched} == before


class FakeGit:
    """Answers the questions the script asks and records the commands it runs."""

    def __init__(self, *, status="", branch="main", tags="", remote_tags="", fails=()):
        self.status = status
        self.branch = branch
        self.tags = tags
        self.remote_tags = remote_tags
        #: Command prefixes that should fail, as ("push", "origin", "main").
        self.fails = tuple(tuple(f) for f in fails)
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, *args, root=None):
        self.commands.append(args)
        for prefix in self.fails:
            if args[: len(prefix)] == prefix:
                raise pr.StepFailed(f"git {' '.join(args)} failed (exit 1): refused")
        if args[:2] == ("rev-parse", "--git-dir"):
            return ".git\n"
        if args[0] == "status":
            return self.status
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return f"{self.branch}\n"
        if args[0] == "tag" and args[1] == "--list":
            return self.tags
        if args[0] == "ls-remote":
            return self.remote_tags
        if args[:2] == ("remote", "get-url"):
            return "https://github.com/b-y-t-e/NexusController2.git\n"
        return ""

    def ran(self, *prefix) -> bool:
        return any(c[: len(prefix)] == prefix for c in self.commands)


@pytest.fixture()
def git(monkeypatch):
    fake = FakeGit()
    monkeypatch.setattr(pr, "git", fake)
    return fake


@pytest.fixture()
def answers(monkeypatch):
    """Queue of replies for input(); an empty queue means nobody is at the keyboard."""
    queued: list[str] = []

    def fake_input(prompt=""):
        if not queued:
            raise EOFError
        return queued.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    return queued


@pytest.fixture()
def no_build(monkeypatch):
    """The build is exercised by test_build_release.py; here it just has to pass."""
    calls: list[list[str]] = []
    monkeypatch.setattr(pr.build_release, "main", lambda argv=None: calls.append(argv) or 0)
    return calls


class TestMain:
    def test_dry_run_prints_the_plan_and_changes_nothing(self, repo, git, answers, capsys):
        assert pr.main(["minor", "--dry-run"]) == 0
        assert pr.current_version(repo) == pr.Version(2, 0, 0)
        assert not git.ran("commit") and not git.ran("tag", "-a") and not git.ran("push")
        assert "2.1.0" in capsys.readouterr().out

    def test_dry_run_on_a_dirty_tree_still_prints_the_plan(
        self, repo, monkeypatch, answers, capsys
    ):
        """The commonest way to run --dry-run, and it used to show nothing.

        The dirty-tree question came first and refusing it aborted before the
        plan was ever printed — so the one command whose entire job is "tell me
        what would happen" answered "commit or stash your changes first".
        """
        monkeypatch.setattr(pr, "git", FakeGit(status=" M server/nexus_server/app.py\n"))

        assert pr.main(["minor", "--dry-run"]) == 0

        out = capsys.readouterr().out
        assert "2.1.0" in out
        assert "1 uncommitted change" in out
        assert "you will be asked" in out
        assert pr.current_version(repo) == pr.Version(2, 0, 0)

    def test_dry_run_on_a_dirty_tree_asks_nothing(self, repo, monkeypatch, answers, capsys):
        """No prompt at all: `answers` is empty, so any question raises EOFError."""
        monkeypatch.setattr(pr, "git", FakeGit(status=" M x.py\n"))
        assert pr.main(["patch", "--dry-run"]) == 0

    def test_ctrl_c_during_the_build_puts_the_version_files_back(
        self, repo, git, answers, monkeypatch, capsys
    ):
        """The likeliest way this ever ends early, and every file says the new
        number by then. Left like that, the bump rides along in whatever gets
        committed next."""
        def interrupted(argv=None):
            raise KeyboardInterrupt

        monkeypatch.setattr(pr.build_release, "main", interrupted)

        assert pr.main(["minor", "--yes"]) == 130

        assert pr.current_version(repo) == pr.Version(2, 0, 0)
        assert "put back" in capsys.readouterr().out

    def test_ctrl_c_after_the_tag_says_what_is_in_the_repository(
        self, repo, git, answers, no_build, monkeypatch, capsys
    ):
        """Past the commit there is nothing to put back — only something to say."""
        real_push = pr.push

        def interrupted(version):
            raise KeyboardInterrupt

        monkeypatch.setattr(pr, "push", interrupted)

        assert pr.main(["patch", "--yes", "--no-build"]) == 130

        out = capsys.readouterr().out
        assert "v2.0.1 is committed and tagged here but not pushed" in out
        assert "git reset --hard HEAD~1" in out
        assert real_push is not None

    def test_a_tag_already_on_the_remote_stops_it(self, repo, monkeypatch, answers):
        """It has been released. Re-tagging would publish a second thing as it."""
        monkeypatch.setattr(pr, "git", FakeGit(remote_tags="abc123\trefs/tags/v2.1.0\n"))
        assert pr.main(["minor", "--yes"]) == 1
        assert pr.current_version(repo) == pr.Version(2, 0, 0)

    def test_a_failed_commit_says_what_is_staged_and_how_to_undo(
        self, repo, monkeypatch, answers, no_build, capsys
    ):
        """A hook can refuse the commit, and the version files are rewritten by then."""
        monkeypatch.setattr(pr, "git", FakeGit(fails=[("commit",)]))

        assert pr.main(["patch", "--yes", "--no-build"]) == 1

        out = capsys.readouterr().out
        assert "nothing was committed" in out
        assert "git restore --staged --worktree" in out

    def test_a_failed_tag_says_the_commit_is_already_there(
        self, repo, monkeypatch, answers, no_build, capsys
    ):
        monkeypatch.setattr(pr, "git", FakeGit(fails=[("tag", "-a")]))

        assert pr.main(["patch", "--yes", "--no-build"]) == 1

        out = capsys.readouterr().out
        assert "IS committed" in out
        assert "git reset --hard HEAD~1" in out

    def test_a_failed_push_says_what_is_local_and_how_to_finish(
        self, repo, monkeypatch, answers, no_build, capsys
    ):
        """The worst moment to be told only "git push failed": the commit and the
        tag exist here, nothing is published, and both finishing and undoing are
        one command that nobody should have to work out under pressure."""
        monkeypatch.setattr(pr, "git", FakeGit(fails=[("push", "origin", "main")]))

        assert pr.main(["patch", "--yes", "--no-build"]) == 1

        out = capsys.readouterr().out
        assert "nothing was published" in out
        assert "git push origin main && git push origin v2.0.1" in out
        assert "git tag -d v2.0.1" in out

    def test_a_push_that_lands_the_branch_but_not_the_tag_says_so(
        self, repo, monkeypatch, answers, no_build, capsys
    ):
        """Half-published: the bump is public and nothing is building."""
        monkeypatch.setattr(pr, "git", FakeGit(fails=[("push", "origin", "v2.0.1")]))

        assert pr.main(["patch", "--yes", "--no-build"]) == 1

        out = capsys.readouterr().out
        assert "the branch is pushed but the tag is not" in out
        assert "git push origin v2.0.1" in out

    def test_a_version_that_is_not_newer_is_refused(self, repo, git, answers):
        assert pr.main(["2.0.0", "--dry-run"]) == 1
        assert pr.main(["1.9.9", "--dry-run"]) == 1

    def test_an_existing_tag_stops_it(self, repo, git, answers):
        git.tags = "v2.1.0\n"
        assert pr.main(["minor", "--dry-run"]) == 1

    def test_typing_the_wrong_thing_stops_it(self, repo, git, answers, no_build):
        answers.append("yes")           # not the version — deliberately
        assert pr.main(["minor"]) == 1
        assert pr.current_version(repo) == pr.Version(2, 0, 0)
        assert not git.ran("tag", "-a")

    def test_the_happy_path_bumps_builds_commits_tags_and_pushes(
        self, repo, git, answers, no_build
    ):
        answers.append("2.1.0")
        assert pr.main(["minor"]) == 0
        assert pr.current_version(repo) == pr.Version(2, 1, 0)
        assert no_build == [[]]
        assert git.ran("commit")
        assert git.ran("tag", "-a", "v2.1.0")
        assert git.ran("push", "origin", "main")
        assert git.ran("push", "origin", "v2.1.0")

    def test_skip_tests_reaches_the_build(self, repo, git, answers, no_build):
        answers.append("2.0.1")
        assert pr.main(["patch", "--skip-tests"]) == 0
        assert no_build == [["--skip-tests"]]

    def test_no_push_stops_after_the_tag(self, repo, git, answers, no_build):
        answers.append("2.0.1")
        assert pr.main(["patch", "--no-push"]) == 0
        assert git.ran("tag", "-a", "v2.0.1")
        assert not git.ran("push")

    def test_no_build_skips_the_build_entirely(self, repo, git, answers, no_build):
        answers.append("2.0.1")
        assert pr.main(["patch", "--no-build"]) == 0
        assert no_build == []

    def test_a_failed_build_leaves_the_version_untouched(
        self, repo, git, answers, monkeypatch
    ):
        monkeypatch.setattr(pr.build_release, "main", lambda argv=None: 1)
        answers.append("2.1.0")
        assert pr.main(["minor"]) == 1
        assert pr.current_version(repo) == pr.Version(2, 0, 0)
        assert not git.ran("commit") and not git.ran("tag", "-a")


class TestDirtyTree:
    def test_uncommitted_changes_are_listed_and_need_an_answer(
        self, repo, git, answers, no_build, capsys
    ):
        git.status = " M server/nexus_server/server.py\n?? notes.txt\n"
        answers.extend(["n"])                    # do not commit them
        assert pr.main(["minor"]) == 1
        out = capsys.readouterr().out
        assert "server.py" in out and "notes.txt" in out
        assert pr.current_version(repo) == pr.Version(2, 0, 0)
        assert not git.ran("tag", "-a")

    def test_saying_yes_commits_everything_together(self, repo, git, answers, no_build):
        git.status = " M server/nexus_server/server.py\n"
        answers.extend(["y", "2.1.0"])
        assert pr.main(["minor"]) == 0
        assert git.ran("add", "-A")
        assert git.ran("tag", "-a", "v2.1.0")

    def test_a_clean_tree_stages_only_the_version_files(self, repo, git, answers, no_build):
        answers.append("2.1.0")
        assert pr.main(["minor"]) == 0
        assert not git.ran("add", "-A")
        assert git.ran("add", "--", "pyproject.toml")

    def test_nobody_at_the_keyboard_means_no(self, repo, git, answers, no_build):
        """A release must never happen because a prompt was answered by EOF."""
        git.status = " M x.py\n"
        assert pr.main(["minor"]) == 1
        assert not git.ran("tag", "-a")

    def test_yes_flag_answers_everything_on_a_clean_tree(self, repo, git, answers, no_build):
        assert pr.main(["minor", "--yes"]) == 0
        assert git.ran("add", "--", "pyproject.toml")
        assert not git.ran("add", "-A")
        assert git.ran("push", "origin", "v2.1.0")

    def test_yes_flag_will_not_commit_a_dirty_tree_it_was_not_shown(
        self, repo, git, answers, no_build, capsys
    ):
        """--yes means "do not ask me", not "publish whatever is lying around".

        Saying yes to this one question runs `git add -A`, so an unattended run
        would tag and push a half-finished edit or a stray scratch file — and by
        the time anyone looks, the tag is public and GitHub has built it.
        """
        git.status = " M x.py\n?? scratch.txt\n"
        assert pr.main(["minor", "--yes"]) == 1
        assert not git.ran("add", "-A")
        assert not git.ran("tag", "-a")
        assert pr.current_version(repo) == pr.Version(2, 0, 0)
        assert "commit or stash" in capsys.readouterr().out

    def test_a_dry_run_with_yes_on_a_dirty_tree_still_prints_the_plan(
        self, repo, git, answers, no_build, capsys
    ):
        """Nothing is committed by a dry run, so there is nothing to refuse."""
        git.status = " M x.py\n"
        assert pr.main(["minor", "--yes", "--dry-run"]) == 0
        assert "about to release 2.1.0" in capsys.readouterr().out


class TestBranch:
    def test_a_side_branch_needs_confirmation(self, repo, git, answers, no_build):
        git.branch = "feature/x"
        answers.extend(["n"])
        assert pr.main(["minor"]) == 1
        assert not git.ran("tag", "-a")

    def test_and_can_be_released_from_anyway(self, repo, git, answers, no_build):
        git.branch = "feature/x"
        answers.extend(["y", "2.1.0"])
        assert pr.main(["minor"]) == 0
        assert git.ran("push", "origin", "feature/x")


class TestTheRealTree:
    """The patterns are only useful if they match the files actually in the repo."""

    def test_the_repository_has_one_agreed_version(self):
        assert pr.current_version(pr.ROOT) is not None

    def test_every_version_file_is_found_and_readable(self):
        for entry in pr.VERSION_FILES:
            assert entry.read(pr.ROOT)
