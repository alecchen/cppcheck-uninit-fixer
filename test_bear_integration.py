#!/usr/bin/env python3
"""Integration test: verify -I and --project=compile_commands.json workflows."""
import subprocess, sys, os, tempfile, shutil, json

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)

script_dir = os.path.dirname(os.path.abspath(__file__))
fixer = os.path.join(script_dir, 'cppcheck-fix-uninit.py')

# Create a project with headers in a separate include/ dir
test_dir = tempfile.mkdtemp(suffix='_uninit_test')
src_dir = os.path.join(test_dir, 'src')
inc_dir = os.path.join(test_dir, 'include')
os.makedirs(src_dir)
os.makedirs(inc_dir)

def write_project():
    with open(os.path.join(inc_dir, 'config.h'), 'w') as f:
        f.write("""#ifndef CONFIG_H
#define CONFIG_H
class AppConfig {
public:
    AppConfig();
    int getPort() const { return port_; }
    const char* getHost() const { return host_; }
    bool getDebug() const { return debug_; }
    float getTimeout() const { return timeout_; }
private:
    int port_;
    const char* host_;
    bool debug_;
    float timeout_;
};
#endif
""")
    with open(os.path.join(src_dir, 'main.cpp'), 'w') as f:
        f.write('#include "config.h"\nAppConfig::AppConfig() {}\n')

write_project()
main_cpp = os.path.join(src_dir, 'main.cpp')

# Test 1: -I flag
print("=== Test 1: -I flag ===")
r = run([sys.executable, fixer, '-v', '-I', inc_dir, main_cpp], cwd=test_dir)
ok1 = 'AppConfig' in r.stderr and 'port_' in r.stderr
print(f"  {'PASS' if ok1 else 'FAIL'} -I flag: found AppConfig members")

# Restore both source and header
write_project()

# Test 2: --project with relative file path
print("=== Test 2: --project (relative) ===")
cc_json = os.path.join(test_dir, 'compile_commands.json')
with open(cc_json, 'w') as f:
    json.dump([{
        "directory": str(src_dir),
        "command": f"g++ -c -I{inc_dir} main.cpp -o /dev/null",
        "file": "main.cpp"
    }], f, indent=2)
r = run([sys.executable, fixer, '-v', '--project', cc_json], cwd=test_dir)
ok2 = 'AppConfig' in r.stderr and 'port_' in r.stderr
print(f"  {'PASS' if ok2 else 'FAIL'} --project (relative):", end="")
print(f" found AppConfig members" if ok2 else f" 0 findings\n{r.stderr[:500]}")
write_project()

# Test 3: --project + positional sources
print("=== Test 3: --project + sources ===")
r = run([sys.executable, fixer, '-v', '--project', cc_json, main_cpp], cwd=test_dir)
ok3 = 'AppConfig' in r.stderr and 'port_' in r.stderr
print(f"  {'PASS' if ok3 else 'FAIL'} --project + sources:", end="")
print(f" found AppConfig members" if ok3 else f" 0 findings\n{r.stderr[:500]}")

# Test 4: bear integration (informational)
print("=== Test 4: bear integration ===")
try:
    r = run(['bear', '--version'], cwd=test_dir)
    if r.returncode == 0:
        with open(os.path.join(test_dir, 'compile.sh'), 'w') as f:
            f.write("#!/bin/sh\ng++ -c -I../include src/main.cpp -o /dev/null\n")
        os.chmod(os.path.join(test_dir, 'compile.sh'), 0o755)
        r = run(['bear', '--output', os.path.join(test_dir, 'cc_bear.json'),
                 '--', 'sh', 'compile.sh'], cwd=test_dir)
        if os.path.exists(os.path.join(test_dir, 'cc_bear.json')):
            print(f"  PASS: bear generated compile_commands.json")
        else:
            print(f"  bear failed: {r.stderr[:200]}")
    else:
        print("  SKIP: bear unavailable")
except FileNotFoundError:
    print("  SKIP: bear not found")

shutil.rmtree(test_dir)
all_pass = all([ok1, ok2, ok3])
sys.exit(0 if all_pass else 1)
