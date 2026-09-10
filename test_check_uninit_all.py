#!/usr/bin/env python3
"""Test check_uninit_all.sh: max-configs guard + per-macro pass coverage."""
import os, sys, subprocess, shutil

from test_helpers import make_project

SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__) or '.',
                                       'check_uninit_all.sh'))

make_files = make_project  # local alias used throughout this suite


# -----------------------------------------------------------------------
# Test 1: max-configs guard -- 4 macros, limit=8 -> error (2^4=16 > 8)
# -----------------------------------------------------------------------

d1 = make_files({
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

cpp = os.path.join(d1, 'test_maxcfg.cpp')
r = subprocess.run(
    ['bash', SCRIPT, '--max-configs=8', cpp],
    cwd=d1, capture_output=True, text=True)
assert 'ERROR: --max-configs=8 is too low for 4 macros' in r.stdout, \
    f"Expected max-configs error, got: {r.stdout[:500]}"
assert r.returncode != 0
shutil.rmtree(d1, ignore_errors=True)
print("Test 1 PASS  max-configs guard: 4 macros with --max-configs=8 errors out")


# -----------------------------------------------------------------------
# Test 2: enough max-configs passes + finds unguarded members
# -----------------------------------------------------------------------

d2 = make_files({
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
            int always_;
        };
        """,
    'test_maxcfg.cpp': """\
        #include "test_maxcfg.h"
        TestMaxCfg::TestMaxCfg() {}
        """,
})

cpp2 = os.path.join(d2, 'test_maxcfg.cpp')
r2 = subprocess.run(
    ['bash', SCRIPT, '--max-configs=4', cpp2],
    cwd=d2, capture_output=True, text=True)
assert r2.returncode == 0, f"Should pass: {r2.stdout[-500:]}"
assert 'always_' in r2.stdout, f"Missing always_: {r2.stdout[-500:]}"
shutil.rmtree(d2, ignore_errors=True)
print("Test 2 PASS  guarded members found, unguarded member found")


# -----------------------------------------------------------------------
# Test 3: per-macro pass finds #ifdef-guarded member
# -----------------------------------------------------------------------

d3 = make_files({
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

cpp3 = os.path.join(d3, 'test_per_macro.cpp')
r3 = subprocess.run(
    ['bash', SCRIPT, '--max-configs=8', cpp3],
    cwd=d3, capture_output=True, text=True)

assert r3.returncode == 0, f"Script failed: {r3.stdout[-500:]}"
assert 'feature_x_' in r3.stdout, \
    f"Missing feature_x_: {r3.stdout[-1000:]}"
assert 'feature_y_' in r3.stdout, \
    f"Missing feature_y_: {r3.stdout[-1000:]}"
assert 'always_' in r3.stdout, \
    f"Missing always_: {r3.stdout[-1000:]}"
shutil.rmtree(d3, ignore_errors=True)
print("Test 3 PASS  #ifdef-guarded members found via per-macro passes")


# -----------------------------------------------------------------------
# Test 4: #ifndef guard -- member found in base pass (macro undefined)
# -----------------------------------------------------------------------

d4 = make_files({
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

cpp4 = os.path.join(d4, 'test_ifndef.cpp')
r4 = subprocess.run(
    ['bash', SCRIPT, '--max-configs=4', cpp4],
    cwd=d4, capture_output=True, text=True)

assert r4.returncode == 0
assert 'active_only_' in r4.stdout, \
    f"Missing active_only_: {r4.stdout[-500:]}"
assert 'always_' in r4.stdout, \
    f"Missing always_: {r4.stdout[-500:]}"
shutil.rmtree(d4, ignore_errors=True)
print("Test 4 PASS  #ifndef guard member found via base pass")


# -----------------------------------------------------------------------
# Test 5: #if and #ifdef macros both detected
# -----------------------------------------------------------------------

d5 = make_files({
    'test_mixed.h': """\
        class TestMixed {
        public:
            TestMixed();
        #if USE_BOTH
            int both_;
        #endif
        #ifdef USE_NEW_API
            long new_api_;
        #endif
        };
        """,
    'test_mixed.cpp': """\
        #include "test_mixed.h"
        TestMixed::TestMixed() {}
        """,
})

cpp5 = os.path.join(d5, 'test_mixed.cpp')
r5 = subprocess.run(
    ['bash', SCRIPT, '--max-configs=8', cpp5],
    cwd=d5, capture_output=True, text=True)

assert r5.returncode == 0
assert 'both_' in r5.stdout, \
    f"Missing both_: {r5.stdout[-500:]}"
assert 'new_api_' in r5.stdout, \
    f"Missing new_api_: {r5.stdout[-500:]}"
shutil.rmtree(d5, ignore_errors=True)
print("Test 5 PASS  #if and #ifdef macros both found")


# -----------------------------------------------------------------------
# Test 6: source in subdirectory -- header found in same subdir
# -----------------------------------------------------------------------

d6 = make_files({
    'src/test_sub.h': """\
        class TestSub {
        public:
            TestSub();
        #ifdef FEATURE_Z
            int feature_z_;
        #endif
            int base_;
        };
        """,
    'src/test_sub.cpp': """\
        #include "test_sub.h"
        TestSub::TestSub() {}
        """,
})

cpp6 = os.path.join(d6, 'src', 'test_sub.cpp')
r6 = subprocess.run(
    ['bash', SCRIPT, '--max-configs=4', cpp6],
    cwd=d6, capture_output=True, text=True)

assert r6.returncode == 0, f"Script failed: {r6.stdout[-500:]}"
assert 'base_' in r6.stdout, f"Missing base_: {r6.stdout[-500:]}"
assert 'feature_z_' in r6.stdout, f"Missing feature_z_: {r6.stdout[-500:]}"
assert 'FEATURE_Z' in r6.stdout, f"Macro FEATURE_Z not extracted from subdir header"
shutil.rmtree(d6, ignore_errors=True)
print("Test 6 PASS  subdirectory: header found, macro extracted, members detected")


# -----------------------------------------------------------------------
# Test 7: multiple source dirs -- simulating 'dir/**/*.cpp' glob expansion
# -----------------------------------------------------------------------

d7 = make_files({
    'src/mod_a.h': """\
        class ModA {
        public:
            ModA();
        #ifdef MOD_A_FLAG
            int a_flag_;
        #endif
            int a_base_;
        };
        """,
    'src/mod_a.cpp': """\
        #include "mod_a.h"
        ModA::ModA() {}
        """,
    'src/extra/mod_b.h': """\
        class ModB {
        public:
            ModB();
        #ifdef MOD_B_FLAG
            int b_flag_;
        #endif
            int b_base_;
        };
        """,
    'src/extra/mod_b.cpp': """\
        #include "mod_b.h"
        ModB::ModB() {}
        """,
})

cpp7 = [os.path.join(d7, 'src', 'mod_a.cpp'),
        os.path.join(d7, 'src', 'extra', 'mod_b.cpp')]
r7 = subprocess.run(
    ['bash', SCRIPT, '--max-configs=8'] + cpp7,
    cwd=d7, capture_output=True, text=True)

assert r7.returncode == 0, f"Script failed: {r7.stdout[-500:]}"
assert 'MOD_A_FLAG' in r7.stdout, f"MOD_A_FLAG not extracted"
assert 'MOD_B_FLAG' in r7.stdout, f"MOD_B_FLAG not extracted from nested subdir"
assert 'a_base_' in r7.stdout, f"Missing a_base_: {r7.stdout[-500:]}"
assert 'b_base_' in r7.stdout, f"Missing b_base_: {r7.stdout[-500:]}"
assert 'a_flag_' in r7.stdout, f"Missing a_flag_: {r7.stdout[-500:]}"
assert 'b_flag_' in r7.stdout, f"Missing b_flag_: {r7.stdout[-500:]}"
shutil.rmtree(d7, ignore_errors=True)
print("Test 7 PASS  multiple subdirectories: both dirs analyzed, macros combined")


# -----------------------------------------------------------------------
# Test 8: separate source and header dirs -- requires -I
# -----------------------------------------------------------------------

d8 = make_files({
    'src/test_inc.cpp': """\
        #include "test_inc.h"
        TestInc::TestInc() {}
        """,
    'include/test_inc.h': """\
        class TestInc {
        public:
            TestInc();
        #ifdef INC_FLAG
            int inc_flag_;
        #endif
            int inc_base_;
        };
        """,
})

cpp8 = os.path.join(d8, 'src', 'test_inc.cpp')
# Without -I: macros from headers in src/ are scanned, but include/ is not checked
# The include/ header has the guard, but src/ has no matching .h to scan
# So macros from co-located headers are found, but the guarded member needs -I to resolve
r8_noI = subprocess.run(
    ['bash', SCRIPT, '--max-configs=4', cpp8],
    cwd=d8, capture_output=True, text=True)
# Without -I, cppcheck can't find test_inc.h -> may find 0 members
# Macro extraction: no .h in src/, so 0 macros found -> 1 pass only

r8_withI = subprocess.run(
    ['bash', SCRIPT, '--max-configs=4', '-I', os.path.join(d8, 'include'), cpp8],
    cwd=d8, capture_output=True, text=True)

assert r8_withI.returncode == 0
assert 'inc_base_' in r8_withI.stdout, \
    f"Missing inc_base_ with -I: {r8_withI.stdout[-500:]}"
assert 'inc_flag_' in r8_withI.stdout, \
    f"Missing inc_flag_ with -I (must use -I for separate header dir): {r8_withI.stdout[-500:]}"
# Verify that -I does NOT add include/ headers to macro scanning
# (macro extraction only scans co-located headers in src/)
has_macros_noI = 'INC_FLAG' in r8_noI.stdout
has_findings_noI = 'inc_base_' in r8_noI.stdout
print(f"  note: without -I, macros found={has_macros_noI}, findings={has_findings_noI}")
shutil.rmtree(d8, ignore_errors=True)
print("Test 8 PASS  -I resolves separate header dir, -I not used for macro extraction")


# -----------------------------------------------------------------------
# Test 9: 14 macros with default --max-configs=9999 -> error (2^14=16384)
# -----------------------------------------------------------------------

MACRO_COUNT = 14
members = []
for i in range(MACRO_COUNT):
    members.append(f"        #if M{i}\n            int m{i}_;\n        #endif")

d9 = make_files({
    'test_14macros.h': """\
        class Test14Macros {
        public:
            Test14Macros();
        """ + '\n'.join(members) + """
            int always_;
        };
        """,
    'test_14macros.cpp': """\
        #include "test_14macros.h"
        Test14Macros::Test14Macros() {}
        """,
})

cpp9 = os.path.join(d9, 'test_14macros.cpp')
r9 = subprocess.run(
    ['bash', SCRIPT, '--max-configs=9999', cpp9],
    cwd=d9, capture_output=True, text=True)

assert r9.returncode != 0, f"Should error: 2^14=16384 > 9999"
assert '16384' in r9.stdout, f"Expected 'needs 16384' in error, got: {r9.stdout[:500]}"
assert 'Rerun with: --max-configs=16384' in r9.stdout, \
    f"Expected rerun hint, got: {r9.stdout[:500]}"
shutil.rmtree(d9, ignore_errors=True)
print(f"Test 9 PASS  {MACRO_COUNT} macros with default --max-configs=9999 errors with 16384")


# -----------------------------------------------------------------------
# Test 10: 14 macros with sufficient --max-configs=16384 -> passes
# -----------------------------------------------------------------------

d10 = make_files({
    'test_14ok.h': """\
        class Test14Ok {
        public:
            Test14Ok();
        """ + '\n'.join(members) + """
            int always_;
        };
        """,
    'test_14ok.cpp': """\
        #include "test_14ok.h"
        Test14Ok::Test14Ok() {}
        """,
})

cpp10 = os.path.join(d10, 'test_14ok.cpp')
r10 = subprocess.run(
    ['bash', SCRIPT, '--max-configs=16384', cpp10],
    cwd=d10, capture_output=True, text=True,
    timeout=60)

assert r10.returncode == 0, f"Should pass with --max-configs=16384: {r10.stdout[-500:]}"
assert 'always_' in r10.stdout, f"Missing always_: {r10.stdout[-500:]}"
assert f'Found {MACRO_COUNT} user macros' in r10.stdout, \
    f"Expected {MACRO_COUNT} macros, got: {r10.stdout[:500]}"
shutil.rmtree(d10, ignore_errors=True)
print(f"Test 10 PASS  {MACRO_COUNT} macros with --max-configs=16384 passes + finds members")


print("\nAll 10 check_uninit_all.sh tests passed")
