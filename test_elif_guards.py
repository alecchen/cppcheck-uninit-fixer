#!/usr/bin/env python3
"""Test that #elif guard chains are tracked correctly by parse_member_types."""
import re, sys, os, tempfile
from collections import defaultdict

def parse_member_types(headers):
    result = defaultdict(dict)
    for hdr in headers:
        try:
            with open(hdr) as f:
                text = f.read()
        except Exception:
            continue
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
            guard_chain = []
            for raw_line in body.split('\n'):
                s = raw_line.strip()
                if s.startswith('#ifdef '):
                    guard_chain.append([f"defined({s[7:].strip()})"]); continue
                if s.startswith('#ifndef '):
                    guard_chain.append([f"!defined({s[7:].strip()})"]); continue
                if s.startswith('#if '):
                    guard_chain.append([s[4:].strip()]); continue
                if s.startswith('#elif '):
                    if guard_chain: guard_chain[-1].append(s[6:].strip()); continue
                if s == '#else':
                    if guard_chain: guard_chain[-1].append(None); continue
                if s.startswith('#endif'):
                    if guard_chain: guard_chain.pop(); continue
                if (not s or s.startswith(('//','#','/*','*','public','private','protected',
                    'typedef','using ','friend','static_assert','enum','template',
                    'explicit','virtual','operator','constexpr','noexcept',
                    'override','final','default','delete','struct','class'))): continue
                if '(' in s or ')' in s: continue
                m = re.match(r'^(.*?)\b(\w+)\s*[;=]', s)
                if not m: continue
                t, v = m.group(1).strip(), m.group(2)
                if guard_chain:
                    parts = []
                    for chain in guard_chain:
                        if len(chain) == 1 and chain[0] is not None:
                            active = chain[0]
                        elif chain[-1] is None:
                            negated = ' && '.join(f'!({c})' if '||' in c else f'!{c}' for c in chain[:-1])
                            active = negated
                        else:
                            prior = chain[:-1]
                            negated = ' && '.join(f'!({c})' if '||' in c else f'!{c}' for c in prior)
                            active = f'{negated} && {chain[-1]}'
                        parts.append(f'({active})' if '||' in active else active)
                    guard = ' && '.join(parts)
                else:
                    guard = None
                result[cls_name][v] = {'type': t, 'guard': guard}
    return result

# Test 1: #if / #elif / #else chain
test_hdr = """class TestChain {
#if A
    int a;
#elif B
    int b;
#else
    int c;
#endif
};
"""
f = tempfile.NamedTemporaryFile(mode='w', suffix='.h', delete=False)
f.write(test_hdr); f.close()
r = parse_member_types([f.name]); os.unlink(f.name)
g = r.get('TestChain', {})
assert 'A' in g.get('a', {}).get('guard', ''), f"guard for 'a': {g.get('a', {})}"
assert g.get('b', {}).get('guard', '') == '!A && B', f"#elif guard: {g.get('b', {})}"
assert g.get('c', {}).get('guard', '') == '!A && !B', f"#else guard: {g.get('c', {})}"
print("Test 1 PASS  #if A / #elif B / #else")

# Test 2: nested #ifdef + #if/#elif
test_hdr2 = """class TestNest {
#ifdef A
    int a;
#else
    int not_a;
#endif
#if B
    int b;
#elif C
    int c;
#endif
};
"""
f = tempfile.NamedTemporaryFile(mode='w', suffix='.h', delete=False)
f.write(test_hdr2); f.close()
r = parse_member_types([f.name]); os.unlink(f.name)
g = r.get('TestNest', {})
assert 'defined(A)' in g.get('a', {}).get('guard', ''), f"#ifdef a: {g.get('a', {})}"
assert '!defined(A)' in g.get('not_a', {}).get('guard', ''), f"#else not_a: {g.get('not_a', {})}"
assert g.get('c', {}).get('guard', '') == '!B && C', f"#elif c: {g.get('c', {})}"
print("Test 2 PASS  nested #ifdef/#else + #if/#elif")

# Test 3: multiple elifs
test_hdr3 = """class TestMulti {
#if ONE
    int one;
#elif TWO
    int two;
#elif THREE
    int three;
#else
    int none;
#endif
};
"""
f = tempfile.NamedTemporaryFile(mode='w', suffix='.h', delete=False)
f.write(test_hdr3); f.close()
r = parse_member_types([f.name]); os.unlink(f.name)
g = r.get('TestMulti', {})
assert g.get('two', {}).get('guard', '') == '!ONE && TWO', f"two: {g.get('two', {})}"
assert g.get('three', {}).get('guard', '') == '!ONE && !TWO && THREE', f"three: {g.get('three', {})}"
assert g.get('none', {}).get('guard', '') == '!ONE && !TWO && !THREE', f"none: {g.get('none', {})}"
print("Test 3 PASS  #if / #elif / #elif / #else")

# Test 4: #ifndef
test_hdr4 = """class TestIfndef {
#ifndef GUARD
    int x;
#endif
};
"""
f = tempfile.NamedTemporaryFile(mode='w', suffix='.h', delete=False)
f.write(test_hdr4); f.close()
r = parse_member_types([f.name]); os.unlink(f.name)
g = r.get('TestIfndef', {})
assert '!defined(GUARD)' in g.get('x', {}).get('guard', ''), f"#ifndef x: {g.get('x', {})}"
print("Test 4 PASS  #ifndef")

# Test 5: #elif after #ifndef (produces !! double-negation, which is correct)
test_hdr5 = """class TestElifNdef {
#ifndef GUARD
    int x;
#elif SPECIAL
    int y;
#endif
};
"""
f = tempfile.NamedTemporaryFile(mode='w', suffix='.h', delete=False)
f.write(test_hdr5); f.close()
r = parse_member_types([f.name]); os.unlink(f.name)
g = r.get('TestElifNdef', {})
assert 'SPECIAL' in g.get('y', {}).get('guard', ''), f"#elif after #ifndef: {g.get('y', {})}"
print("Test 5 PASS  #elif after #ifndef")

print("\nAll 5 guard tracking tests passed")
