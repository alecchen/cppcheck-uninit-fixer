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


# -----------------------------------------------------------------------
# Test 9: an explicit header only touches that header, not its whole dir
# -----------------------------------------------------------------------

d = make_project({
    'a.h': """\
        class OnlyHeader {
        private:
            int header_member;
        };
        """,
    'b.c': """\
        struct SiblingC {
            int c_member;
        };
        """,
    'c.cpp': """\
        class SiblingCpp {
        private:
            int cpp_member;
        };
        """,
})
r = subprocess.run(
    [sys.executable, FIXER, '--fast', os.path.join(d, 'a.h')],
    cwd=d, capture_output=True, text=True)
assert r.returncode == 0, f"--fast failed: {r.stderr[-300:]}"
assert 'header_member = 0' in open(os.path.join(d, 'a.h')).read()
assert 'c_member = 0' not in open(os.path.join(d, 'b.c')).read(), \
    f"sibling .c modified: {open(os.path.join(d, 'b.c')).read()}"
assert 'cpp_member = 0' not in open(os.path.join(d, 'c.cpp')).read(), \
    f"sibling .cpp modified: {open(os.path.join(d, 'c.cpp')).read()}"

# A directory input still scans everything in it, headers included.
r = subprocess.run(
    [sys.executable, FIXER, '--fast', '--report-only', d],
    cwd=d, capture_output=True, text=True)
assert 'c_member' not in r.stdout, f".c parsed as class body: {r.stdout[-400:]}"
assert 'cpp_member' in r.stdout, f".cpp not scanned from dir: {r.stdout[-400:]}"
shutil.rmtree(d, ignore_errors=True)
print("Test 9 PASS  explicit header does not sweep its directory")


# -----------------------------------------------------------------------
# Test 10: statements that merely name a member are never edited
# -----------------------------------------------------------------------

d = make_project({
    'a.h': """\
        class IterCfg {
        private:
            int _iter;
            int _niter;
        };
        """,
    'b.cpp': """\
        void runCfg(IterCfg* c)
        {
            _iter = _niter;
            c->_iter = 1;
        }
        """,
})
r = subprocess.run(
    [sys.executable, FIXER, '--fast', os.path.join(d, 'a.h')],
    cwd=d, capture_output=True, text=True)
assert r.returncode == 0, f"--fast failed: {r.stderr[-300:]}"
text = open(os.path.join(d, 'b.cpp')).read()
assert '_iter = _niter;' in text, f"statement rewritten:\n{text}"
assert '= 0' not in text, f"statement got a default:\n{text}"
assert '_iter = 0' in open(os.path.join(d, 'a.h')).read()
shutil.rmtree(d, ignore_errors=True)
print("Test 10 PASS  assignment statements are not treated as declarations")


# -----------------------------------------------------------------------
# Test 11: trailing comments do not hide a declaration
# -----------------------------------------------------------------------

d = make_project({
    'c.h': """\
        class Tone {
        private:
            int paren_comment_;   // range (0..9)
            int block_comment_;   /* see f(x) */
            int plain_;
        };
        """,
})
r = subprocess.run(
    [sys.executable, FIXER, '--fast', os.path.join(d, 'c.h')],
    cwd=d, capture_output=True, text=True)
assert r.returncode == 0, f"--fast failed: {r.stderr[-300:]}"
text = open(os.path.join(d, 'c.h')).read()
for mn in ('paren_comment_', 'block_comment_', 'plain_'):
    assert f'{mn} = 0;' in text, f"{mn} not defaulted:\n{text}"
shutil.rmtree(d, ignore_errors=True)
print("Test 11 PASS  comments containing parens do not hide members")


# -----------------------------------------------------------------------
# Test 12: class headers with export macros and final are parsed
# -----------------------------------------------------------------------

d = make_project({
    'k.h': """\
        class FINAL_ONE final {
        public:
            int final_member_;
        };
        class API_EXPORT Exported : public FINAL_ONE {
        public:
            int export_member_;
        };
        struct Tagged final : public FINAL_ONE {
        public:
            int tag_member_;
        };
        """,
})
r = subprocess.run(
    [sys.executable, FIXER, '--fast', '--report-only', os.path.join(d, 'k.h')],
    cwd=d, capture_output=True, text=True)
assert r.returncode == 0, f"--fast failed: {r.stderr[-300:]}"
for cls, mn in (('FINAL_ONE', 'final_member_'), ('Exported', 'export_member_'),
                ('Tagged', 'tag_member_')):
    assert f'{cls}::{mn}' in r.stdout, f"{cls}::{mn} missing: {r.stdout[-400:]}"
assert 'Class::' not in r.stdout, f"class name taken from header: {r.stdout[-400:]}"
shutil.rmtree(d, ignore_errors=True)
print("Test 12 PASS  export macros and final in class headers")

# export macro that takes an argument, and a template base
d = make_project({
    'k2.h': """\
        class DECLSPEC(Tone) ParenExported {
        public:
            int paren_member_;
        };
        class Derived : public Base<int> {
        public:
            int derived_member_;
        };
        """,
})
r = subprocess.run(
    [sys.executable, FIXER, '--fast', '--report-only', os.path.join(d, 'k2.h')],
    cwd=d, capture_output=True, text=True)
assert r.returncode == 0, f"--fast failed: {r.stderr[-300:]}"
for cls, mn in (('ParenExported', 'paren_member_'),
                ('Derived', 'derived_member_')):
    assert f'{cls}::{mn}' in r.stdout, f"{cls}::{mn} missing: {r.stdout[-400:]}"
shutil.rmtree(d, ignore_errors=True)


# -----------------------------------------------------------------------
# Test 13: method bodies are not parsed as member declarations
# -----------------------------------------------------------------------

d = make_project({
    'm.h': """\
        class Mix {
        public:
            int before_;
            int f(int a)
            {
                int local_body_;
                return a;
            }
            int inline_g() { int local_inline_; return 0; }
            int after_;
            int declared_(int b);
            int after_decl_;
            DISALLOW_COPY(Mix);
            int after_macro_;
        };
        """,
})
hdr = os.path.join(d, 'm.h')
r = subprocess.run(
    [sys.executable, FIXER, '--fast', '--report-only', hdr],
    cwd=d, capture_output=True, text=True)
assert r.returncode == 0, f"--fast failed: {r.stderr[-300:]}"
for bad in ('local_body_', 'local_inline_', 'a', 'b'):
    assert f'::{bad} ' not in r.stdout and f'::{bad}\n' not in r.stdout, \
        f"method local {bad} treated as a member: {r.stdout[-400:]}"
for mn in ('before_', 'after_', 'after_decl_', 'after_macro_'):
    assert f'Mix::{mn}' in r.stdout, f"member {mn} lost: {r.stdout[-400:]}"

r = subprocess.run([sys.executable, FIXER, '--fast', hdr],
                   cwd=d, capture_output=True, text=True)
assert r.returncode == 0, f"--fast fix failed: {r.stderr[-300:]}"
text = open(hdr).read()
assert 'int local_body_ = 0' not in text and 'int local_inline_ = 0' not in text, \
    f"method local got a default:\n{text}"
assert 'DISALLOW_COPY(Mix) = 0' not in text, f"macro line edited:\n{text}"
for mn in ('before_', 'after_', 'after_decl_', 'after_macro_'):
    assert f'int {mn} = 0;' in text, f"{mn} not defaulted:\n{text}"
shutil.rmtree(d, ignore_errors=True)
print("Test 13 PASS  method bodies skipped in parse, report and fix")


# -----------------------------------------------------------------------
# Test 14: --fail-on-findings gates CI, and stays off by default
# -----------------------------------------------------------------------

d = make_project({
    'dirty.h': """\
        class Dirty {
        private:
            int unset_;
        };
        """,
    'clean.h': """\
        class Clean {
        private:
            int set_ = 0;
        };
        """,
})
dirty = os.path.join(d, 'dirty.h')
clean = os.path.join(d, 'clean.h')


def gate(args):
    return subprocess.run([sys.executable, FIXER, '--fast', '--report-only'] + args,
                          cwd=d, capture_output=True, text=True).returncode


assert gate([dirty]) == 0, "report-only must stay 0 by default"
assert gate(['--fail-on-findings', dirty]) == 1, "gate must fail on findings"
assert gate(['--fail-on-findings', clean]) == 0, "gate must pass on a clean file"
# no inputs is a usage error, not a pass: callers guard the empty file list
assert gate(['--fail-on-findings']) == 1, "no inputs must not silently pass a gate"

# the flag is a report-only knob: fix mode still writes and exits 0
r = subprocess.run([sys.executable, FIXER, '--fast', '--fail-on-findings', dirty],
                   cwd=d, capture_output=True, text=True)
assert r.returncode == 0, f"fix mode must not gate: {r.stderr[-300:]}"
assert 'unset_ = 0' in open(dirty).read(), "fix mode must still apply defaults"
shutil.rmtree(d, ignore_errors=True)
print("Test 14 PASS  --fail-on-findings gates report-only, off by default")


print("\nAll 14 --fast mode tests passed")
