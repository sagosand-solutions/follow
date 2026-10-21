# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixed numeric limits for the CI fixture boundary.

These are the design's numbers, not local v1.5 defaults. They are frozen in ``ci/policy.json``; the constants here are
the single source used by the code and the static tests.
"""

MAX_ARTIFACT_FILES = 64
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_RAW_TAR_BYTES = 4_718_592  # 4.5 MiB: 4 MiB data + <=64*(512-byte header + 511-byte padding) of tar overhead
MAX_OBSERVATION_BYTES = 65_536
MAX_ZIP_BYTES = 8 * 1024 * 1024
MAX_UNCOMPRESSED_TOTAL = 4_718_592 + 65_536
MAX_ENTRIES = 128
MAX_PATH_BYTES = 4096
MAX_TOTAL_PATH_BYTES = 65_536
MAX_STDOUT_BYTES = 65_536
MAX_STDERR_BYTES = 65_536
MAX_COMPRESSION_RATIO = 100
OUT_TMPFS_BYTES = 8 * 1024 * 1024
OUT_TMPFS_INODES = 128
