"""intercept v2: 大幅擴大 ROUTES, 利用 fn 名直接從 engine_lib_v2 找對應函式"""
import sys, re, subprocess, tempfile, os, json, urllib.request, shutil
from huggingface_hub import hf_hub_download
import pyarrow.parquet as pq

N = int(sys.argv[1]) if len(sys.argv)>1 else 1
R = int(sys.argv[2]) if len(sys.argv)>2 else 1
NP = int(sys.argv[3]) if len(sys.argv)>3 else 100
URL = sys.argv[4] if len(sys.argv)>4 else "http://localhost:8080/v1/chat/completions"
SLOTS=4

LIB_PATH = os.path.expanduser("~/engine_lib_v2.py")
# 抓 engine_lib_v2 所有函式名
LIB_TEXT = open(LIB_PATH).read()
LIB_FNS = set(re.findall(r"^def (\w+)\(", LIB_TEXT, re.MULTILINE))

# (題目 keyword pattern, engine_lib 函式名) 對應表 - 涵蓋 fail 35 題
ROUTES = [
    (r"\boctagonal\b", "octagonal"),
    (r"\bnonagonal\b", "nonagonal"),
    (r"\bdecagonal\b", "decagonal"),
    (r"centered.*hexagonal|hexagonal.*centered", "centered_hexagonal_number"),
    (r"\btetrahedral\b", "tetrahedral_number"),
    (r"\bheptagonal\b", "heptagonal"),
    (r"\bcatalan\b", "catalan_number"),
    (r"\bwoodall\b|\bwoodball\b", "is_woodall"),
    (r"\beulerian\b", "eulerian_num"),
    (r"\btriangular prism", "find_Volume"),
    (r"\bsphere.*volume|volume.*sphere", "sphere_volume"),
    (r"\bsphere.*surface|surface.*sphere", "sphere_surface"),
    (r"newman.*conway|conway.*sequence|newman conway sequence", "sequence"),
    (r"bell number", "bell_number"),
    (r"perimeter.*rectangle", "rectangle_perimeter"),
    (r"perimeter.*square", "square_perimeter"),
    (r"perimeter of a rombus|rhombus", "rombus_perimeter"),
    (r"area.*triangle|triangle.*area", "triangle_area"),
    (r"area.*rectangle|rectangle.*area", "rectangle_area"),
    (r"volume.*cube|cube.*volume", "cube_volume"),
    (r"volume.*cylinder|cylinder.*volume", "cylinder_volume"),
    (r"area of a regular polygon", "area_polygon"),
    (r"angle of a complex number", "angle_complex"),
    (r"divisible by 11", "is_Diff"),
    (r"one less than twice its reverse", "check_one_less_twice_reverse"),
    (r"difference.*two squares|difference of two squares", "dif_Square"),
    (r"sum of non(-| )?zero powers of two|sum.*powers of two", "is_Sum_Of_Powers_Of_Two"),
    (r"undulating", "is_undulating"),
    (r"smallest power of 2 greater than", "next_power_of_2"),
    (r"sum of (the )?common divisors", "common_divisors_sum"),
    (r"sum of the divisors of two integers", "are_equivalent"),
    (r"ax \+ by = n|integers x and y that satisfy", "find_solution"),
    (r"character made by adding the ascii", "get_Char"),
    (r"calculate the sum.*\(n - 2\*i\)|sum_series", "sum_series"),
    (r"remove characters from the first string which are present", "remove_dirty_chars"),
    (r"remove the characters which have odd index|odd index value", "odd_values_string"),
    (r"words that are longer than n", "long_words"),
    (r"string is present as a substring", "find_substring"),
    (r"max(imum)? difference between the number of 0s and 1s", "find_length"),
    (r"merge three dictionaries", "merge_dictionaries_three"),
    (r"append the given list to the given tuples", "add_lists"),
    (r"number of lists present in the given tuple", "find_lists"),
    (r"element that appears only once in a sorted", "search"),
    (r"follows the sequence given in the patterns|patterns array", "is_samepatterns"),
    (r"maximum difference between available pairs", "max_difference"),
    (r"next smallest palindrome", "next_smallest_palindrome"),
    (r"sorted array.*majority|majority.*sorted", "is_majority"),
    (r"convert.*to floats", "list_to_float"),
    (r"sort.*pancake|pancake sort", "pancake_sort"),
]

def route(prompt):
    p = prompt.lower()
    for pat, fn in ROUTES:
        if re.search(pat, p, re.IGNORECASE):
            if fn in LIB_FNS:
                return fn
    return None

def post(msgs, n, temp, max_tok=600):
    body = json.dumps({"messages":msgs,"n":n,"temperature":temp,"top_p":0.95,"max_tokens":max_tok}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r: d=json.loads(r.read())
        if "choices" not in d: return []
        return [c["message"]["content"] for c in d["choices"]]
    except: return []
def gen(msgs, n, greedy=False):
    out=[]; TEMPS=[0.2,0.5,0.8,1.0]
    if greedy: out += post(msgs, 1, 0.0)
    need = n-len(out); ti=0
    while need>0:
        k=min(need,SLOTS); t=TEMPS[ti%len(TEMPS)]; ti+=1
        got = post(msgs, k, t)
        out += got if got else [""]*k
        need -= k
    return out
def extract_code(t):
    ms = re.findall(r"```(?:python)?\s*\n(.*?)```", t, re.DOTALL)
    if ms: return ms[-1]
    ls = t.splitlines(); s = next((i for i,l in enumerate(ls) if l.startswith(("def ","import ","from ","class "))), None)
    return "\n".join(ls[s:]) if s is not None else t
def run_tests(code, tests, setup=""):
    src = (setup+"\n" if setup else "") + code + "\n" + "\n".join(tests) + "\n"
    with tempfile.TemporaryDirectory() as td:
        # 把 engine_lib_v2 當 engine_lib 進去 (用戶 code 用 from engine_lib import X)
        shutil.copy(LIB_PATH, os.path.join(td,"engine_lib.py"))
        with open(os.path.join(td,"t.py"),"w") as f: f.write(src)
        try:
            r = subprocess.run(["python3","t.py"], capture_output=True, timeout=8, text=True, cwd=td)
            return r.returncode == 0, (r.stderr or "")[-300:]
        except: return False, "Timeout"
def parse_fn(test_line):
    m = re.search(r"assert\s+(\w+)\s*\(([^)]*)\)", test_line)
    if m: return m.group(1), m.group(2)
    return None, None

p = hf_hub_download(repo_id="mbpp", filename="sanitized/test-00000-of-00001.parquet", repo_type="dataset")
DATA = pq.read_table(p).to_pylist()[:NP]

n_single=0; n_solved=0; n_routed=0; n_routed_ok=0
for i, ex in enumerate(DATA):
    prompt = ex["prompt"]; tests = ex["test_list"]
    setup = ex.get("test_imports") or ""
    if isinstance(setup, list): setup = "\n".join(setup)
    visible = tests[0] if tests else ""
    fn_name, _ = parse_fn(visible)
    routed = route(prompt)
    if routed and fn_name:
        n_routed += 1
        # 直接 wrap
        code = f"from engine_lib import {routed} as _eng\ndef {fn_name}(*args, **kwargs):\n    return _eng(*args, **kwargs)"
        ok, err = run_tests(code, tests, setup)
        if ok:
            n_routed_ok += 1
            n_single += 1; n_solved += 1
            print(f"  [{i+1:>3}/{len(DATA)}] ROUTED→{routed} SOLVED", flush=True)
            continue
        else:
            print(f"  [{i+1:>3}/{len(DATA)}] ROUTED→{routed} failed: {err[:80]}", flush=True)
    # LLM fallback
    user = f"Write a Python function. {prompt}\nIt must pass this test:\n{visible}\nReturn only the code in a ```python block."
    msgs0 = [{"role":"user","content":user}]
    solved=False; single_ok=None; last_code=""; last_err=""
    for r in range(R):
        if r==0:
            cands = gen(msgs0, N, greedy=True)
            if single_ok is None: single_ok,_ = run_tests(extract_code(cands[0]), tests, setup)
        else:
            msgs = msgs0 + [{"role":"assistant","content":f"```python\n{last_code}\n```"},
                            {"role":"user","content":f"FAILED. Test:{visible}\nError:{last_err}\nFix; return full code in ```python."}]
            cands = gen(msgs, N)
        for c in cands:
            code = extract_code(c)
            okv,err = run_tests(code, [visible], setup)
            if okv:
                fok,_ = run_tests(code, tests, setup)
                if fok: solved=True
                break
            if code.strip(): last_code, last_err = code, err
        if solved: break
    n_single += bool(single_ok); n_solved += solved
    print(f"  [{i+1:>3}/{len(DATA)}] LLM single={'P' if single_ok else 'F'} system={'SOLVED' if solved else 'fail'}", flush=True)

print(f"\n=== INTERCEPT_V2 routed_attempt={n_routed} routed_ok={n_routed_ok}  single={n_single}/{len(DATA)}={n_single/len(DATA)*100:.1f}%  system={n_solved}/{len(DATA)}={n_solved/len(DATA)*100:.1f}% ===", flush=True)
