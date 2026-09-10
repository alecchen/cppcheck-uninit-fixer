#!/usr/bin/env python3
"""
cppcheck-fix-uninit.py -- find and auto-fix uninitialized member variables.

Usage:
    ./cppcheck-fix-uninit.py file1.cpp file2.cpp
    ./cppcheck-fix-uninit.py -I /api/headers *.cpp
    ./cppcheck-fix-uninit.py --report-only *.cpp
    ./cppcheck-fix-uninit.py --project=compile_commands.json

Finds uninitialized members using cppcheck (base pass + one pass per macro),
then inserts missing initializations into constructors. Creates .bak backups.

Supports: all primitives, pointers, references, fixed-width integers,
const/volatile qualified types, pointer qualifier variants.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

# ---------------------------------------------------------------------------
# Run cppcheck
# ---------------------------------------------------------------------------


CPPCHECK_SUPPRESS = [
    "--suppress=missingIncludeSystem", "--suppress=unmatchedSuppression",
    "--suppress=checkersReport", "--suppress=unusedFunction",
]

CACHE_DIR = ".cppcheck-cache"


def cppcheck_cmd(sources, defines=None, project=None, max_configs=9999):
    """Build the cppcheck argv. Shared by the runner and verbose output."""
    cmd = [
        "cppcheck", "--quiet", "--xml", "--enable=warning", "--inconclusive",
        f"--max-configs={max_configs}", "--check-level=exhaustive",
        "-j", str(os.cpu_count() or 4),
        f"--cppcheck-build-dir={CACHE_DIR}",
    ] + CPPCHECK_SUPPRESS
    if defines:
        cmd.extend(defines)
    if project:
        cmd.append(f'--project={project}')
    else:
        cmd.extend(sources)
    return cmd


def cppcheck_xml(sources, defines=None, project=None, max_configs=9999):
    """Run cppcheck with --xml, return parsed ElementTree root.

    Pass sources (for header/macro scanning) and optionally project
    (compile_commands.json path) for cppcheck's own analysis.
    When project is set, cppcheck gets --project=<path> instead of sources.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    cmd = cppcheck_cmd(sources, defines, project, max_configs)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # cppcheck writes XML to stderr, progress to stdout
    try:
        return ET.fromstring(proc.stderr)
    except ET.ParseError as e:
        print(f"cppcheck XML parse error: {e}", file=sys.stderr)
        print("stderr excerpt:", proc.stderr[:500], file=sys.stderr)
        sys.exit(1)


def extract_macros(sources):
    """Scan source files for user macros in #if/#ifdef/#ifndef/#elif."""
    sys_macros = {'if','elif','ifdef','ifndef','defined','else','endif',
                  'true','false','TRUE','FALSE','NULL','NULLPTR','nullptr',
                  'std','size_t','ptrdiff_t'}
    macros = set()

    for src in sources:
        if not os.path.isfile(src):
            continue
        with open(src, errors='replace') as f:
            text = f.read()

        # Strip comments to avoid matching #if inside // comments
        text = re.sub(r'//.*', '', text)
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)

        # Collect #define names (to filter out include guards)
        defines = set()
        for dm in re.finditer(r'#\s*define\s+(\w+)', text, re.MULTILINE):
            defines.add(dm.group(1))

        for m in re.finditer(r'#\s*(?:if|elif|ifdef|ifndef)\b.*', text, re.MULTILINE):
            line = re.sub(r'#[a-z]+\s*', '', m.group())
            line = re.sub(r'defined\s*\((\w+)\)', r'\1', line)
            for tok in re.findall(r'\b([A-Za-z_]\w*)\b', line):
                if (not tok.startswith('__') and not (tok.startswith('_') and tok.endswith('_'))
                        and tok not in sys_macros
                        and tok not in defines):   # skip include guards
                    try:
                        int(tok)
                    except ValueError:
                        macros.add(tok)
    return sorted(macros)


# ---------------------------------------------------------------------------
# Parse compilation database
# ---------------------------------------------------------------------------


def parse_compilation_db(path):
    """Extract source file paths from a compile_commands.json."""
    with open(path) as f:
        entries = json.load(f)
    sources = []
    for e in entries:
        src = e.get('file', '')
        if not src:
            continue
        if not os.path.isabs(src):
            src = os.path.join(e.get('directory', ''), src)
        sources.append(os.path.normpath(src))
    return sorted(set(s for s in sources if s.endswith(('.cpp', '.c', '.cc', '.cxx'))))


# ---------------------------------------------------------------------------
# Parse cppcheck XML
# ---------------------------------------------------------------------------


def parse_findings(xml_root):
    """Extract (file, line, class_name, member_name) from uninitMemberVar errors."""
    findings = []
    for err in xml_root.findall('.//error'):
        if err.get('id') not in ('uninitMemberVar', 'uninitStructMember', 'uninitData'):
            continue
        sym = err.find('symbol')
        if sym is None or '::' not in (sym.text or ''):
            continue
        cls, member = sym.text.split('::', 1)
        loc = err.find('location')
        if loc is None:
            continue
        try:
            findings.append((
                os.path.abspath(loc.get('file', '')),
                int(loc.get('line', 0)),
                cls,
                member,
            ))
        except (ValueError, TypeError):
            pass
    # Deduplicate
    return list(set(findings))


# ---------------------------------------------------------------------------
# Find header files
# ---------------------------------------------------------------------------


def find_headers(sources, include_dirs=None, with_sources=False):
    """Collect header files to parse for member declarations.

    Header files passed directly are included as-is; directories are
    scanned non-recursively. With with_sources=True (fast mode), .cpp
    files are collected too, so classes defined in a translation unit
    (internal classes, pimpl) are seen. cppcheck mode leaves it False:
    it analyzes .cpp files itself.
    """
    headers = set()
    dirs = set()
    hdr_exts = ('.h', '.hpp', '.hxx')
    src_exts = ('.cpp', '.c', '.cc', '.cxx')
    wanted = hdr_exts + src_exts if with_sources else hdr_exts

    for src in sources:
        a = os.path.abspath(src)
        if os.path.isfile(a) and a.endswith(wanted):
            headers.add(a)
            # Fall through: a .cpp input still needs its co-located
            # headers scanned, so do not continue here.
        if os.path.isdir(a):
            dirs.add(a)
            continue
        d = os.path.dirname(a)
        if d:
            dirs.add(d)
    if include_dirs:
        dirs.update(include_dirs)
    for d in dirs:
        if not os.path.isdir(d):
            continue
        try:
            for fn in os.listdir(d):
                if fn.endswith(wanted):
                    headers.add(os.path.abspath(os.path.join(d, fn)))
        except PermissionError:
            continue
    return sorted(headers)


# ---------------------------------------------------------------------------
# Parse member types from headers
# ---------------------------------------------------------------------------


_DECL_KEYWORDS = {'public', 'private', 'protected', 'class', 'struct',
                  'const', 'volatile', 'static', 'mutable', 'using', 'friend'}


def _split_declarators(body):
    """Split a declaration body on top-level commas (ignore <>[]{})."""
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch in '<[{':
            depth += 1
        elif ch in '>]}':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(''.join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append(''.join(cur))
    return parts


def _parse_decl_line(s):
    """Parse one member-declaration line into [(type, name)].

    Handles comma-separated multi-declarations: `int a_, b_;`
    Skips arrays, bitfields, and keyword declarators.
    """
    body = s[:-1] if s.endswith(';') else s
    decls = _split_declarators(body)
    m0 = re.match(r'^(.*?)\b(\w+)\s*(=|$)', decls[0])
    if not m0:
        return []
    t0, base = m0.group(1).strip(), None
    base = re.sub(r'[*&]', ' ', t0).strip()
    out = []
    for k, d in enumerate(decls):
        d_no_init = d.split('=', 1)[0]
        if k == 0:
            t, v = t0, m0.group(2)
        else:
            if ':' in d_no_init:
                continue  # bitfield
            m3 = re.match(r'^\s*(.*?)\b(\w+)(\s*\[.*\])?\s*$', d_no_init)
            if not m3:
                continue
            if m3.group(3):
                continue  # array
            v, t = m3.group(2), (base + ' ' + m3.group(1)).strip()
        if v in _DECL_KEYWORDS:
            continue
        if t.startswith(('public:', 'private:', 'protected:')):
            continue
        out.append((t, v))
    return out


def _needs_default(line, mn):
    """True if line declares mn without an in-class initializer."""
    m = re.search(r'\b' + re.escape(mn) + r'\b', line)
    if not m:
        return False
    rest = line[m.end():].lstrip()
    if rest.startswith('='):
        return False
    return rest.startswith(',') or rest.startswith(';')


def _insert_default(line, mn, dv):
    """Insert ` = dv` after mn's declaration. None if already init."""
    m = re.search(r'\b' + re.escape(mn) + r'\b', line)
    if not m:
        return None
    if line[m.end():].lstrip().startswith('='):
        return None
    return line[:m.end()] + f" = {dv}" + line[m.end():]


def parse_member_types(headers):
    """Extract {class_name: {member_name: {'type': str, 'guard': str|None}}}.

    'guard' is None for members not behind #if/#ifdef/#ifndef,
    or the full #if expression as a string for guarded members.
    """
    result = defaultdict(dict)

    for hdr in headers:
        if not os.path.isfile(hdr):
            continue
        try:
            with open(hdr, errors='replace') as f:
                text = f.read()
        except Exception:
            continue

        # Remove string literals and comments
        text = re.sub(r'"[^"\\\\]*(?:\\\\.[^"\\\\]*)*"', '""', text)
        text = re.sub(r'//.*', '', text)
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)

        for cm in re.finditer(r'(?:class|struct)\s+(\w+)\s*(?::[^{]*)?\{', text):
            cls_name = cm.group(1)
            start, depth = cm.end(), 1
            end = start
            while depth > 0 and end < len(text):
                if text[end] == '{': depth += 1
                elif text[end] == '}': depth -= 1
                end += 1
            body = text[start:end - 1]

            # Track preprocessor guard chains while scanning lines.
            # Each stack entry is a list of conditions in the current
            # #if/#elif/#else chain at that nesting level.  For example:
            #   #if A          -> push ["A"]
            #   #elif B        -> append -> ["A", "B"]
            #     int x;       -> active = "!A && B"
            #   #else          -> append None -> ["A", "B", None]
            #     int y;       -> active = "!A && !B"
            #   #endif         -> pop
            guard_chain = []  # list of lists: chain per nesting level
            brace_depth = 0   # >0 means inside a nested class/struct body

            for raw_line in body.split('\n'):
                s = raw_line.strip()

                # Track preprocessor directives
                if s.startswith('#ifdef '):
                    guard_chain.append([f"defined({s[7:].strip()})"])
                    continue
                if s.startswith('#ifndef '):
                    guard_chain.append([f"!defined({s[7:].strip()})"])
                    continue
                if s.startswith('#if '):
                    guard_chain.append([s[4:].strip()])
                    continue
                if s.startswith('#elif '):
                    if guard_chain:
                        guard_chain[-1].append(s[6:].strip())
                    continue
                if s == '#else':
                    if guard_chain:
                        guard_chain[-1].append(None)  # None marks else branch
                    continue
                if s.startswith('#endif'):
                    if guard_chain:
                        guard_chain.pop()
                    continue

                # Members of a nested class belong to that class, not this
                # one. Skip anything inside a nested body, tracking depth so
                # a nested class's own members are still recorded when the
                # outer scan reaches it.
                if brace_depth > 0:
                    brace_depth += s.count('{') - s.count('}')
                    continue

                # Skip non-declaration lines
                if (not s or s.startswith(('//','#','/*','*','public','private','protected',
                    'typedef','using ','friend','static_assert','enum','template',
                    'explicit','virtual','operator','constexpr','noexcept',
                    'override','final','default','delete','struct','class'))):
                    # A nested class/struct opens a body we must skip over.
                    if re.match(r'^(class|struct)\b.*\{\s*$', s):
                        brace_depth = 1
                    continue
                if '(' in s or ')' in s:
                    continue

                for t, v in _parse_decl_line(s):
                    # Build the guard expression from the chain stack
                    if guard_chain:
                        parts = []
                        for chain in guard_chain:
                            if len(chain) == 1 and chain[0] is not None:
                                # Simple #if
                                active = chain[0]
                            elif chain[-1] is None:
                                # #else branch: negate all conditions in the chain
                                negated = ' && '.join(
                                    f'!({c})' if '||' in c else f'!{c}'
                                    for c in chain[:-1]
                                )
                                active = negated
                            else:
                                # #elif branch: negate all prior, keep last
                                prior = chain[:-1]
                                negated = ' && '.join(
                                    f'!({c})' if '||' in c else f'!{c}'
                                    for c in prior
                                )
                                active = f'{negated} && {chain[-1]}'
                            parts.append(f'({active})' if '||' in active else active)
                        guard = ' && '.join(parts)
                    else:
                        guard = None

                    result[cls_name][v] = {'type': t, 'guard': guard}

    return result


# ---------------------------------------------------------------------------
# Default value by type
# ---------------------------------------------------------------------------

VERBOSE = False


def default_init(type_info):
    """Return a C++ default-value expression for a type, or None if not possible.

    type_info can be:
      - a dict with 'type' key (new format from parse_member_types)
      - a plain string (old format, for backward compat)
    """
    if isinstance(type_info, dict):
        type_str = type_info.get('type', '')
    else:
        type_str = type_info or ''
    if not type_str:
        return '{}'
    t = type_str.strip().lower()

    # Reference -- no safe default
    if '&' in type_str:
        return None

    # Pointer
    if '*' in type_str:
        return 'nullptr'

    # Bool
    if t in ('_bool', 'bool'):
        return 'false'

    # Float
    if t == 'float':
        return '0.0f'
    if t in ('double', 'long double'):
        return '0.0'

    # Char
    if t in ('char', 'signed char', 'unsigned char', 'wchar_t',
             'char8_t', 'char16_t', 'char32_t'):
        return "'\\0'"

    # Everything else -> 0 (works for int, long, short, fixed-width, enum, etc.)
    return '0'


_POD_WORDS = {
    'bool', 'char', 'wchar_t', 'char8_t', 'char16_t', 'char32_t',
    'short', 'int', 'long', 'float', 'double', 'signed', 'unsigned',
    'const', 'volatile',
    'size_t', 'ptrdiff_t',
    'int8_t', 'int16_t', 'int32_t', 'int64_t',
    'uint8_t', 'uint16_t', 'uint32_t', 'uint64_t',
    'intptr_t', 'uintptr_t', 'intmax_t', 'uintmax_t',
}


def default_init_strict(type_info):
    """Like default_init, but None for unknown (non-POD) types.

    Fast mode has no cppcheck findings to limit scope, so class types
    like std::string (which self-initialize) must be skipped instead of
    getting a bogus `= 0`. Also skips statics (in-class init is an
    error for non-const statics pre-C++17).
    """
    if isinstance(type_info, dict):
        type_str = type_info.get('type', '')
    else:
        type_str = type_info or ''
    if not type_str:
        return None
    if '&' in type_str:
        return None
    if '*' in type_str:
        return 'nullptr'
    words = re.findall(r'[A-Za-z_]\w*', type_str.lower())
    if not words or any(w not in _POD_WORDS for w in words):
        return None
    return default_init(type_str)


def dump_member_types(member_types, strict=False):
    """Print parsed members with guards and defaults (verbose output)."""
    for cls, members in sorted(member_types.items()):
        print(f"    class {cls}:", file=sys.stderr)
        for mname, minfo in sorted(members.items()):
            if isinstance(minfo, dict):
                type_str = minfo.get('type', '?')
                guard = minfo.get('guard')
            else:
                type_str = str(minfo)
                guard = None
            dv = default_init_strict(minfo) if strict else default_init(minfo)
            dv = dv or "(skipped)"
            g = f" [{guard}]" if guard else ""
            print(f"      {type_str} {mname} -> {dv}{g}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Fix constructors
# ---------------------------------------------------------------------------


def fix_file(filepath, grouped, member_types):
    """Insert missing member initializations. Returns (#constructors-fixed, [errors])."""
    if not os.path.isfile(filepath):
        return 0, [f"not found: {filepath}"]

    with open(filepath) as f:
        lines = f.readlines()

    # Group by constructor line
    by_line = defaultdict(list)   # line -> [(class_name, member_name)]
    for cls, member, line in grouped:
        by_line[line].append((cls, member))

    fixes = []
    for const_line in sorted(by_line, reverse=True):
        members = by_line[const_line]
        cls = members[0][0]
        mnames = sorted(set(m[1] for m in members))
        try:
            new_lines = insert(lines, const_line - 1, cls, mnames, member_types)
            if new_lines is not None:
                if VERBOSE:
                    print(f"    line {const_line}: {cls}() +{', '.join(mnames)}", file=sys.stderr)
                lines = new_lines
                fixes.append(const_line)
        except Exception as e:
            print(f"  Error at {filepath}:{const_line}: {e}", file=sys.stderr)

    if fixes:
        with open(filepath + '.bak', 'w') as f:
            f.writelines(lines)
        # Actually write the fixed version
        with open(filepath, 'w') as f:
            f.writelines(lines)
        return len(fixes), []

    return 0, []


def fix_in_class(header_files, findings, member_types):
    """Add in-class default initializers to header member declarations.

    Instead of modifying constructor initializer lists (which must be done
    per-constructor), adds `= default` to each member declaration in the header:

        int a_;  ->  int a_ = 0;

    This works correctly with #if-guarded members since the initializer
    lives in the same #if block as the declaration. Returns count of
    members fixed.
    """
    # Collect all unique (class, member) pairs from findings
    need_fix = set()  # (class, member)
    for fp, line, cls, member in findings:
        need_fix.add((cls, member))

    # Group by header file
    by_header = defaultdict(set)  # header -> set of (class, member)
    for hdr in header_files:
        abspath = os.path.abspath(hdr)
        for cls, member in need_fix:
            by_header[abspath].add((cls, member))

    fixed = 0
    for hdr, cls_members in sorted(by_header.items()):
        if not os.path.isfile(hdr):
            continue
        try:
            with open(hdr) as f:
                lines = f.readlines()
        except Exception:
            continue

        any_change = False
        cls_member_names = set(m[1] for m in cls_members)
        for i, line in enumerate(lines):
            s = line.strip()
            # Skip comments, preprocessor, access specifiers, functions
            if (not s or s.startswith(('//', '#', '/*', '*', 'public', 'private',
                                        'protected', 'typedef', 'using ', 'friend',
                                        'return', '}', '{'))):
                continue
            # Skip function declarations
            if '(' in s or ')' in s:
                continue

            # Try to find member declarations we need to fix
            # (a line may declare several: `int a_, b_;`)
            orig = line
            for mn in sorted(cls_member_names):
                if mn not in s:
                    continue
                if not _needs_default(s, mn):
                    continue

                # Find the type from member_types
                cls_name = None
                for cm in cls_members:
                    if cm[1] == mn:
                        cls_name = cm[0]
                        break
                if not cls_name:
                    continue
                info = member_types.get(cls_name, {}).get(mn)
                dv = default_init(info)
                if dv is None:
                    continue  # skip references

                new_line = _insert_default(line, mn, dv)
                if new_line is None:
                    continue
                line = new_line
                s = line.strip()
                if VERBOSE:
                    print(f"    {os.path.basename(hdr)}: {mn} = {dv}", file=sys.stderr)
                fixed += 1
            if line != orig:
                lines[i] = line
                any_change = True

        if any_change:
            with open(hdr + '.bak', 'w') as f:
                f.writelines(lines)
            with open(hdr, 'w') as f:
                f.writelines(lines)

    return fixed


def find_uninit_candidates(header_files, member_types):
    """List (hdr, line_idx, class, member, default) for members lacking init.

    Fast-mode helper: no cppcheck findings, so every parsed POD/pointer
    member without an in-class initializer is a candidate. Skips
    references, statics, class/enum types, and arrays (strict defaults).
    """
    cands = []
    for hdr in sorted(set(os.path.abspath(h) for h in header_files)):
        if not os.path.isfile(hdr):
            continue
        try:
            with open(hdr) as f:
                lines = f.readlines()
        except Exception:
            continue
        seen = set()  # (class, member) already reported in this header
        for i, line in enumerate(lines):
            s = line.strip()
            if (not s or s.startswith(('//', '#', '/*', '*', 'public', 'private',
                                        'protected', 'typedef', 'using ', 'friend',
                                        'return', '}', '{'))):
                continue
            if '(' in s or ')' in s:
                continue
            for cls in sorted(member_types):
                for mn, info in sorted(member_types[cls].items()):
                    if (cls, mn) in seen or mn not in s:
                        continue
                    if not _needs_default(s, mn):
                        continue
                    dv = default_init_strict(info)
                    if dv is None:
                        continue
                    cands.append((hdr, i, cls, mn, dv))
                    seen.add((cls, mn))
    return cands


def fix_all_in_class(header_files, member_types):
    """Fast mode: add in-class defaults to every uninit POD/pointer member.

    Skips cppcheck entirely. Returns count of members fixed.
    """
    cands = find_uninit_candidates(header_files, member_types)
    by_hdr = defaultdict(list)
    for hdr, i, cls, mn, dv in cands:
        by_hdr[hdr].append((i, mn, dv))

    fixed = 0
    for hdr, items in sorted(by_hdr.items()):
        with open(hdr) as f:
            lines = f.readlines()
        # Group members sharing a line (`int a_, b_;`); insert right to
        # left so earlier offsets stay valid.
        per_line = defaultdict(list)
        for i, mn, dv in items:
            per_line[i].append((mn, dv))
        for i in sorted(per_line):
            for mn, dv in sorted(per_line[i], key=lambda p: lines[i].find(p[0]),
                                 reverse=True):
                new_line = _insert_default(lines[i], mn, dv)
                if new_line is None or new_line == lines[i]:
                    continue
                lines[i] = new_line
                if VERBOSE:
                    print(f"    {os.path.basename(hdr)}: {mn} = {dv}", file=sys.stderr)
                fixed += 1
        with open(hdr + '.bak', 'w') as f:
            f.writelines(lines)
        with open(hdr, 'w') as f:
            f.writelines(lines)
    return fixed


def _build_init_lines(entries, append):
    """Build formatted init-list lines.

    entries: list of (member_name, default_value)
    append: True when appending to existing init list, False for new list.

    Returns list of strings (each with trailing \n).
    """
    result = []
    for k, (mn, dv) in enumerate(entries):
        if k == 0 and not append:
            result.append(f"    : {mn}({dv})\n")
        else:
            result.append(f"    , {mn}({dv})\n")
    return result


def insert(lines, start_0idx, class_name, member_names, member_types):
    """Find constructor at start_0idx and add missing init entries.

    Returns modified lines list, or None if nothing to add.
    """
    cls_types = member_types.get(class_name, {})
    entries = []
    skipped_guarded = False
    for mn in member_names:
        info = cls_types.get(mn) or {}
        d = default_init(info)
        if d is None:
            continue  # skip references
        # Get guard condition (None if unconditional, str like "USE_LEGACY")
        guard = info.get('guard') if isinstance(info, dict) else None
        if guard:
            # Cannot auto-fix guarded members -- init-list comma depends on
            # which #if blocks are active, which we don't know at fix time.
            skipped_guarded = True
            continue
        entries.append((mn, d))
    if skipped_guarded and VERBOSE:
        print(f"    note: {class_name} has guarded members that won't be auto-fixed", file=sys.stderr)
        return None

    # Scan forward from constructor line to find body brace
    depth = 0
    i = start_0idx
    while i < len(lines):
        for ch in lines[i]:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
        s = lines[i].strip()
        if s.startswith('#') or s.startswith('//') or s == '':
            i += 1
            continue
        if depth < 0:
            depth = 0
        brace_pos = lines[i].find('{')
        if brace_pos >= 0 and depth == 0:
            break
        i += 1
    else:
        return None
    brace_line_idx = i
    brace_text = lines[i]
    brace_col = brace_text.index('{')

    # Determine if init list exists by scanning combined signature
    sig_list = []
    for j in range(start_0idx, brace_line_idx + 1):
        sig_list.append(lines[j].rstrip())
    sig = ''.join(sig_list)
    # Find the constructor's closing paren (first ) that returns to depth 0)
    lp = -1
    depth = 0
    for ci, ch in enumerate(sig):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                lp = ci
                break
    if lp < 0:
        return None
    after = sig[lp + 1:]
    colon = -1
    for ci in range(len(after)):
        if after[ci] == ':' and ci + 1 < len(after) and after[ci + 1] != ':':
            if '?' not in after[:ci].rsplit('(', 1)[-1]:
                colon = ci
                break

    # --- Build init entries ---
    if colon >= 0:
        # append to existing init list
        lines_to_add = _build_init_lines(entries, append=True)
    else:
        # new init list
        lines_to_add = _build_init_lines(entries, append=False)

    if colon >= 0:
        # Existing init list: find where to insert (before the brace)
        # Find the last non-blank line before the brace
        last_entry = brace_line_idx - 1
        while last_entry > start_0idx and not lines[last_entry].strip():
            last_entry -= 1
        # Do NOT add trailing comma -- new entries already have `, ` prefix
        if not lines[brace_line_idx].strip().lstrip().startswith('{'):
            # Brace shares a line with something else -- split it out
            # The brace is the last thing on the line
            before_brace = lines[brace_line_idx][:brace_col]
            lines[brace_line_idx] = before_brace.rstrip() + '\n'
            # Insert brace on its own line + new entries before it
            # We can't easily splice before an existing line, so:
            # Remove brace text, add entries, re-add brace on its own line
            brace_ws = ' ' * (len(lines[brace_line_idx]) - len(lines[brace_line_idx].lstrip()))
            lines[brace_line_idx] = ''
            # Insert at the brace position
            if last_entry >= start_0idx and lines[last_entry].strip():
                for k, il in enumerate(lines_to_add):
                    lines.insert(last_entry + k + 1, il)
            else:
                # fallback: insert before brace
                for k, il in enumerate(lines_to_add):
                    lines.insert(brace_line_idx + k, il)
                lines.insert(brace_line_idx + len(lines_to_add),
                             f"{brace_ws}{{\n")
        else:
            # Brace on its own line -- insert new entries just before it
            for k, il in enumerate(lines_to_add):
                lines.insert(brace_line_idx + k, il)
    else:
        # No init list: insert one before brace
        # Determine indentation
        brace_ws = ' ' * (len(lines[brace_line_idx]) - len(lines[brace_line_idx].lstrip()))
        brace_on_own_line = lines[brace_line_idx].strip() == '{'
        if not brace_on_own_line:
            # Brace shares line with signature/member -- move it
            before_brace = lines[brace_line_idx][:brace_col]
            lines[brace_line_idx] = before_brace.rstrip() + '\n'
            for k, il in enumerate(lines_to_add):
                lines.insert(brace_line_idx + k, il)
            lines.insert(brace_line_idx + len(lines_to_add), f"{brace_ws}{{\n")
        else:
            for k, il in enumerate(lines_to_add):
                lines.insert(brace_line_idx + k, il)

    return lines


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(
        description='Find and auto-fix uninitialized C++ member variables.')
    ap.add_argument('sources', nargs='*', default=[],
                    help='Source files (.cpp) or, with --fast, also headers '
                         '(.h/.hpp) and directories. May be omitted when '
                         '--project is given.')
    ap.add_argument('--report-only', action='store_true',
                    help='Only print findings, do not modify')
    ap.add_argument('-v', '--verbose', action='store_true',
                    help='Show detailed progress: found macros, cppcheck commands, '
                         'member types with guards, fix details')
    ap.add_argument('--init-list', action='store_true',
                    help='Fix via constructor initializer lists instead of in-class '
                         'defaults. Adds member_(0) to each constructor (default: '
                         'adds = 0 to header declarations). Skipped for #if-guarded '
                         'members due to init-list comma syntax limitations.')
    ap.add_argument('-I', dest='includes', action='append', default=[],
                    help='Include path')
    ap.add_argument('-D', dest='defines', action='append', default=[],
                    help='Define macro')
    ap.add_argument('--project', dest='project', default=None,
                    help='Path to compile_commands.json for build-aware analysis. '
                         'When set, cppcheck uses the compilation database instead '
                         'of requiring -I and -D flags. Source files from the DB are '
                         'also used for header/macro scanning.')
    ap.add_argument('--max-configs', dest='max_configs', type=int, default=9999,
                    help='Maximum cppcheck configs per file (default: 9999). '
                         'Must be >= 2^N where N is the number of extracted macros.')
    ap.add_argument('--fast', action='store_true',
                    help='Skip cppcheck entirely: add in-class defaults to every '
                         'parsed POD/pointer member without an initializer. '
                         'Instant, no cppcheck time or memory. Class/enum/ '
                         'typedef members are skipped; members already '
                         'initialized in constructors still get (redundant but '
                         'harmless) defaults.')
    args = ap.parse_args()

    global VERBOSE
    VERBOSE = args.verbose

    raw_inputs = [s for s in args.sources
                  if os.path.isfile(s) or os.path.isdir(s)]
    if args.fast:
        # Fast mode fixes headers directly: keep header/dir inputs too.
        sources = [os.path.abspath(s) for s in raw_inputs]
    else:
        sources = [os.path.abspath(s) for s in raw_inputs
                   if s.endswith(('.cpp', '.c', '.cc', '.cxx'))]

    # When --project is given, extract source files from the compilation DB
    project_sources = []
    if args.project:
        project_path = os.path.abspath(args.project)
        if not os.path.isfile(project_path):
            print(f"Error: --project file not found: {project_path}", file=sys.stderr)
            sys.exit(1)
        project_sources = parse_compilation_db(project_path)
        if not project_sources:
            print("Error: no source files found in compile_commands.json", file=sys.stderr)
            sys.exit(1)
        if VERBOSE:
            print(f"Found {len(project_sources)} source(s) in compilation database",
                  file=sys.stderr)
            for s in project_sources:
                print(f"  {os.path.basename(s)}", file=sys.stderr)

    # Merge project sources with positional sources
    all_sources = sources + project_sources
    if not all_sources:
        print("Error: no source files found. Pass .cpp files or use --project.",
              file=sys.stderr)
        sys.exit(1)

    # ---- Fast mode: skip cppcheck, default every parsed member ----
    # Fast mode parses .cpp files too, so classes defined in a translation
    # unit (internal classes, pimpl) are included.
    headers = find_headers(all_sources, args.includes, with_sources=args.fast)
    member_types = parse_member_types(headers)
    if args.fast:
        total = sum(len(v) for v in member_types.values())
        print(f"Fast mode: parsed {total} member(s), skipping cppcheck...", file=sys.stderr)
        if total == 0:
            print("  warning: no members parsed. Check that the paths point at "
                  "C++ sources or headers.", file=sys.stderr)
        if VERBOSE and member_types:
            dump_member_types(member_types, strict=True)
        if args.report_only:
            for hdr, i, cls, mn, dv in find_uninit_candidates(headers, member_types):
                print(f"  {hdr}:{i + 1}: {cls}::{mn} -> {dv}")
            return
        if args.init_list:
            print("Error: --fast only supports in-class defaults, not --init-list.",
                  file=sys.stderr)
            sys.exit(1)
        n = fix_all_in_class(headers, member_types)
        print(f"\nDone. Added in-class defaults for {n} member(s). Backups (*.bak) created.",
              file=sys.stderr)
        return

    extra = []
    for inc in args.includes:
        extra.extend(['-I', inc])
    for d in args.defines:
        extra.extend(['-D', d])

    also_read = all_sources + find_headers(all_sources, args.includes)

    # ---- Phase 1: Extract macros for Pass 2 ----
    print("Phase 1: scanning for macros...", file=sys.stderr)
    macros = extract_macros(also_read)
    print(f"  found {len(macros)} user macro(s)", file=sys.stderr)
    if VERBOSE and macros:
        for m in macros:
            print(f"    {m}", file=sys.stderr)
    if not macros and VERBOSE:
        print("    (none found - skipping per-macro passes)", file=sys.stderr)

    # Verify --max-configs covers all macro combinations
    max_cfg = getattr(args, 'max_configs', 9999)
    if macros:
        needed = 1 << len(macros)   # 2^N
        if needed > max_cfg:
            print(f"ERROR: --max-configs={max_cfg} is too low for {len(macros)} macros.",
                  file=sys.stderr)
            print(f"  Worst-case configs needed: 2^{len(macros)} = {needed}",
                  file=sys.stderr)
            print(f"  Rerun with: --max-configs={needed}", file=sys.stderr)
            sys.exit(1)
        if VERBOSE:
            print(f"  max-configs check: 2^{len(macros)} = {needed} <= {max_cfg} (OK)",
                  file=sys.stderr)

    # ---- Phase 2: Run cppcheck (base + per-macro passes) ----
    print("Phase 2: running cppcheck (base + per-macro passes)...", file=sys.stderr)
    project_path = os.path.abspath(args.project) if args.project else None
    if VERBOSE:
        cmd0 = cppcheck_cmd(sources, extra, project_path, max_cfg)
        print(f"  cmd: {' '.join(cmd0)}", file=sys.stderr)
    xml_root = cppcheck_xml(sources, extra, project=project_path,
                            max_configs=max_cfg)

    for m in macros:
        print(f"Phase 2: running cppcheck (-D{m}=1)...", file=sys.stderr)
        if VERBOSE:
            cmd2 = cppcheck_cmd(sources, extra + [f'-D{m}=1'], project_path, max_cfg)
            print(f"  cmd: {' '.join(cmd2)}", file=sys.stderr)
        xml2 = cppcheck_xml(sources, extra + [f'-D{m}=1'],
                            project=project_path, max_configs=max_cfg)
        # Merge into first pass results
        e1 = xml_root.find('errors')
        e2 = xml2.find('errors')
        if e1 is not None and e2 is not None:
            for err in e2:
                e1.append(err)
            if VERBOSE:
                n2 = len(xml2.findall('.//error'))
                print(f"  -D{m}=1 added {n2} error(s)", file=sys.stderr)

    # ---- Phase 3: Parse findings ----
    print("Phase 3: parsing findings...", file=sys.stderr)
    findings = parse_findings(xml_root)
    print(f"  {len(findings)} uninitialized member(s)", file=sys.stderr)
    if VERBOSE and findings:
        # Group by constructor line for readability
        by_line = defaultdict(list)
        for f in findings:
            by_line[(f[0], f[1])].append((f[2], f[3]))
        for (fp, ln), members in sorted(by_line.items()):
            print(f"    {os.path.basename(fp)}:{ln} - {len(members)} member(s)", file=sys.stderr)
            for cls, mname in sorted(members):
                print(f"      {cls}::{mname}", file=sys.stderr)

    if args.report_only:
        for f in sorted(set(findings)):
            print(f"  {f[0]}:{f[1]}: {f[2]}::{f[3]}")
        return

    # ---- Phase 4: Parse member types ----
    print("Phase 4: parsing member types from headers...", file=sys.stderr)
    total_types = sum(len(v) for v in member_types.values())
    print(f"  parsed {total_types} member(s) from headers", file=sys.stderr)
    if VERBOSE and member_types:
        dump_member_types(member_types)

    # ---- Phase 5: Apply fixes ----
    print("Phase 5: applying fixes...", file=sys.stderr)

    if getattr(args, 'init_list', False):
        # Constructor init-list mode: insert into constructor init lists
        by_file = defaultdict(list)
        for f in findings:
            by_file[f[0]].append((f[2], f[3], f[1]))  # (class, member, line)

        total = 0
        for fp, fgs in sorted(by_file.items()):
            if VERBOSE:
                print(f"  editing {os.path.basename(fp)}...", file=sys.stderr)
            n, errs = fix_file(fp, fgs, member_types)
            if n:
                print(f"  fixed {n} constructor(s) in {os.path.basename(fp)}", file=sys.stderr)
            if VERBOSE and errs:
                for e in errs:
                    print(f"    warning: {e}", file=sys.stderr)
            total += n
        print(f"\nDone. Fixed {total} constructor(s). Backups (*.bak) created.", file=sys.stderr)
        return

    # Default: in-class mode -- add default initializers to header declarations
    n = fix_in_class(headers, findings, member_types)
    if n:
        print(f"  added in-class initializers for {n} member(s)", file=sys.stderr)
    else:
        print("  (nothing to fix)", file=sys.stderr)
    print(f"\nDone. All uninit members have in-class defaults. Backups (*.bak) created.", file=sys.stderr)


if __name__ == '__main__':
    main()
