# -*- coding: utf-8 -*-
"""原子写入：先写临时文件、刷到磁盘，再一步替换。

以前直接覆盖写 report.json / *.pkl，进程中途被杀（断电、Actions 超时）会留下半个
文件，下次读到就是损坏的缓存或打不开的页面。（采纳 ChatGPT 2026-10-15 审查建议。）
"""
import os
import tempfile
from pathlib import Path


def atomic_bytes(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
            temp = Path(f.name)
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        temp = None
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
