#!/usr/bin/env python3
"""Shared helpers for the test suites."""
import os
import tempfile
import textwrap


def make_project(files, d=None):
    """Create a temp dir with the given {name: content} dict.

    Names may include directory components (e.g. 'subdir/file.h');
    intermediate directories are created automatically. Leading blank
    lines from triple-quoted literals are stripped and the rest is
    dedented. Returns the directory path.
    """
    if d is None:
        d = tempfile.mkdtemp()
    for name, content in files.items():
        text = textwrap.dedent(content)
        if text.startswith('\n'):
            text = text[1:]
        path = os.path.join(d, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(text)
    return d
