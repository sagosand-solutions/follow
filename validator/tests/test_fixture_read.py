# SPDX-License-Identifier: MIT
"""The fixture reader is bound to descriptors: races between "check" and "open", growth after stat, special files, leaked descriptors."""
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest import mock

from validator import fixture_read
from validator.core import SetupError
from validator.fixture_read import FixtureProblem, read_fixture

REL = Path("content/fixtures/artifacts/fixture-one.out")
LIMIT = 100


def open_descriptors():
    return len(os.listdir("/proc/self/fd"))


class FixtureReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.dir = self.root / REL.parent
        self.dir.mkdir(parents=True)
        self.file = self.root / REL
        self.file.write_bytes(b"real\n")
        self.outside = Path(self.temp.name).resolve() / "outside"
        self.outside.mkdir()
        (self.outside / "fixture-one.out").write_bytes(b"outside\n")

    def swap_before(self, target_name, action):
        """Run `action` once, right before os.open is asked to open `target_name`."""
        real_open = os.open
        done = []

        def wrapper(path, flags, mode=0o777, *, dir_fd=None):
            if path == target_name and not done:
                done.append(True)
                action()
            return real_open(path, flags, mode, dir_fd=dir_fd) if dir_fd is not None else real_open(path, flags, mode)
        return mock.patch.object(os, "open", wrapper)

    def test_the_bytes_of_a_plain_file(self):
        self.assertEqual(read_fixture(self.root, REL, LIMIT), b"real\n")
        self.file.write_bytes(b"x" * LIMIT)
        self.assertEqual(len(read_fixture(self.root, REL, LIMIT)), LIMIT, "exactly the limit is allowed")
        self.file.write_bytes(b"")
        self.assertEqual(read_fixture(self.root, REL, LIMIT), b"")

    def test_missing_pieces_are_problems_not_crashes(self):
        self.file.unlink()
        with self.assertRaises(FixtureProblem):
            read_fixture(self.root, REL, LIMIT)
        self.dir.rmdir()
        with self.assertRaises(FixtureProblem):
            read_fixture(self.root, REL, LIMIT)
        (self.root / "content/fixtures/artifacts").write_bytes(b"a file where a directory should be")
        with self.assertRaises(FixtureProblem):
            read_fixture(self.root, REL, LIMIT)

    def test_a_path_that_leaves_the_root_is_refused_before_anything_is_opened(self):
        for bad in ("../outside/fixture-one.out", "/etc/passwd", "content/../../x", ""):
            with self.subTest(bad), mock.patch.object(os, "open", side_effect=AssertionError("opened")), self.assertRaises(SetupError):
                read_fixture(self.root, bad, LIMIT)

    def test_a_symlink_anywhere_on_the_way_is_refused(self):
        self.file.unlink()
        self.file.symlink_to(self.outside / "fixture-one.out")
        with self.assertRaisesRegex(SetupError, "symlink"):
            read_fixture(self.root, REL, LIMIT)
        self.file.unlink()
        self.dir.rmdir()
        self.dir.symlink_to(self.outside, target_is_directory=True)
        with self.assertRaisesRegex(SetupError, "symlink"):
            read_fixture(self.root, REL, LIMIT)
        self.dir.unlink()
        (self.root / "content/fixtures").rename(self.root / "moved")
        (self.root / "content/fixtures").symlink_to(self.root / "moved", target_is_directory=True)
        with self.assertRaisesRegex(SetupError, "symlink"):
            read_fixture(self.root, REL, LIMIT)

    def test_the_file_swapped_for_a_symlink_between_looking_and_opening_is_not_followed(self):
        def swap():
            self.file.unlink()
            self.file.symlink_to(self.outside / "fixture-one.out")
        with self.swap_before("fixture-one.out", swap), self.assertRaisesRegex(SetupError, "symlink"):
            read_fixture(self.root, REL, LIMIT)

    def test_a_parent_directory_swapped_for_a_symlink_after_it_was_opened_changes_nothing(self):
        # The directory is already open by descriptor when it is replaced; the file is read from the directory we opened.
        def swap():
            self.dir.rename(self.root / "content/fixtures/original")
            self.dir.symlink_to(self.outside, target_is_directory=True)
        with self.swap_before("fixture-one.out", swap):
            data = read_fixture(self.root, REL, LIMIT)
        self.assertEqual(data, b"real\n", "the original directory's file, never the outside one")

    def test_a_file_that_grows_after_its_size_was_looked_at_is_still_bounded(self):
        self.file.write_bytes(b"x" * 10)
        real_fstat = os.fstat

        class Small:
            def __init__(self, st):
                self.st_mode = st.st_mode
                self.st_size = 10                           # what a stat taken a moment earlier would have said

        def fstat(fd):
            st = real_fstat(fd)
            self.file.write_bytes(b"y" * 5000)              # and the file grows once it is open
            return Small(st)
        with mock.patch.object(os, "fstat", fstat), self.assertRaises(FixtureProblem):
            read_fixture(self.root, REL, LIMIT)

    def test_a_huge_file_is_never_read_past_the_limit_plus_one(self):
        self.file.write_bytes(b"x" * 300000)
        real_read = os.read
        got = []

        def counting(fd, n):
            data = real_read(fd, n)
            got.append(len(data))
            return data
        with mock.patch.object(fixture_read.os, "read", counting), self.assertRaises(FixtureProblem):
            read_fixture(self.root, REL, LIMIT)
        self.assertLessEqual(sum(got), LIMIT + 1, "the overflow is detected from one byte past the limit, not from the whole file")

    def test_a_fifo_does_not_hang_and_is_not_read(self):
        self.file.unlink()
        os.mkfifo(self.file)
        def hung(_signum, _frame):
            raise AssertionError("reading a FIFO waited for a writer")
        previous = signal.signal(signal.SIGALRM, hung)
        signal.alarm(5)
        try:
            with self.assertRaises(FixtureProblem):
                read_fixture(self.root, REL, LIMIT)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)

    def test_a_directory_or_device_in_place_of_the_file_is_refused(self):
        self.file.unlink()
        self.file.mkdir()
        with self.assertRaises(FixtureProblem):
            read_fixture(self.root, REL, LIMIT)
        self.file.rmdir()
        self.file.symlink_to("/dev/null")
        with self.assertRaisesRegex(SetupError, "symlink"):
            read_fixture(self.root, REL, LIMIT)

    def test_no_descriptor_is_left_open_on_any_path(self):
        before = open_descriptors()
        read_fixture(self.root, REL, LIMIT)
        self.file.write_bytes(b"x" * 500)
        with self.assertRaises(FixtureProblem):
            read_fixture(self.root, REL, LIMIT)
        self.file.unlink()
        with self.assertRaises(FixtureProblem):
            read_fixture(self.root, REL, LIMIT)
        self.file.symlink_to(self.outside / "fixture-one.out")
        with self.assertRaises(SetupError):
            read_fixture(self.root, REL, LIMIT)
        self.assertEqual(open_descriptors(), before)

    def test_the_hashed_bytes_are_the_ones_that_were_read(self):
        # The export hashes the returned bytes and does not open the path again.
        with mock.patch.object(fixture_read.os, "read", wraps=os.read) as read, mock.patch.object(Path, "read_bytes", side_effect=AssertionError("path read")):
            self.assertEqual(read_fixture(self.root, REL, LIMIT), b"real\n")
        self.assertTrue(read.called)


class ExportUsesTheDescriptorReaderTests(unittest.TestCase):
    def test_the_export_does_not_read_the_fixture_by_path(self):
        import inspect
        from validator import export
        source = inspect.getsource(export.artifact_fixture_issues)
        self.assertIn("read_fixture(", source)
        for forbidden in ("read_bytes", "is_file", "stat(", "safe_path"):
            self.assertNotIn(forbidden, source, f"a separate {forbidden} would reopen the path")


if __name__ == "__main__":
    unittest.main()
