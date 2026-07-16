# cppcheck Uninitialized Member Detector

Find and auto-fix uninitialized member variables in C++ classes — handles `#if`/`#ifdef`/`#ifndef`/`#elif` preprocessor guards automatically.

## Quick Start

```bash
# 1. Find uninitialized members
./check_uninit_all.sh *.cpp

# 2. Auto-fix (in-class defaults = header int a_; → int a_ = 0;)
./cppcheck-fix-uninit.py *.cpp

# 3. Verify clean
./cppcheck-fix-uninit.py --report-only *.cpp

# Or all at once: find + fix + verify
./cppcheck-fix-uninit.py *.cpp && ./cppcheck-fix-uninit.py --report-only *.cpp

# For #if-heavy code, in-class mode handles guards automatically (default).
# For constructor init-lists instead: add --init-list
```


## The Problem

cppcheck finds most uninitialized members of primitive types (`int`, `double`, `float`, `bool`, `char*`, etc.), but it has two critical blind spots when the code uses preprocessor conditionals:

1. **Single-flag permutations only.** cppcheck's auto-config detection checks each undefined `#if` symbol independently. It **never** tries combinations — so `#if A && B` is never checked in the config where both are active.

2. **Default config limit.** cppcheck checks at most 12 configs by default. Codebases with many feature flags may have relevant configs skipped entirely.

Both scripts work around these limitations with a two-pass strategy.

## How It Works

### Two-pass cppcheck strategy

**Pass 1: Base config + `--max-configs=9999`**
Runs cppcheck with no extra defines, but with the config limit raised to 9999.

| Pattern | Covered? | Why |
|---|---|---|
| `#if A` | Yes | Single-permutation config checks A-on |
| `#ifdef A` | Yes | Same |
| `#ifndef A` | Yes | Base config has A off |
| `#if A && !B` | Yes | A-on config has B off → condition true |
| `#if A \|\| B` | Yes | A-on config makes it true |
| `#if A && B` | **No** | No single config has both A and B on |

**Pass 2: All extracted macros defined.**
Scans all source files for macro names (filtering out include guards and comment noise), then runs cppcheck with every macro explicitly defined (`-DMACRO=1`).

| Pattern | Covered? | Why |
|---|---|---|
| `#if A && B` | Yes | Both defined → condition true |
| `#if defined(A) && defined(B)` | Yes | Same |
| `#if A && B && C` | Yes | All three defined |
| `#if A && B && !C` | **No** | C is defined → `!C` is false |

### Compound condition detection

The bash script flags `#if` conditions with 3+ user macros for manual review:

```text
=== Compound conditions detected ===
  [3+ macros] config.h: #if FEATURE_A && FEATURE_B && !FEATURE_C
    Manual check needed: run with selective -D/-U flags for this condition
```

### Fix mode: in-class defaults (default) vs init-list (opt-in)

The Python fixer has two modes:

**Default: in-class initializers** — adds `= 0` / `= nullptr` / `= false` directly to the member declaration in the header:

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

**Opt-in: constructor init-list** — adds `: member_(0)` to each constructor in the `.cpp`:

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

Use `--init-list` for projects that prefer explicit per-constructor initialization over in-class defaults. Note that `#if`-guarded members are **skipped** in init-list mode — the C++ init-list comma syntax is position-dependent, so inserting a conditional entry unconditionally produces invalid code when the guard evaluates to false.

## Usage

### Find only (bash script)

```bash
# Scan .cpp files
./check_uninit_all.sh *.cpp

# With include paths
./check_uninit_all.sh -I /path/to/api *.cpp

# With additional flags passed through
./check_uninit_all.sh -I /path -j 8 --suppress=unusedFunction *.cpp
```

### Find + auto-fix (Python script)

```bash
# Default: add in-class =0 initializers to headers (handles #if guards)
./cppcheck-fix-uninit.py *.cpp
./cppcheck-fix-uninit.py -I /path/to/api *.cpp

# Verbose: show macros, cppcheck commands, member types with guards
./cppcheck-fix-uninit.py -v *.cpp

# Opt-in: constructor initializer lists instead (skips guarded members)
./cppcheck-fix-uninit.py --init-list *.cpp

# Preview only, do not modify
./cppcheck-fix-uninit.py --report-only *.cpp
./cppcheck-fix-uninit.py -v --report-only *.cpp
```

Creates `.bak` backup files before any modification.

**Important:** Always pass `.cpp` source files, not `.h` headers. cppcheck analyzes headers through the `.cpp` translation unit's `#include` directives. Passing only `.h` files produces zero findings — cppcheck cannot flag uninitialized members without seeing the constructors. Passing both `.cpp` and `.h` is harmless but redundant.

**Include paths:** If headers are in a different directory than the `.cpp` files, `-I` is required for **both** detection and fixing — the Python fixer needs `-I` to find the headers for type parsing and in-class default insertion. Without `-I`, type info will not be found and the fixer falls back to `{}` values. Use the same `-I` flags for the Python fixer as you use for the bash script.

### With compile_commands.json (best results)

```bash
bear -- ./build_script.sh
./cppcheck-fix-uninit.py --project=compile_commands.json
```

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
python3 test_elif_guards.py
make check
```

## Supported Member Types

All primitive types, pointers, references, and enums are detected **regardless of qualifiers** — `const`, `volatile`, `const volatile` — on the type, the pointer, or both. Fixed-width integer types from `<cstdint>` are detected too.

### Detected

| Category | Types | Default init value |
|---|---|---|
| **Plain integers** | `char`, `signed/unsigned char`, `short`, `unsigned short` | `0` / `'\0'` |
| | `int`, `unsigned`, `long`, `unsigned long` | `0` / `0L` |
| | `long long`, `unsigned long long` | `0LL` |
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

Types with their own default constructor — cppcheck considers them "initialized" even if forgotten:

| Type | Safe default |
|---|---|
| `std::string`, `std::vector<T>` | `{}` (empty) |
| `std::unique_ptr<T>`, `std::shared_ptr<T>` | `{}` (nullptr) |
| `std::optional<T>`, `std::variant<T...>` | `{}` (nullopt / first alt) |
| Any class/struct with a default ctor | cppcheck trusts it |

A forgotten `std::string name_` in the initializer list will **not** be flagged — the default constructor produces a valid empty state.

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
    // const double& m_ = ???  — skipped (reference)
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
    // RAII — not flagged
    std::string name_;
};
```

### How to fix (init-list, opt-in with `--init-list`)

```cpp
Foo::Foo()
    : a_(0), b_(0.0), c_(0.0f), d_(false), e_('\0')
    , f_(0), g_(0L), h_(0U), i_(nullptr), j_(nullptr)
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

### Python script — default (in-class) mode

```text
Phase 1: scanning for macros...
  found 6 user macro(s)
Phase 2: running cppcheck (pass 1 — base config)...
Phase 2: running cppcheck (pass 2 — all macros defined)...
  pass 2 added 13 error(s) from defined-macros config
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
      int cache_size_ → 0 [BUILD_PERFORMANCE]
      const char* host_ → nullptr
      int legacy_port_ → 0 [USE_LEGACY]
      int platform_id_ → 0 [(PLATFORM_A || PLATFORM_B)]
      int retries_ → 0
      float x_y_ratio_ → 0.0f [FEATURE_X && FEATURE_Y]
```

### Python script — init-list mode (`--init-list`)

```text
Phase 5: applying fixes...
  note: ServiceConfig has guarded members that won't be auto-fixed
  fixed 4 constructor(s) in uninit_guarded_test.cpp
  fixed 3 constructor(s) in uninit_test.cpp

Done. Fixed 7 constructor(s). Backups (*.bak) created.
```

Guarded members (`[BUILD_PERFORMANCE]`, etc.) appear in findings but are skipped during auto-fix.

## Limitations

### False negatives cppcheck cannot avoid

```text
#if A && B && !C
    int hidden_;  // needs -DA=1 -DB=1 -UC
#endif
```

The script flags 3+ macro compound conditions for manual review. Run cppcheck manually with the specific combination:

```bash
cppcheck -DA=1 -DB=1 -UC --enable=warning --inconclusive file.cpp
```

### cppcheck limitations

- Does not track `memset()`, placement `new`, or helper functions in the constructor body
- Marking a member as `= default` suppresses warnings
- Members with user-defined default constructors are never flagged

### Auto-fix limitations (`cppcheck-fix-uninit.py`)

- **Reference members** (`T&`, `const T&`) cannot be auto-fixed — no known binding target
- **Header parsing** is regex-based; complex types (nested templates, macros-as-types) fall back to `{}` (safe)
- **Preprocessor macros in member declarations** (`STATUS_FLAG flags_;`) are not parsed — fall back to `{}`
- **Init-list mode** (`--init-list`) skips `#if`-guarded members due to colon/comma syntax constraints. In-class mode (default) handles them correctly

## Workflow

1. **Generate test files** — `./generate_test.sh` (resets both test suites)
2. **Scan** — `./check_uninit_all.sh -I /api *.cpp` to see what's uninitialized
3. **Preview with guards** — `./cppcheck-fix-uninit.py -v --report-only *.cpp`
4. **Auto-fix** — `./cppcheck-fix-uninit.py *.cpp` (adds in-class defaults)
5. **Compile + test** — Verify it compiles and works
6. **Manual review** — Check `*.bak` files; handle reference members manually
7. **Repeat** — `./generate_test.sh` resets for another round

For best results with build flags:

```bash
bear -- ./build_script.sh
./cppcheck-fix-uninit.py --project=compile_commands.json
```

## Test files

| File | Uninit members | Features |
|---|---|---|
| `uninit_test.h/cpp` | 57 | All primitive/ptr/ref/volatile types, no guards |
| `uninit_guarded_test.h/cpp` | 15 | 6 feature flags, compound `&&`/`\|\|`/`!` guards |
| `test_elif_guards.py` | — | Unit test for `#elif`/`#else` guard chain tracking |

Regenerate with `./generate_test.sh`. Verify guard tracking with `make check` or `python3 test_elif_guards.py`.

## Dependencies

- `cppcheck` (2.13+ recommended)
- `grep`, `sed`, `sort`, `xargs` — bash script
- `python3` — Python fixer
- `bear` — optional, for `compile_commands.json`
