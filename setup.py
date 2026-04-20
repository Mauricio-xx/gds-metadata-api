"""Build script with graceful fallback chain:

1. Cython available + C compiler  -> compile .pyx (fastest)
2. No Cython + C compiler         -> compile pre-generated .c
3. No C compiler at all            -> pure Python (still works, ~100x slower scan)
"""

import os
import sys
from setuptools import setup, Extension

pyx_path = os.path.join("src", "gds_metadata", "_scanner.pyx")
c_path = os.path.join("src", "gds_metadata", "_scanner.c")

ext_modules = []

try:
    from Cython.Build import cythonize

    ext_modules = cythonize(
        [Extension(
            "gds_metadata._scanner",
            [pyx_path],
            extra_compile_args=["-O3"],
        )],
        language_level="3",
    )
    print("** Building Cython extension from .pyx")

except ImportError:
    if os.path.exists(c_path):
        ext_modules = [Extension(
            "gds_metadata._scanner",
            [c_path],
            extra_compile_args=["-O3"],
        )]
        print("** Cython not found, building from pre-generated .c")
    else:
        print("** Cython not found and no pre-generated .c - "
              "installing pure-Python fallback (slower)")

try:
    setup(
        ext_modules=ext_modules,
        package_dir={"": "src"},
    )
except Exception as e:
    if ext_modules:
        print(f"\n** C extension build failed: {e}")
        print("** Falling back to pure-Python install (no C compiler?)")
        setup(
            ext_modules=[],
            package_dir={"": "src"},
        )
    else:
        raise
