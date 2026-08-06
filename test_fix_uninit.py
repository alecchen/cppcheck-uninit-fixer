#!/usr/bin/env python3
"""Test cppcheck-fix-uninit.py per-macro passes find guarded members."""
import os, sys, tempfile, subprocess, textwrap, shutil

FIXER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cppcheck-fix-uninit.py')


def make_project(files):
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


# -----------------------------------------------------------------------
# Test 1: two #ifdef-guarded members -- per-macro finds both
# -----------------------------------------------------------------------

d1 = make_project({
    'test_per_macro.h': """\
        class TestPerMacro {
        public:
            TestPerMacro();
        #ifdef FEATURE_X
            int feature_x_;
        #endif
        #ifdef FEATURE_Y
            double feature_y_;
        #endif
            int always_;
        };
        """,
    'test_per_macro.cpp': """\
        #include "test_per_macro.h"
        TestPerMacro::TestPerMacro() {}
        """,
})

cpp = os.path.join(d1, 'test_per_macro.cpp')
r = subprocess.run(
    [sys.executable, FIXER, '--report-only', cpp],
    cwd=d1, capture_output=True, text=True)

assert r.returncode == 0, f"Fixer failed: {r.stderr[-500:]}"
assert 'feature_x_' in r.stdout, \
    f"Missing feature_x_ (#ifdef FEATURE_X): {r.stdout[-500:]}"
assert 'feature_y_' in r.stdout, \
    f"Missing feature_y_ (#ifdef FEATURE_Y): {r.stdout[-500:]}"
assert 'always_' in r.stdout, \
    f"Missing always_ (unguarded): {r.stdout[-500:]}"
shutil.rmtree(d1, ignore_errors=True)
print("Test 1 PASS  per-macro: finds #ifdef-guarded members from both macros")


# -----------------------------------------------------------------------
# Test 2: #ifndef guard -- found in base pass
# -----------------------------------------------------------------------

d2 = make_project({
    'test_ifndef.h': """\
        class TestIfndef {
        public:
            TestIfndef();
        #ifndef DISABLED
            int active_only_;
        #endif
            int always_;
        };
        """,
    'test_ifndef.cpp': """\
        #include "test_ifndef.h"
        TestIfndef::TestIfndef() {}
        """,
})

cpp2 = os.path.join(d2, 'test_ifndef.cpp')
r2 = subprocess.run(
    [sys.executable, FIXER, '--report-only', cpp2],
    cwd=d2, capture_output=True, text=True)

assert r2.returncode == 0
assert 'active_only_' in r2.stdout, \
    f"Missing active_only_ (#ifndef): {r2.stdout[-500:]}"
assert 'always_' in r2.stdout, \
    f"Missing always_: {r2.stdout[-500:]}"
shutil.rmtree(d2, ignore_errors=True)
print("Test 2 PASS  #ifndef guard: member found via base pass")


# -----------------------------------------------------------------------
# Test 3: source in subdirectory with header in same subdir
# -----------------------------------------------------------------------

d3 = make_project({
    'src/test_sub.h': """\
        class TestSub {
        public:
            TestSub();
        #ifdef SUB_FEATURE
            int sub_feature_;
        #endif
            int sub_base_;
        };
        """,
    'src/test_sub.cpp': """\
        #include "test_sub.h"
        TestSub::TestSub() {}
        """,
})

cpp3 = os.path.join(d3, 'src', 'test_sub.cpp')
r3 = subprocess.run(
    [sys.executable, FIXER, '-v', '--report-only', cpp3],
    cwd=d3, capture_output=True, text=True)

assert r3.returncode == 0, f"Fixer failed: {r3.stderr[-500:]}"
assert 'sub_base_' in r3.stdout, f"Missing sub_base_: {r3.stdout[-500:]}"
assert 'sub_feature_' in r3.stdout, f"Missing sub_feature_: {r3.stdout[-500:]}"
# Verify macro was extracted from subdirectory header (verbose output to stderr)
assert 'SUB_FEATURE' in r3.stderr, \
    f"Macro SUB_FEATURE not extracted: {r3.stderr[:1000]}"
shutil.rmtree(d3, ignore_errors=True)
print("Test 3 PASS  subdirectory: finds guarded member in subdir")


# -----------------------------------------------------------------------
# Test 4: separate include/ dir -- macros extracted from -I path headers
# -----------------------------------------------------------------------

d4 = make_project({
    'src/main.cpp': """\
        #include "config.h"
        AppConfig::AppConfig() {}
        """,
    'include/config.h': """\
        class AppConfig {
        public:
            AppConfig();
        #ifdef INC_FEATURE
            int inc_feature_;
        #endif
            int inc_base_;
        };
        """,
})

cpp4 = os.path.join(d4, 'src', 'main.cpp')
r4 = subprocess.run(
    [sys.executable, FIXER, '-v', '-I', os.path.join(d4, 'include'),
     '--report-only', cpp4],
    cwd=d4, capture_output=True, text=True)

assert r4.returncode == 0
assert 'inc_base_' in r4.stdout, f"Missing inc_base_: {r4.stdout[-500:]}"
assert 'inc_feature_' in r4.stdout, \
    f"Missing inc_feature_: {r4.stdout[-500:]}"
# Unlike check_uninit_all.sh, the Python fixer scans -I directories for
# macros via find_headers(), so INC_FEATURE appears in per-macro list
assert 'INC_FEATURE' in r4.stderr, \
    f"INC_FEATURE not in per-macro list: {r4.stderr[:500]}"
shutil.rmtree(d4, ignore_errors=True)
print("Test 4 PASS  -I: guarded member found, macro extracted from -I header")


# -----------------------------------------------------------------------
# Test 5: max-configs guard -- 4 macros with --max-configs=8 errors out
# -----------------------------------------------------------------------

d5 = make_project({
    'test_maxcfg.h': """\
        class TestMaxCfg {
        public:
            TestMaxCfg();
        #if A
            int a_;
        #endif
        #if B
            int b_;
        #endif
        #if C
            int c_;
        #endif
        #if D
            int d_;
        #endif
        };
        """,
    'test_maxcfg.cpp': """\
        #include "test_maxcfg.h"
        TestMaxCfg::TestMaxCfg() {}
        """,
})

cpp5 = os.path.join(d5, 'test_maxcfg.cpp')
r5 = subprocess.run(
    [sys.executable, FIXER, '--max-configs=8', '--report-only', cpp5],
    cwd=d5, capture_output=True, text=True)
assert r5.returncode != 0, f"Should exit with error: {r5.stderr[:500]}"
assert '16' in r5.stderr and 'max-configs=8 is too low' in r5.stderr, \
    f"Expected max-configs error: {r5.stderr[:500]}"
shutil.rmtree(d5, ignore_errors=True)
print("Test 5 PASS  max-configs guard: 4 macros with --max-configs=8 errors out")


# -----------------------------------------------------------------------
# Test 6: 4 macros with --max-configs=16 passes
# -----------------------------------------------------------------------

d6 = make_project({
    'test_maxcfg.h': """\
        class TestMaxCfg {
        public:
            TestMaxCfg();
        #if A
            int a_;
        #endif
        #if B
            int b_;
        #endif
        #if C
            int c_;
        #endif
        #if D
            int d_;
        #endif
        };
        """,
    'test_maxcfg.cpp': """\
        #include "test_maxcfg.h"
        TestMaxCfg::TestMaxCfg() {}
        """,
})

cpp6 = os.path.join(d6, 'test_maxcfg.cpp')
r6 = subprocess.run(
    [sys.executable, FIXER, '--max-configs=16', '--report-only', cpp6],
    cwd=d6, capture_output=True, text=True)
assert r6.returncode == 0, f"Should pass: {r6.stderr[-500:]}"
assert 'a_' in r6.stdout, f"Missing a_: {r6.stdout[-500:]}"
shutil.rmtree(d6, ignore_errors=True)
print("Test 6 PASS  max-configs guard: --max-configs=16 passes for 4 macros")


print("\nAll 6 cppcheck-fix-uninit.py per-macro tests passed")
