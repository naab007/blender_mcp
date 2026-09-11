"""Diff two apichecks output files (out_<ver>.jsonl) key by key.

Usage: python diff_outputs.py out_4.3.2.jsonl out_5.2.1.jsonl
Prints, per script and per top-level key: values only in A, only in B, or changed.
List values are compared as sets (order-insensitive) and as lists when lengths differ.
"""
import json, sys, re

def load(path):
    scripts, cur, errors = {}, None, {}
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.rstrip("\n")
        if line.startswith("### "):
            cur = line[4:].strip(); scripts.setdefault(cur, {}); errors.setdefault(cur, [])
            continue
        m = re.match(r"^[A-Z0-9]+\s+(\{.*\})$", line)
        if m and cur:
            try:
                scripts[cur].update(json.loads(m.group(1)))
            except json.JSONDecodeError as e:
                errors[cur].append(f"bad json: {e}")
        elif cur and ("Error" in line or "Traceback" in line or re.search(r"line \d+", line)):
            errors[cur].append(line.strip())
    return scripts, errors

def norm(v):
    if isinstance(v, list):
        try:
            return sorted(v, key=lambda x: json.dumps(x, sort_keys=True))
        except TypeError:
            return v
    return v

def main(a_path, b_path):
    A, ea = load(a_path); B, eb = load(b_path)
    for script in sorted(set(A) | set(B)):
        a, b = A.get(script, {}), B.get(script, {})
        lines = []
        for k in sorted(set(a) | set(b)):
            if k not in a: lines.append(f"  + {k}: only in B = {json.dumps(b[k])[:300]}")
            elif k not in b: lines.append(f"  - {k}: only in A = {json.dumps(a[k])[:300]}")
            elif norm(a[k]) != norm(b[k]):
                if isinstance(a[k], list) and isinstance(b[k], list):
                    sa, sb = set(map(json.dumps, a[k])), set(map(json.dumps, b[k]))
                    lines.append(f"  ~ {k}: removed={sorted(sa-sb)} added={sorted(sb-sa)}")
                else:
                    lines.append(f"  ~ {k}: A={json.dumps(a[k])[:200]} B={json.dumps(b[k])[:200]}")
        errs = [f"  ! A: {e}" for e in ea.get(script, [])] + [f"  ! B: {e}" for e in eb.get(script, [])]
        if lines or errs:
            print(f"== {script} ==")
            print("\n".join(errs + lines))
        else:
            print(f"== {script} == identical")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
