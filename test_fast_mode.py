#!/usr/bin/env python3
"""Test --fast mode: every POD member gets a default, no cppcheck run."""
import importlib.util
import os
import subprocess
import sys
import shutil

from test_helpers import make_project

FIXER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     'cppcheck-fix-uninit.py')
spec = importlib.util.spec_from_file_location("fast_fixer", FIXER)
fixer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixer)


EDGE_H = """\
    class Widget {
    public:
        Widget();
        void set_id(int id);
        int get_id(int extra, double scale);
        void callback(int id_, double value_);
        DISALLOW_COPY(Widget);
        void (*cb_)(int);
        static int static_count_;
        int a_, b_;
        std::string name_;
        int& ref_;
        int already_ = 0;
    private:
        int id_;
        double value_;
    };
    """

# -----------------------------------------------------------------------
# Test 1: method args are never parsed as members (no parens leak)
# -----------------------------------------------------------------------

d = make_project({'w.h': EDGE_H})
mt = fixer.parse_member_types([os.path.join(d, 'w.h')])
g = mt.get('Widget', {})
assert 'set_id' not in g, f"method parsed as member: {sorted(g)}"
assert 'get_id' not in g, f"method parsed as member: {sorted(g)}"
assert 'callback' not in g, f"method parsed as member: {sorted(g)}"
assert 'extra' not in g, f"method arg parsed as member: {sorted(g)}"
assert 'scale' not in g, f"method arg parsed as member: {sorted(g)}"
assert 'cb_' not in g, f"function pointer should be skipped: {sorted(g)}"
assert 'id_' in g, f"real member id_ missing: {sorted(g)}"
# multi-decl line captures every var
assert 'a_' in g, f"a_ missing from 'int a_, b_': {sorted(g)}"
assert 'b_' in g, f"b_ missing from 'int a_, b_': {sorted(g)}"
shutil.rmtree(d, ignore_errors=True)
print("Test 1 PASS  method declarations/args never parsed as members")


# -----------------------------------------------------------------------
# Test 2: fast candidates skip string/ref/static/already-init
# -----------------------------------------------------------------------

d = make_project({'w.h': EDGE_H})
hdrs = [os.path.join(d, 'w.h')]
mt = fixer.parse_member_types(hdrs)
cands = fixer.find_uninit_candidates(hdrs, mt)
names = sorted(c[3] for c in cands)
assert 'name_' not in names, f"std::string must be skipped: {names}"
assert 'ref_' not in names, f"reference must be skipped: {names}"
assert 'static_count_' not in names, f"static must be skipped: {names}"
assert 'already_' not in names, f"already-init must be skipped: {names}"
assert 'id_' in names and 'value_' in names, f"real members missing: {names}"
shutil.rmtree(d, ignore_errors=True)
print("Test 2 PASS  fast candidates skip string/ref/static/init'd")


# -----------------------------------------------------------------------
# Test 3: end-to-end --fast adds defaults, no cppcheck needed
# -----------------------------------------------------------------------

d = make_project({
    'app.h': """\
        class App {
        public:
            App();
            void run(int verbose);
        private:
            int port_;
            const char* host_;
            int lo_, hi_;
            bool debug_;
            std::string name_;
        };
        """,
    'app.cpp': """\
        #include "app.h"
        App::App() {}
        """,
})
cpp = os.path.join(d, 'app.cpp')
r = subprocess.run(
    [sys.executable, FIXER, '--fast', '--report-only', cpp],
    cwd=d, capture_output=True, text=True)
assert r.returncode == 0, f"--fast failed: {r.stderr[-500:]}"
assert 'port_' in r.stdout and 'host_' in r.stdout and 'debug_' in r.stdout, \
    f"missing members: {r.stdout[-500:]}"
assert 'lo_' in r.stdout and 'hi_' in r.stdout, \
    f"multi-decl members missing: {r.stdout[-500:]}"
assert 'name_' not in r.stdout, f"std::string must be skipped: {r.stdout[-500:]}"
assert 'verbose' not in r.stdout, f"method arg leaked: {r.stdout[-500:]}"

r = subprocess.run(
    [sys.executable, FIXER, '--fast', cpp],
    cwd=d, capture_output=True, text=True)
assert r.returncode == 0, f"--fast fix failed: {r.stderr[-500:]}"
text = open(os.path.join(d, 'app.h')).read()
assert 'port_ = 0' in text, f"port_ not defaulted:\n{text}"
assert 'host_ = nullptr' in text, f"host_ not defaulted:\n{text}"
assert 'lo_ = 0' in text and 'hi_ = 0' in text, \
    f"multi-decl line not fixed:\n{text}"
assert 'debug_ = false' in text, f"debug_ not defaulted:\n{text}"
assert 'name_ = ' not in text, f"std::string must be untouched:\n{text}"
shutil.rmtree(d, ignore_errors=True)
print("Test 3 PASS  --fast end-to-end: defaults added, string/args untouched")


# -----------------------------------------------------------------------
# Test 4: --fast --report-only never invokes cppcheck
# (poison PATH with a failing cppcheck shim)
# -----------------------------------------------------------------------

d = make_project({
    'app.h': """\
        class App {
        public:
            App();
        private:
            int port_;
        };
        """,
    'app.cpp': """\
        #include "app.h"
        App::App() {}
        """,
    'bin/cppcheck': """\
        #!/bin/sh
        echo "cppcheck must not run in --fast mode" >&2
        exit 99
        """,
})
os.chmod(os.path.join(d, 'bin', 'cppcheck'), 0o755)
env = dict(os.environ, PATH=os.path.join(d, 'bin') + ':' + os.environ['PATH'])
r = subprocess.run(
    [sys.executable, FIXER, '--fast', '--report-only',
     os.path.join(d, 'app.cpp')],
    cwd=d, capture_output=True, text=True, env=env)
assert r.returncode == 0, f"--fast failed with shimmed cppcheck: {r.stderr[-300:]}"
assert 'port_' in r.stdout, f"missing port_: {r.stdout[-300:]}"
shutil.rmtree(d, ignore_errors=True)
print("Test 4 PASS  --fast never invokes cppcheck")


# Test 5: --fast accepts headers and directories directly
# -----------------------------------------------------------------------

d = make_project({
    'inc/widget.h': """\
        class Widget {
        public:
            Widget();
        private:
            int id_;
            double value_;
        };
        """,
})
hdr = os.path.join(d, 'inc', 'widget.h')
for target in ([hdr], [os.path.join(d, 'inc')]):
    r = subprocess.run(
        [sys.executable, FIXER, '--fast', '--report-only'] + target,
        cwd=d, capture_output=True, text=True)
    assert r.returncode == 0, f"--fast failed on {target}: {r.stderr[-300:]}"
    assert 'id_' in r.stdout and 'value_' in r.stdout, \
        f"missing members for {target}: {r.stdout[-300:]}"
shutil.rmtree(d, ignore_errors=True)
print("Test 5 PASS  --fast accepts .h files and directories")


# Test 6: classes defined in a .cpp are parsed (internal class, pimpl)
# -----------------------------------------------------------------------

d = make_project({
    'impl.cpp': """\
        #include <string>

        class Impl {
        public:
            Impl();
        private:
            int handle_;
            const char* name_;
            std::string buf_;
        };

        Impl::Impl() {}

        class Holder {
        public:
            Holder();
        private:
            struct Impl2;
            Impl2* p_;
            int flags_;
        };

        Holder::Holder() {}
        """,
})
cpp = os.path.join(d, 'impl.cpp')
r = subprocess.run(
    [sys.executable, FIXER, '--fast', '--report-only', cpp],
    cwd=d, capture_output=True, text=True)
assert r.returncode == 0, f"--fast failed: {r.stderr[-300:]}"
for mn in ('handle_', 'name_', 'p_', 'flags_'):
    assert mn in r.stdout, f"missing {mn} from .cpp-defined class: {r.stdout[-400:]}"
assert 'buf_' not in r.stdout, f"std::string must be skipped: {r.stdout[-400:]}"

# function-body locals must not be picked up
d2 = make_project({
    'body.cpp': """\
        class Only {
        public:
            Only();
        private:
            int real_;
        };

        Only::Only() {
            int local = 0;
            char buf[64];
            for (int i = 0; i < 3; ++i) {
                long inner;
            }
        }
        """,
})
r2 = subprocess.run(
    [sys.executable, FIXER, '--fast', '--report-only',
     os.path.join(d2, 'body.cpp')],
    cwd=d2, capture_output=True, text=True)
assert 'real_' in r2.stdout, f"missing real_: {r2.stdout[-300:]}"
for bad in ('local', 'buf', 'inner', 'i'):
    assert f'::{bad} ' not in r2.stdout and f'::{bad}\n' not in r2.stdout, \
        f"function-body local {bad} leaked: {r2.stdout[-400:]}"
shutil.rmtree(d, ignore_errors=True)
shutil.rmtree(d2, ignore_errors=True)
print("Test 6 PASS  .cpp-defined classes parsed, function locals ignored")


# -----------------------------------------------------------------------
# Test 7: nested class members are not attributed to the outer class
# -----------------------------------------------------------------------

d = make_project({
    'nest.h': """\
        class Outer {
        public:
            Outer();
            class Inner {
            public:
                Inner();
            private:
                int inner_b_;
            };
        private:
            int outer_c_;
        };
        """,
    'nest.cpp': """\
        #include "nest.h"
        Outer::Outer() {}
        """,
})
r = subprocess.run(
    [sys.executable, FIXER, '--fast', '--report-only',
     os.path.join(d, 'nest.h')],
    cwd=d, capture_output=True, text=True)
assert r.returncode == 0, f"--fast failed: {r.stderr[-300:]}"
assert 'Outer::inner_b_' not in r.stdout, \
    f"nested member attributed to outer class: {r.stdout[-400:]}"
assert 'Inner::inner_b_' in r.stdout, \
    f"nested member missing from its own class: {r.stdout[-400:]}"
assert 'Outer::outer_c_' in r.stdout, f"missing outer_c_: {r.stdout[-400:]}"

# apply and check the fix lands in the right class
r = subprocess.run(
    [sys.executable, FIXER, '--fast', os.path.join(d, 'nest.h')],
    cwd=d, capture_output=True, text=True)
text = open(os.path.join(d, 'nest.h')).read()
assert 'int inner_b_ = 0;' in text, f"inner_b_ not fixed:\n{text}"
assert 'int outer_c_ = 0;' in text, f"outer_c_ not fixed:\n{text}"
assert text.count('inner_b_ = 0') == 1, f"inner_b_ fixed twice:\n{text}"
shutil.rmtree(d, ignore_errors=True)
print("Test 7 PASS  nested class members attributed correctly")


# -----------------------------------------------------------------------
# Test 8: --fast warns when it parses nothing
# -----------------------------------------------------------------------

d = make_project({
    'empty.cpp': """\
        int main() { return 0; }
        """,
})
r = subprocess.run(
    [sys.executable, FIXER, '--fast', '--report-only',
     os.path.join(d, 'empty.cpp')],
    cwd=d, capture_output=True, text=True)
assert r.returncode == 0
assert 'no members parsed' in r.stderr, \
    f"expected a warning on zero members: {r.stderr[-300:]}"
shutil.rmtree(d, ignore_errors=True)
print("Test 8 PASS  --fast warns when nothing is parsed")


print("\nAll 8 --fast mode tests passed")
