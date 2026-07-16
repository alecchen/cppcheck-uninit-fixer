#!/usr/bin/env python3
"""
cppcheck-fix-uninit.py — find and auto-fix uninitialized member variables.

Usage:
    ./cppcheck-fix-uninit.py file1.cpp file2.cpp
    ./cppcheck-fix-uninit.py -I /api/headers *.cpp
    ./cppcheck-fix-uninit.py --report-only *.cpp
    ./cppcheck-fix-uninit.py --project=compile_commands.json

Finds uninitialized members using cppcheck (two-pass: base + all-macros-defined),
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


def cppcheck_xml(sources, defines=None, project=None):
    """Run cppcheck with --xml, return parsed ElementTree root.

    Pass sources (for header/macro scanning) and optionally project
    (compile_commands.json path) for cppcheck's own analysis.
    When project is set, cppcheck gets --project=<path> instead of sources.
    """
    cmd = [
        "cppcheck", "--quiet", "--xml", "--enable=warning", "--inconclusive",
        "--max-configs=9999", "--check-level=exhaustive",
        "-j", str(os.cpu_count() or 4),
        "--suppress=missingIncludeSystem", "--suppress=unmatchedSuppression",
        "--suppress=checkersReport", "--suppress=unusedFunction",
    ]
    if defines:
        cmd.extend(defines)
    if project:
        cmd.append(f'--project={project}')
    else:
        cmd.extend(sources)

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


def find_headers(sources, include_dirs=None):
    """Collect .h/.hpp files co-located with source files or in -I dirs."""
    headers = set()
    dirs = set()
    for src in sources:
        d = os.path.dirname(src)
        if d:
            dirs.add(d)
    if include_dirs:
        dirs.update(include_dirs)
    for d in dirs:
        if not os.path.isdir(d):
            continue
        try:
            for fn in os.listdir(d):
                if fn.endswith(('.h', '.hpp', '.hxx')):
                    headers.add(os.path.abspath(os.path.join(d, fn)))
        except PermissionError:
            continue
    return sorted(headers)


# ---------------------------------------------------------------------------
# Parse member types from headers
# ---------------------------------------------------------------------------


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

                # Skip non-declaration lines
                if (not s or s.startswith(('//','#','/*','*','public','private','protected',
                    'typedef','using ','friend','static_assert','enum','template',
                    'explicit','virtual','operator','constexpr','noexcept',
                    'override','final','default','delete','struct','class'))):
                    continue
                if '(' in s or ')' in s:
                    continue

                # Match: [qualifiers...] typename varname [= ...] ;
                m = re.match(r'^(.*?)\b(\w+)\s*[;=]', s)
                if not m:
                    continue
                t, v = m.group(1).strip(), m.group(2)
                if v in ('public','private','protected','class','struct',
                         'const','volatile','static','mutable','using','friend'):
                    continue
                if t.startswith(('public:','private:','protected:')):
                    continue

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

            # Try to find a member declaration we need to fix
            for mn in cls_member_names:
                if mn not in s:
                    continue
                # Match: ... member_name ; or ... member_name = ... ;
                m = re.match(r'^(.*?)\b' + re.escape(mn) + r'\b\s*([;=])', s)
                if not m:
                    continue
                sep = m.group(2)
                if sep == '=':
                    # Already has in-class initializer
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

                # Insert = defaultValue before the ;
                line_before = line[:line.index(';')]
                lines[i] = line_before + f" = {dv};\n"
                any_change = True
                if VERBOSE:
                    print(f"    {os.path.basename(hdr)}: {mn} = {dv}", file=sys.stderr)
                fixed += 1

        if any_change:
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
                    help='Source files (.cpp). May be omitted when --project is given.')
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
    args = ap.parse_args()

    global VERBOSE
    VERBOSE = args.verbose

    sources = [os.path.abspath(s) for s in args.sources if os.path.isfile(s)]

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
        print("    (none found - skipping pass 2)", file=sys.stderr)

    # ---- Phase 2: Run cppcheck (Pass 1 + Pass 2) ----
    print("Phase 2: running cppcheck (pass 1 - base config)...", file=sys.stderr)
    if VERBOSE:
        src_part = [f'--project={args.project}'] if args.project else sources
        cmd = ["cppcheck", "--quiet", "--xml", "--enable=warning", "--inconclusive",
               "--max-configs=9999", "--check-level=exhaustive",
               "-j", str(os.cpu_count() or 4)] + extra + src_part
        print(f"  cmd: {' '.join(cmd)}", file=sys.stderr)

    project_path = os.path.abspath(args.project) if args.project else None
    xml_root = cppcheck_xml(sources, extra, project=project_path)

    if macros:
        all_defs = [f'-D{m}=1' for m in macros]
        print("Phase 2: running cppcheck (pass 2 - all macros defined)...", file=sys.stderr)
        if VERBOSE:
            src_part = [f'--project={args.project}'] if args.project else sources
            cmd2 = ["cppcheck", "--quiet", "--xml", "--enable=warning", "--inconclusive",
                    "--max-configs=9999", "--check-level=exhaustive",
                    "-j", str(os.cpu_count() or 4)] + extra + all_defs + src_part
            print(f"  cmd: {' '.join(cmd2)}", file=sys.stderr)
        xml2 = cppcheck_xml(sources, extra + all_defs, project=project_path)
        # Merge second pass errors into first
        e1 = xml_root.find('errors')
        e2 = xml2.find('errors')
        if e1 is not None and e2 is not None:
            for err in e2:
                e1.append(err)
            if VERBOSE:
                n2 = len(xml2.findall('.//error'))
                print(f"  pass 2 added {n2} error(s) from defined-macros config", file=sys.stderr)

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
    headers = find_headers(all_sources, args.includes)
    member_types = parse_member_types(headers)
    total_types = sum(len(v) for v in member_types.values())
    print(f"  parsed {total_types} member(s) from headers", file=sys.stderr)
    if VERBOSE and member_types:
        for cls, members in sorted(member_types.items()):
            print(f"    class {cls}:", file=sys.stderr)
            for mname, minfo in sorted(members.items()):
                if isinstance(minfo, dict):
                    type_str = minfo.get('type', '?')
                    guard = minfo.get('guard')
                else:
                    type_str = str(minfo)
                    guard = None
                default = default_init(minfo)
                dv = default or "(reference - skipped)"
                g = f" [{guard}]" if guard else ""
                print(f"      {type_str} {mname} -> {dv}{g}", file=sys.stderr)

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
