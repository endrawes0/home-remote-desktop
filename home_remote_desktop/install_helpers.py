from __future__ import annotations

import struct
import sys


def python_executable_arch() -> str:
    with open(sys.executable, "rb") as handle:
        data = handle.read(512)
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    with open(sys.executable, "rb") as handle:
        handle.seek(pe_offset + 4)
        machine = struct.unpack("<H", handle.read(2))[0]
    return {
        0x014C: "x86",
        0x8664: "x64",
        0xAA64: "arm64",
    }.get(machine, "x64")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "python-arch":
        print(python_executable_arch())
        return
    raise SystemExit("usage: python -m home_remote_desktop.install_helpers python-arch")


if __name__ == "__main__":
    main()

