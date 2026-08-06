# cppcheck Uninitialized Member Detector

Find and auto-fix uninitialized member variables in C++ classes with automatic handling of `#if`/`#ifdef`/`#ifndef`/`#elif` preprocessor guards.

## Quick Start

```bash
# Find only (bash script)
./check_uninit_all.sh *.cpp

# Find + auto-fix in one step (Python script)
python3 cppcheck-fix-uninit.py *.cpp

# Preview without modifying
python3 cppcheck-fix-uninit.py --report-only *.cpp

# For #if-heavy code, in-class mode handles guards automatically (default).
# For constructor init-lists instead: add --init-list
```


## The Problem

cppcheck finds most uninitialized members of primitive types (`int`, `double`, `float`, `bool`, `char*`, etc.), but it has blind spots when the code uses preprocessor conditionals:

1. **Config discovery depends on parsing.** cppcheck discovers config variables from `#ifdef` blocks it encounters during analysis. If member declarations live in template-heavy or third-party headers (boost, yaml-cpp, igraph) that cppcheck cannot fully parse, those guards are invisible.

2. **Default config limit.** cppcheck checks at most 12 configs per file by default. With N config variables, exhaustive checking needs 2^N configs. At 14+ macros, even `--max-configs=9999` is too low.

Both scripts solve this with a per-macro strategy.

## How It Works

### Per-macro cppcheck strategy

The bash script extracts all user macros from `#if`/`#ifdef`/`#ifndef`/`#elif` directives, then runs cppcheck once per macro with just that macro defined (`-DMACRO=1`), plus a base pass with no extra defines.

| Pass | What | Catches |
|---|---|---|
| Base | No `-D` flags | Members behind `#ifndef MACRO`, `#if !defined(MACRO)` |
| `-DMACRO1=1` | Macro 1 defined | Members behind `#ifdef MACRO1`, `#if MACRO1` |
| `-DMACRO2=1` | Macro 2 defined | Members behind `#ifdef MACRO2`, `#if MACRO2` |
| ... | One per macro | All single-macro guards |

The `--cppcheck-build-dir` cache makes repeated runs fast — each subsequent pass only rechecks code paths affected by the different `-D` flag.

### Max-configs guard

Both scripts check whether `--max-configs` can cover all combinations of the discovered macros. If 2^N exceeds the limit, they error out with the required value:

```text
ERROR: --max-configs=9999 is too low for 14 macros.
  Worst-case configs needed: 2^14 = 16384
  Rerun with: --max-configs=16384
```

Pass `--max-configs=N` to override:

```bash
# bash script: pass through as a cppcheck flag
./check_uninit_all.sh --max-configs=16384 *.cpp

# Python script: built-in argument
python3 cppcheck-fix-uninit.py --max-configs=16384 *.cpp
```

### Compound condition detection

The bash script flags `#if` conditions with 3+ user macros for manual review:

```text
=== Compound conditions detected ===
  [3+ macros] config.h: #if FEATURE_A && FEATURE_B && !FEATURE_C
    Manual check needed: run with selective -D/-U flags for this condition
```

### Fix mode: in-class defaults (default) vs init-list (opt-in)

The Python fixer has two modes:

**Default: in-class initializers.** Adds `= 0` / `= nullptr` / `= false` directly to the member declaration in the header:

```cpp
// Before                     // After
int a_;                       int a_ = 0;
const char* label_;           const char* label_ = nullptr;
uint32_t u32_;                uint32_t u32_ = 0;
```

One change covers all constructors. Works correctly with `#if`-guarded members because the initializer lives in the same preprocessor block:

```cpp
#if BUILD_PERFORMANCE
    int cache_size_ = 0;   // only compiled when BUILD_PERFORMANCE is defined
#endif
```

**Opt-in: constructor init-list.** Adds `: member_(0)` to each constructor in the `.cpp`:

```bash
./cppcheck-fix-uninit.py --init-list *.cpp
```

Produces:

```cpp
Foo::Foo()
    : a_(0)
    , label_(nullptr)
{}
```

Use `--init-list` for projects that prefer explicit per-constructor initialization over in-class defaults. `#if`-guarded members are **skipped** in init-list mode. The C++ init-list comma syntax is position-dependent, so inserting a conditional entry unconditionally produces invalid code when the guard evaluates to false.

## Usage

### Find only (bash script)

```bash
# Scan .cpp files in current directory
./check_uninit_all.sh *.cpp

# With include paths
./check_uninit_all.sh -I /path/to/api *.cpp

# With additional flags passed through
./check_uninit_all.sh -I /path -j 8 --suppress=unusedFunction *.cpp

# Recursive directory scan (zsh — default on macOS)
./check_uninit_all.sh dir/**/*.cpp

# Recursive directory scan (bash)
shopt -s globstar
./check_uninit_all.sh dir/**/*.cpp

# Recursive directory scan (universal — any shell)
find dir/ -name '*.cpp' -print0 | xargs -0 ./check_uninit_all.sh

# Narrow to specific subdirectory, exclude third-party code
./check_uninit_all.sh dir/src/**/*.cpp
```

### Find + auto-fix (Python script)

```bash
# Default: add in-class =0 initializers to headers (handles #if guards)
./cppcheck-fix-uninit.py *.cpp
./cppcheck-fix-uninit.py -I /path/to/api *.cpp

# With compile_commands.json (resolves -I and -D from build)
bear -- ./build_script.sh
./cppcheck-fix-uninit.py --project=compile_commands.json
./cppcheck-fix-uninit.py --project=compile_commands.json main.cpp

# Verbose: show macros, cppcheck commands, member types with guards
./cppcheck-fix-uninit.py -v *.cpp

# Opt-in: constructor initializer lists instead (skips guarded members)
./cppcheck-fix-uninit.py --init-list *.cpp

# Preview only, do not modify
./cppcheck-fix-uninit.py --report-only *.cpp
./cppcheck-fix-uninit.py -v --report-only *.cpp
	# Raise config limit for codebases with many feature flags
	./cppcheck-fix-uninit.py --max-configs=16384 *.cpp
```

Creates `.bak` backup files before any modification.

**Important:** Always pass `.cpp` source files, not `.h` headers. cppcheck analyzes headers through the `.cpp` translation unit's `#include` directives. Passing only `.h` files produces zero findings because cppcheck cannot flag uninitialized members without seeing the constructors. Passing both `.cpp` and `.h` is harmless but redundant.

**Include paths:** If headers are in a different directory than the `.cpp` files, `-I` is required for **both** detection and fixing.

- **Bash script (`check_uninit_all.sh`):** `-I` only affects cppcheck's analysis. Macro extraction only scans `*.h`/`*.hpp` files co-located with the source files. If your guarded members are in headers under an `-I` directory, those guards will still be checked by cppcheck's own config detection during the base pass, but the macros won't appear in the per-macro pass list.
- **Python fixer (`cppcheck-fix-uninit.py`):** `-I` affects both cppcheck's analysis AND macro extraction, since `find_headers()` scans `-I` directories for macros. This means macros from separate include directories DO appear in the per-macro pass list.

The Python fixer needs `-I` to find the headers for type parsing and in-class default insertion. Without `-I`, type info will not be found and the fixer falls back to `{}` values. Use the same `-I` flags for the Python fixer as you use for the bash script. Passing `--project=compile_commands.json` resolves includes automatically.

### Test harness

```bash
# Regenerate both test suites (resets all uninitialized members)
./generate_test.sh

# Test 1: basic (57 uninit members, no #if, 29 member types)
./cppcheck-fix-uninit.py uninit_test.cpp

# Guarded (15 uninit members, 6 #if flags, compound conditions)
./cppcheck-fix-uninit.py -v uninit_guarded_test.cpp

# Both together
./cppcheck-fix-uninit.py uninit_test.cpp uninit_guarded_test.cpp

# Verify #elif/#else guard chain tracking
make check

# Full test suite (guard chain + -I/--project integration)
make check-all
```

## Supported Member Types

All primitive types, pointers, references, and enums are detected **regardless of qualifiers** (`const`, `volatile`, `const volatile`) on the type, the pointer, or both. Fixed-width integer types from `<cstdint>` are detected too.

### Detected

| Category | Types | Default init value |
|---|---|---|
| **Plain integers** | `char`, `signed/unsigned char`, `short`, `unsigned short` | `0` / `'\0'` |
| | `int`, `unsigned`, `long`, `unsigned long` | `0` |
| | `long long`, `unsigned long long` | `0` |
| **Fixed-width** | `int8_t..uint64_t`, `size_t`, `ptrdiff_t` | `0` |
| **Floating-point** | `float` / `double` / `long double` | `0.0f` / `0.0` |
| **Enum** | `enum` (scoped and unscoped) | `{}` |
| **Bool** | `bool` | `false` |
| **Qualified** | `const T`, `volatile T`, `const volatile T` | (same as T) |
| **Pointers** | `T*`, `const T*`, `T* const`, `const T* const` | `nullptr` |
| | `volatile T*`, `T* volatile`, `const volatile T*` | `nullptr` |
| **References** | `T&`, `const T&` | must bind (skipped) |
| **Ptr-to-member** | `int T::*` | `nullptr` |

**Note on `const` and reference members:** `const T` and `T&`/`const T&` members are compiler-mandated in the initializer list. In-class initialization works for `const T` in C++11 (`const int a_ = 0`) but not for references (they need a binding target). cppcheck flags them when missing; references are skipped by the auto-fixer.

### NOT detected

Types with their own default constructor. cppcheck considers them "initialized" even if forgotten:

| Type | Safe default |
|---|---|
| `std::string`, `std::vector<T>` | `{}` (empty) |
| `std::unique_ptr<T>`, `std::shared_ptr<T>` | `{}` (nullptr) |
| `std::optional<T>`, `std::variant<T...>` | `{}` (nullopt / first alt) |
| Any class/struct with a default ctor | cppcheck trusts it |

A forgotten `std::string name_` in the initializer list will **not** be flagged. The default constructor produces a valid empty state.

### Primitive arrays and structs

| Type | Flags? | Notes |
|---|---|---|
| `int arr[10]` | No | Elements not checked individually |
| `struct Point { int x,y; };` | Depends | Only if `Point` has no default ctor |
| `union { int a; float b; };` | No | Not tracked |

### How to fix (in-class, default)

The fixer adds in-class default initializers to the header:

```cpp
class Foo {
    int a_ = 0;
    double b_ = 0.0;
    float c_ = 0.0f;
    bool d_ = false;
    char e_ = '\0';
    short f_ = 0;
    long g_ = 0;
    unsigned h_ = 0;
    const char* i_ = nullptr;
    int* j_ = nullptr;
    int k_ = 0;
    // const / reference
    const int l_ = 0;
    // const double& m_ = ???  -- skipped (reference)
    const int* n_ = nullptr;
    const char* const o_ = nullptr;
    int* const p_ = nullptr;
    // fixed-width
    uint32_t q_ = 0;
    uint64_t r_ = 0;
    int32_t s_ = 0;
    int64_t t_ = 0;
    size_t sz_ = 0;
    // volatile
    volatile int vol_ = 0;
    const volatile int cv_ = 0;
    const uint32_t const_u32_ = 0;
    // volatile pointer variants
    volatile int* vol_ptr_ = nullptr;
    int* volatile ptr_vol_ = nullptr;
    const volatile int* cv_ptr_ = nullptr;
    int* const volatile ptr_cv_ = nullptr;
    // RAII -- not flagged
    std::string name_;
};
```

### How to fix (init-list, opt-in with `--init-list`)

```cpp
Foo::Foo()
    : a_(0), b_(0.0), c_(0.0f), d_(false), e_('\0')
    , f_(0), g_(0), h_(0), i_(nullptr), j_(nullptr)
    , k_(0), l_(0)
    , n_(nullptr), o_(nullptr), p_(nullptr)
    , q_(0), r_(0), s_(0), t_(0), sz_(0)
    , vol_(0), cv_(0), const_u32_(0)
    , vol_ptr_(nullptr), ptr_vol_(nullptr)
    , cv_ptr_(nullptr), ptr_cv_(nullptr)
{}
```

## Output

### Bash script (`check_uninit_all.sh`)

Writes report to `uninit_report.txt`:

```text
uninit_test.cpp:6:13: warning: Member variable 'DataHolder::id_' is not initialized in the constructor. [uninitMemberVar]
uninit_test.cpp:11:13: warning: Member variable 'DataHolder::flag_' is not initialized in the constructor. [uninitMemberVar]
```

Lines with `inconclusive` appear when the constructor body is fully empty. `--inconclusive` must be enabled.

### Python script: default (in-class) mode

```text
Phase 1: scanning for macros...
  found 6 user macro(s)
Phase 2: running cppcheck (base + per-macro passes)...
Phase 3: parsing findings...
  15 uninitialized member(s)
Phase 4: parsing member types from headers...
  parsed 45 member(s) from headers
Phase 5: applying fixes...
  added in-class initializers for 12 member(s)

Done. All uninit members have in-class defaults. Backups (*.bak) created.
```

Verbose (`-v`) adds: found macro names, cppcheck command lines, member types with guards and defaults:

```text
    class ServiceConfig:
      int cache_size_ -> 0 [BUILD_PERFORMANCE]
      const char* host_ -> nullptr
      int legacy_port_ -> 0 [USE_LEGACY]
      int platform_id_ -> 0 [(PLATFORM_A || PLATFORM_B)]
      int retries_ -> 0
      float x_y_ratio_ -> 0.0f [FEATURE_X && FEATURE_Y]
```

### Python script: init-list mode (`--init-list`)

```text
Phase 5: applying fixes...
  note: ServiceConfig has guarded members that won't be auto-fixed
  fixed 4 constructor(s) in uninit_guarded_test.cpp
  fixed 3 constructor(s) in uninit_test.cpp

Done. Fixed 7 constructor(s). Backups (*.bak) created.
```

Guarded members (`[BUILD_PERFORMANCE]`, etc.) appear in findings but are skipped during auto-fix.

## Limitations

### Remaining blind spots

```text
#if A && B
    int hidden_;  // caught by per-macro -DA=1 pass (A on, B on in base config)
#endif

#if A && !B
    int hidden_;  // caught by -DA=1 pass (A on, B off — B is off in per-macro passes for other macros)
#endif

#if A && B && C
    int hidden_;  // NOT caught — no single pass has all three on
#endif
```

Per-macro passes catch all single-macro and two-macro `#if` combinations (because one macro is on from `-D` and the other is on from cppcheck's own config discovery). Three-macro combinations and `#if A && B && !C` patterns are flagged by the compound condition detector for manual review:

```bash
cppcheck -DA=1 -DB=1 -UC --enable=warning --inconclusive file.cpp
```

### cppcheck limitations

- Does not track `memset()`, placement `new`, or helper functions in the constructor body
- Marking a member as `= default` suppresses warnings
- Members with user-defined default constructors are never flagged

### Auto-fix limitations (`cppcheck-fix-uninit.py`)

- **Reference members** (`T&`, `const T&`) cannot be auto-fixed. There is no known binding target.
- **Header parsing** is regex-based. Complex types (nested templates, macros-as-types) fall back to `{}`, which is safe.
- **Preprocessor macros in member declarations** (`STATUS_FLAG flags_;`) are not parsed. They fall back to `{}`.
- **Init-list mode** (`--init-list`) skips `#if`-guarded members due to colon and comma syntax constraints. In-class mode (default) handles them correctly.

## Workflow

1. **Generate test files.** `./generate_test.sh` resets both test suites.
2. **Find.** `./check_uninit_all.sh -I /api *.cpp` to see what is uninitialized, or `python3 cppcheck-fix-uninit.py -v --report-only *.cpp` for a preview with guard info.
3. **Auto-fix.** `python3 cppcheck-fix-uninit.py *.cpp` adds in-class defaults.
4. **Compile and test.** Verify it compiles and works.
5. **Manual review.** Check `*.bak` files and handle reference members manually.
6. **Repeat.** `./generate_test.sh` resets for another round.

For best results with build flags:

```bash
bear -- ./build_script.sh
./cppcheck-fix-uninit.py --project=compile_commands.json
```

## Test files

| File | Uninit members | Features |
|---|---|---|
| `uninit_test.h/cpp` | 57 | All primitive/ptr/ref/volatile types, no guards |
| `uninit_guarded_test.h/cpp` | 15 | 6 feature flags, compound `&&`/`||`/`!` guards |
| `test_elif_guards.py` | - | Unit test for `#elif`/`#else` guard chain tracking |
| `test_check_uninit_all.py` | - | Unit test for per-macro passes and max-configs guard |
| `test_fix_uninit.py` | - | Unit test for Python fixer per-macro and -I behavior |
| `test_bear_integration.py` | - | Integration test for `-I` and `--project` workflows |

Regenerate test files with `./generate_test.sh`. Run `make check` for quick checks or `make check-all` for full suite.

## Dependencies

- `cppcheck` (2.13+ recommended)
- `grep`, `sed`, `sort`, `xargs`: bash script
- `python3`: Python fixer
- `bear`: optional, for `--project` support
