# -*- coding: utf-8 -*-
"""测试环境补丁（只影响 Windows 本机，CI 不变）。

DSH 沙箱会把 `tempfile.mkdtemp()` 建的 0o700 目录封成不可写（进程自己都写不进去），
所以 Windows 上换成 `os.mkdir(path, 0o777)` 的等价实现，并把 tempdir 指到仓库内的
`.test-tmp/`（继承工作区的 ACL）。Linux CI 上 tempfile 本来正常，这里什么都不做。

本进程建的临时目录会在退出时统一清理，避免 `.test-tmp` 越跑越大（一次全量测试
会建上千个 `amo-*` 目录）。
"""
import atexit
import os
import shutil
import tempfile

if os.name == "nt" and not getattr(tempfile, "_amo_mkdtemp_patched", False):
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _tmp = os.path.join(_root, ".test-tmp")
    os.makedirs(_tmp, exist_ok=True)
    tempfile.tempdir = _tmp

    _names = tempfile._RandomNameSequence()
    _created = []

    def _mkdtemp(suffix: str = "", prefix: str = "tmp", dir: str | None = None) -> str:
        base = dir or tempfile.gettempdir()
        for _ in range(200):
            path = os.path.join(base, prefix + next(_names) + suffix)
            try:
                os.mkdir(path, 0o777)
            except FileExistsError:
                continue
            _created.append(path)
            return path
        raise FileExistsError("could not create a temporary directory")

    tempfile.mkdtemp = _mkdtemp
    tempfile.TemporaryDirectory._mkdtemp = staticmethod(_mkdtemp)
    tempfile._amo_mkdtemp_patched = True

    @atexit.register
    def _cleanup_created_dirs() -> None:
        while _created:
            shutil.rmtree(_created.pop(), ignore_errors=True)
