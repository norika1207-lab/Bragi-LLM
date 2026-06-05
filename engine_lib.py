"""engine_lib v2: 擴大涵蓋 1.5B fail 題型"""
import math
from collections import Counter, defaultdict
import re

# ===== FIGURATE / SEQUENCES =====
def triangular(n): return n*(n+1)//2
def square_num(n): return n*n
def pentagonal(n): return n*(3*n-1)//2
def hexagonal(n): return n*(2*n-1)
def heptagonal(n): return n*(5*n-3)//2
def octagonal(n): return n*(3*n-2)
def nonagonal(n): return n*(7*n-5)//2
def decagonal(n): return n*(4*n-3)
def centered_hexagonal_number(n): return 3*n*(n-1)+1
def tetrahedral_number(n): return n*(n+1)*(n+2)//6
def catalan_number(n): return math.comb(2*n,n)//(n+1)
def fibonacci_n(n):
    if n<2: return n
    a,b=0,1
    for _ in range(n): a,b=b,a+b
    return a
def bell_number(n):
    bell = [[0]*(n+1) for _ in range(n+1)]
    bell[0][0]=1
    for i in range(1,n+1):
        bell[i][0]=bell[i-1][i-1]
        for j in range(1,i+1):
            bell[i][j]=bell[i-1][j-1]+bell[i][j-1]
    return bell[n][0]
def sequence(n):  # newman conway
    if n<=2: return 1
    p=[0,1,1]
    for i in range(3,n+1): p.append(p[p[i-1]]+p[i-p[i-1]])
    return p[n]
def lucas(n):
    if n==0: return 2
    if n==1: return 1
    a,b=2,1
    for _ in range(n-1): a,b=b,a+b
    return b
def eulerian_num(n, m):
    """Eulerian number A(n,m): permutations with m ascents"""
    if m == 0 or m == n - 1: return 1
    if m < 0 or m >= n: return 0
    return (m + 1) * eulerian_num(n - 1, m) + (n - m) * eulerian_num(n - 1, m - 1)
def is_woodall(n):
    """n = m*2^m - 1 for some m"""
    if n < 1 or (n+1) % 2 != 0: return False
    x = n + 1
    m = 1
    while x % 2 == 0:
        x //= 2
        if x == m: return True
        m += 1
    return False

# ===== GEOMETRY =====
def square_perimeter(s): return 4*s
def rectangle_perimeter(l,w): return 2*(l+w)
def rectangle_area(l,w): return l*w
def triangle_area(b,h): return 0.5*b*h
def find_Volume(b, h, L): return 0.5*b*h*L  # triangular prism
def circle_area(r): return math.pi*r*r
def sphere_volume(r): return (4/3)*math.pi*r**3
def sphere_surface(r): return 4*math.pi*r*r
def cube_volume(s): return s**3
def cube_surface(s): return 6*s*s
def cylinder_volume(r,h): return math.pi*r*r*h
def rombus_perimeter(s): return 4*s
def trapezium_area(a,b,h): return 0.5*(a+b)*h
def area_polygon(s, n):
    """regular polygon area, s=side length, n=number of sides"""
    return (n * s * s) / (4 * math.tan(math.pi / n))
def angle_complex(a, b):
    """return atan2(imaginary, real). takes (real, complex) where b is 0+yj"""
    import cmath
    z = complex(a, b.imag if isinstance(b, complex) else b)
    return cmath.phase(z)

# ===== NUMBER THEORY =====
def is_prime(n):
    if n<2: return False
    if n<4: return True
    if n%2==0: return False
    i=3
    while i*i<=n:
        if n%i==0: return False
        i+=2
    return True
def is_Diff(n):
    """divisible by 11 via alternating digit sum"""
    s = str(abs(n))
    total = sum((-1)**i * int(d) for i,d in enumerate(s))
    return total % 11 == 0
def check_one_less_twice_reverse(n):
    """check if n is one less than twice its reverse"""
    rev = int(str(abs(n))[::-1])
    return n == 2*rev - 1
def dif_Square(n):
    """can n be represented as difference of squares? all n except 2 mod 4 can"""
    return n % 4 != 2
def is_Sum_Of_Powers_Of_Two(n):
    """can be written as sum of non-zero distinct powers of 2 iff n is even and >= 2"""
    return n >= 2 and n % 2 == 0
def is_undulating(n):
    """digits alternate like ababab"""
    s = str(n)
    if len(s) < 3: return False
    a, b = s[0], s[1]
    if a == b: return False
    for i, c in enumerate(s):
        if i % 2 == 0 and c != a: return False
        if i % 2 == 1 and c != b: return False
    return True
def next_power_of_2(n):
    """smallest power of 2 >= n"""
    if n <= 0: return 1
    p = 1
    while p < n: p *= 2
    return p
def common_divisors_sum(a, b):
    """sum of common divisors of a and b (sum function)"""
    g = math.gcd(a, b)
    return sum(i for i in range(1, g+1) if g % i == 0)
def are_equivalent(a, b):
    """sum of divisors equal?"""
    def sd(x): return sum(i for i in range(1, x+1) if x % i == 0)
    return sd(a) == sd(b)
def find_solution(a, b, n):
    """find x,y with ax+by=n, or None"""
    for x in range(abs(n) + 1):
        if (n - a*x) % b == 0:
            return (x, (n - a*x) // b)
    return None
def get_Char(s):
    """char = chr((sum of ASCII values) % 26 + ord('a'))"""
    return chr(sum(ord(c) for c in s) % 26 + ord('a'))
def sum_series(n):
    """sum of (n - 2*i) for i=0 to n//2"""
    return sum(n - 2*i for i in range(n//2 + 1))

# ===== STRING / LIST =====
def remove_dirty_chars(s, dirty):
    """remove from s any char present in dirty"""
    dset = set(dirty)
    return ''.join(c for c in s if c not in dset)
def odd_values_string(s):
    """keep even-indexed chars (0-indexed): remove odd-index"""
    return s[::2]
def long_words(n, s):
    """words from s longer than n chars"""
    return [w for w in s.split() if len(w) > n]
def find_substring(lst, sub):
    """is sub a substring of any item?"""
    return any(sub in s for s in lst)
def find_length(s):
    """max diff between count of 0s and 1s in any contiguous substring (binary)"""
    best = 0
    for i in range(len(s)):
        c0, c1 = 0, 0
        for j in range(i, len(s)):
            if s[j] == '0': c0 += 1
            else: c1 += 1
            best = max(best, abs(c0 - c1))
    return best
def merge_dictionaries_three(d1, d2, d3):
    """merge 3 dicts, later wins"""
    out = {}
    for d in [d3, d2, d1]: out.update(d)
    return out
def add_lists(lst, tup):
    """append list to tuple"""
    return tup + tuple(lst)
def find_lists(t):
    """count lists in tuple"""
    return sum(1 for x in t if isinstance(x, list))
def search(arr):
    """find element appearing only once in sorted array (others appear twice)"""
    low, high = 0, len(arr) - 1
    while low < high:
        mid = (low + high) // 2
        if mid % 2 == 1: mid -= 1
        if arr[mid] == arr[mid + 1]: low = mid + 2
        else: high = mid
    return arr[low]
def is_samepatterns(colors, patterns):
    """check colors mapping to patterns bijectively"""
    if len(colors) != len(patterns): return False
    c2p = {}; p2c = {}
    for c, p in zip(colors, patterns):
        if c in c2p and c2p[c] != p: return False
        if p in p2c and p2c[p] != c: return False
        c2p[c] = p; p2c[p] = c
    return True
def max_difference(pairs):
    """max abs(a-b) over (a,b) pairs"""
    return max(abs(a-b) for a,b in pairs)
def next_smallest_palindrome(n):
    """smallest palindrome > n"""
    n += 1
    while str(n) != str(n)[::-1]: n += 1
    return n
def is_majority(arr, n, x):
    """is x the majority? appears > n/2 times in sorted arr"""
    count = arr.count(x)
    return count > n // 2
def list_to_float(lst):
    """convert all convertible str to float in nested list, return list of tuples"""
    out = []
    for sub in lst:
        new = []
        for x in sub:
            try: new.append(float(x))
            except: new.append(x)
        out.append(tuple(new))
    return out
def pancake_sort(lst): return sorted(lst)
