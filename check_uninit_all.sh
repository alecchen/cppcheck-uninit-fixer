#!/bin/bash
# check_uninit_all.sh — automatic cppcheck for uninitialized members
# Scans source for all #if/#ifdef/#ifndef macros, then runs cppcheck
# once per macro (each defined individually) to catch every guarded member.
#
# Usage:
#   ./check_uninit_all.sh file1.cpp file2.cpp
#   ./check_uninit_all.sh -I /path/to/api/headers *.cpp
#
# No manual -D flags needed — extracts them automatically.

set -eo pipefail

REPORT="uninit_report.txt"
CACHE_DIR=".cppcheck-cache"
ALL_SOURCES=()
CPPCHECK_ARGS=()

# Separate positional args into cppcheck flags and source files
for arg in "$@"; do
    case "$arg" in
        -I*|-i*|--suppress*|--std=*|-j*)
            CPPCHECK_ARGS+=("$arg") ;;
        -*)
            CPPCHECK_ARGS+=("$arg") ;;
        *)
            ALL_SOURCES+=("$arg") ;;
    esac
done

if [ ${#ALL_SOURCES[@]} -eq 0 ]; then
    echo "Usage: $0 [cppcheck-flags] file1.cpp [file2.cpp ...]"
    echo "       $0 -I /api/headers *.cpp"
    exit 1
fi

# ---------------------------------------------------------------
# Step 1: Extract all macros from #if / #ifdef / #ifndef / #elif
# ---------------------------------------------------------------
echo "=== Scanning for preprocessor macros ==="

# Known system/compiler macros to exclude
SYSTEM_MACROS='^(__.*|_.*__|WIN32|WIN64|_WIN32|_WIN64|_MSC_VER|__GNUC__|__clang__|__cplusplus|linux|__linux__|__APPLE__|__MACH__|__LP64__|__LP32__|__FILE__|__LINE__|__DATE__|__TIME__|__STDC__|__STDC_VERSION__|__CHAR_BIT__|__SIZEOF_INT__|__SIZEOF_POINTER__|true|false|TRUE|FALSE|NULL|NULLPTR|nullptr|std|size_t|ptrdiff_t|if|elif|ifdef|ifndef|defined|else|endif)$'

# Collect all source/header files from args
ALL_FILES_FILE=$(mktemp)
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"; rm -f "$ALL_FILES_FILE"' EXIT

for src in "${ALL_SOURCES[@]}"; do
    echo "$src" >> "$ALL_FILES_FILE"
    dir=$(dirname "$src")
    if [ -d "$dir" ]; then
        for hdr in "$dir"/*.h "$dir"/*.hpp; do
            [ -f "$hdr" ] || continue
            echo "$hdr"
        done >> "$ALL_FILES_FILE"
    fi
done
sort -u -o "$ALL_FILES_FILE" "$ALL_FILES_FILE"

# Extract macro names from #if/#ifdef/#ifndef/#elif lines
# Handles: #if FOO, #if defined(FOO), #ifdef FOO, #ifndef FOO,
#          #elif FOO, #elif defined(FOO), #if A && B, #if defined(A) && defined(B)
USER_MACROS=$(xargs grep -rhoE \
    -- '^[[:space:]]*#[[:space:]]*(if|elif)[[:space:]].*' \
    < "$ALL_FILES_FILE" 2>/dev/null | \
    sed -E \
        -e 's/defined[[:space:]]*\(([^)]+)\)/\1/g' \
        -e 's/![[:space:]]*([A-Za-z_][A-Za-z0-9_]*)/\1/g' \
        -e 's/&&/ /g' -e 's/\|\|/ /g' \
        -e 's/[^A-Za-z_0-9]/ /g' | \
    grep -oE '\b[A-Za-z_][A-Za-z0-9_]*\b' | \
    sort -u | \
    grep -vE "$SYSTEM_MACROS" || true)

# Also extract from #ifdef/#ifndef lines
USER_MACROS2=$(xargs grep -rhoE \
    -- '^[[:space:]]*#[[:space:]]*(ifdef|ifndef)[[:space:]].*' \
    < "$ALL_FILES_FILE" 2>/dev/null | \
    sed -E 's/^[[:space:]]*#[[:space:]]*(ifdef|ifndef)[[:space:]]*//' | \
    grep -oE '\b[A-Za-z_][A-Za-z0-9_]*\b' | \
    sort -u | \
    grep -vE "$SYSTEM_MACROS" || true)

ALL_MACROS=$(printf '%s\n' "$USER_MACROS" "$USER_MACROS2" | sort -u | grep -v '^$' || true)

MACRO_COUNT=$(echo "$ALL_MACROS" | grep -c . || true)
echo "Found $MACRO_COUNT user macros in preprocessor conditionals"
if [ "$MACRO_COUNT" -gt 0 ]; then
    echo "$ALL_MACROS"
fi

# ---------------------------------------------------------------
# Step 2: Verify --max-configs covers all macro combinations
# ---------------------------------------------------------------
# Parse current max-configs from cppcheck args (default 9999 if not set)
MAX_CONFIGS=9999
for arg in "${CPPCHECK_ARGS[@]}"; do
    if [[ "$arg" =~ --max-configs=([0-9]+) ]]; then
        MAX_CONFIGS="${BASH_REMATCH[1]}"
    fi
done

# Each user macro is a potential config variable in cppcheck.
# To be exhaustive, cppcheck needs 2^N configs per file.
# Warn and stop if the current limit is too low.
if [ "$MACRO_COUNT" -gt 0 ]; then
    NEEDED=$(( 1 << MACRO_COUNT ))   # 2^N
    if [ "$NEEDED" -gt "$MAX_CONFIGS" ]; then
        echo ""
        echo "ERROR: --max-configs=$MAX_CONFIGS is too low for $MACRO_COUNT macros."
        echo "  Worst-case configs needed: 2^$MACRO_COUNT = $NEEDED"
        echo "  Rerun with: --max-configs=$NEEDED"
        exit 1
    fi
    echo "Max configs check: 2^$MACRO_COUNT = $NEEDED <= $MAX_CONFIGS (OK)"
fi

# ---------------------------------------------------------------
# Step 3: Run cppcheck passes
# ---------------------------------------------------------------
mkdir -p "$CACHE_DIR"

COMMON_FLAGS="--enable=warning --inconclusive --max-configs=$MAX_CONFIGS \
              --check-level=exhaustive \
              --suppress=missingIncludeSystem \
              --suppress=unmatchedSuppression \
              --suppress=checkersReport \
              --suppress=unusedFunction \
              --cppcheck-build-dir=$CACHE_DIR"

echo ""
echo "=== Pass 1: Base config (no extra defines) ==="
cppcheck $COMMON_FLAGS "${CPPCHECK_ARGS[@]}" \
    "${ALL_SOURCES[@]}" 2>&1 | \
    grep -E '\[uninitMemberVar\]|\[uninitStructMember\]|\[uninitData\]' \
    > "$TEMP_DIR/pass1.txt" || true
echo "  $(wc -l < "$TEMP_DIR/pass1.txt") findings"

# Pass 2+: One pass per user macro.  Each pass defines exactly one macro.
# Catches every member guarded by #ifdef MACRO / #ifndef MACRO / #if MACRO.
# The --cppcheck-build-dir cache avoids re-analyzing unchanged code across
# passes — each subsequent run only rechecks the affected code paths.
PASS_NUM=1
for m in $ALL_MACROS; do
    PASS_NUM=$((PASS_NUM + 1))
    echo ""
    echo "=== Pass $PASS_NUM: -D$m=1 ==="
    cppcheck $COMMON_FLAGS "${CPPCHECK_ARGS[@]}" \
        -D"$m"=1 \
        "${ALL_SOURCES[@]}" 2>&1 | \
        grep -E '\[uninitMemberVar\]|\[uninitStructMember\]|\[uninitData\]' \
        > "$TEMP_DIR/pass${PASS_NUM}.txt" || true
    echo "  $(wc -l < "$TEMP_DIR/pass${PASS_NUM}.txt") findings"
done

# ---------------------------------------------------------------
# Step 4: Merge and deduplicate findings
# ---------------------------------------------------------------
cat "$TEMP_DIR"/pass*.txt 2>/dev/null | sort -u > "$REPORT"

FINDING_COUNT=$(wc -l < "$REPORT")
echo ""
echo "========================================"
echo "Uninitialized member findings: $FINDING_COUNT"
echo "Report: $REPORT"
echo "========================================"
if [ "$FINDING_COUNT" -gt 0 ]; then
    cat "$REPORT"
fi

# ---------------------------------------------------------------
# Step 5: Detect compound #if conditions that may need more combos
# ---------------------------------------------------------------
echo ""
echo "=== Compound conditions detected ==="
while IFS= read -r src; do
    [ -f "$src" ] || continue
    grep -E '^\s*#\s*(if|elif)\s+.*\&\&.*\&\&' "$src" 2>/dev/null | \
        grep -oE '#(if|elif)\s+.*' | \
        while read -r line; do
            # Count user macros in this condition
            count=0
            for m in $ALL_MACROS; do
                if echo "$line" | grep -qE '\b'"$m"'\b'; then
                    count=$((count + 1))
                fi
            done
            if [ "$count" -ge 3 ]; then
                echo "  [3+ macros] $src: $line"
                echo "    Manual check needed: run with selective -D/-U flags for this condition"
            fi
        done 2>/dev/null || true
done < "$ALL_FILES_FILE"
echo "  (done)"
