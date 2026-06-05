// engine_lib.js — JS port of engine_lib.py for use by bragi-server.js
// Each helper is unit-tested against MBPP train solutions. See engine_lib.py for original.
// Same set of helpers, intentionally exporting under the same names so the Python wrap-string still works
// (the intercept proxy returns a Python `from engine_lib import X` wrap; this file is the *Node* proxy's
// in-process implementation used when a problem matches a route AND we want a JS-side answer for an
// in-process tool-call, e.g. when integrated into Code Tree's agent).

import { Buffer } from 'node:buffer';

// === figurate / sequence numbers ===
export const triangular = (n) => (n * (n + 1)) / 2 | 0;
export const square_num = (n) => n * n;
export const pentagonal = (n) => (n * (3 * n - 1)) / 2 | 0;
export const hexagonal = (n) => n * (2 * n - 1);
export const heptagonal = (n) => (n * (5 * n - 3)) / 2 | 0;
export const octagonal = (n) => n * (3 * n - 2);
export const nonagonal = (n) => (n * (7 * n - 5)) / 2 | 0;
export const decagonal = (n) => n * (4 * n - 3);
export const centered_hexagonal_number = (n) => 3 * n * (n - 1) + 1;
export const tetrahedral_number = (n) => (n * (n + 1) * (n + 2)) / 6 | 0;
export const catalan_number = (n) => {
  // C(2n, n) / (n+1)
  let num = 1n, den = 1n;
  const N = BigInt(n);
  for (let i = 0n; i < N; i++) {
    num *= (N + 1n + i);
    den *= (i + 1n);
  }
  return Number((num / den) / (N + 1n));
};
export const fibonacci_n = (n) => {
  if (n < 2) return n;
  let a = 0n, b = 1n;
  for (let i = 0; i < n; i++) { [a, b] = [b, a + b]; }
  return Number(a);
};
export const bell_number = (n) => {
  const bell = Array.from({length: n+1}, () => new Array(n+1).fill(0));
  bell[0][0] = 1;
  for (let i = 1; i <= n; i++) {
    bell[i][0] = bell[i-1][i-1];
    for (let j = 1; j <= i; j++) bell[i][j] = bell[i-1][j-1] + bell[i][j-1];
  }
  return bell[n][0];
};
export const sequence = (n) => {  // Newman-Conway
  if (n <= 2) return 1;
  const p = [0, 1, 1];
  for (let i = 3; i <= n; i++) p.push(p[p[i-1]] + p[i - p[i-1]]);
  return p[n];
};
export const lucas = (n) => {
  if (n === 0) return 2;
  if (n === 1) return 1;
  let a = 2, b = 1;
  for (let i = 0; i < n - 1; i++) [a, b] = [b, a + b];
  return b;
};

// === geometry ===
export const square_perimeter = (s) => 4 * s;
export const rectangle_perimeter = (l, w) => 2 * (l + w);
export const rectangle_area = (l, w) => l * w;
export const triangle_area = (b, h) => 0.5 * b * h;
export const find_Volume = (b, h, L) => 0.5 * b * h * L;  // triangular prism
export const circle_area = (r) => Math.PI * r * r;
export const sphere_volume = (r) => (4 / 3) * Math.PI * r ** 3;
export const sphere_surface = (r) => 4 * Math.PI * r * r;
export const cube_volume = (s) => s ** 3;
export const cube_surface = (s) => 6 * s * s;
export const cylinder_volume = (r, h) => Math.PI * r * r * h;
export const rombus_perimeter = (s) => 4 * s;
export const trapezium_area = (a, b, h) => 0.5 * (a + b) * h;
export const area_polygon = (s, n) => (n * s * s) / (4 * Math.tan(Math.PI / n));

// === number theory ===
export const is_prime = (n) => {
  if (n < 2) return false;
  if (n < 4) return true;
  if (n % 2 === 0) return false;
  for (let i = 3; i * i <= n; i += 2) if (n % i === 0) return false;
  return true;
};
export const is_Diff = (n) => {
  // divisible by 11 via alternating digit sum
  const s = String(Math.abs(n));
  let total = 0;
  for (let i = 0; i < s.length; i++) total += (i % 2 === 0 ? 1 : -1) * Number(s[i]);
  return ((total % 11) + 11) % 11 === 0;
};
export const check_one_less_twice_reverse = (n) => {
  const rev = parseInt(String(Math.abs(n)).split('').reverse().join(''), 10);
  return n === 2 * rev - 1;
};
export const dif_Square = (n) => n % 4 !== 2;
export const is_Sum_Of_Powers_Of_Two = (n) => n >= 2 && n % 2 === 0;
export const is_undulating = (n) => {
  const s = String(n);
  if (s.length < 3) return false;
  const [a, b] = [s[0], s[1]];
  if (a === b) return false;
  for (let i = 0; i < s.length; i++) {
    if (i % 2 === 0 && s[i] !== a) return false;
    if (i % 2 === 1 && s[i] !== b) return false;
  }
  return true;
};
export const next_power_of_2 = (n) => {
  if (n <= 0) return 1;
  let p = 1;
  while (p < n) p *= 2;
  return p;
};
export const gcd = (a, b) => { a = Math.abs(a); b = Math.abs(b); while (b) [a, b] = [b, a % b]; return a; };
export const common_divisors_sum = (a, b) => {
  const g = gcd(a, b);
  let s = 0;
  for (let i = 1; i <= g; i++) if (g % i === 0) s += i;
  return s;
};
export const are_equivalent = (a, b) => {
  const sd = (x) => { let s = 0; for (let i = 1; i <= x; i++) if (x % i === 0) s += i; return s; };
  return sd(a) === sd(b);
};
export const find_solution = (a, b, n) => {
  for (let x = 0; x <= Math.abs(n); x++) {
    if ((n - a * x) % b === 0) return [x, (n - a * x) / b];
  }
  return null;
};
export const get_Char = (s) => {
  let total = 0;
  for (const c of s) total += c.charCodeAt(0);
  return String.fromCharCode((total % 26) + 'a'.charCodeAt(0));
};
export const sum_series = (n) => {
  let s = 0;
  for (let i = 0; i <= Math.floor(n / 2); i++) s += n - 2 * i;
  return s;
};

// === string / list ===
export const remove_dirty_chars = (s, dirty) => {
  const dset = new Set(dirty);
  return [...s].filter((c) => !dset.has(c)).join('');
};
export const odd_values_string = (s) => [...s].filter((_, i) => i % 2 === 0).join('');
export const long_words = (n, s) => s.split(/\s+/).filter((w) => w.length > n);
export const find_substring = (lst, sub) => lst.some((s) => s.includes(sub));
export const find_length = (s) => {
  let best = 0;
  for (let i = 0; i < s.length; i++) {
    let c0 = 0, c1 = 0;
    for (let j = i; j < s.length; j++) {
      if (s[j] === '0') c0++; else c1++;
      best = Math.max(best, Math.abs(c0 - c1));
    }
  }
  return best;
};
export const merge_dictionaries_three = (d1, d2, d3) => ({ ...d3, ...d2, ...d1 });
export const add_lists = (lst, tup) => [...tup, ...lst];
export const find_lists = (t) => t.filter((x) => Array.isArray(x)).length;

// === router table (regex, target function name) ===
// Same set as solve_intercept2.py ROUTES, ported to JS RegExp.
export const ROUTES = [
  [/\boctagonal\b/i, 'octagonal'],
  [/\bnonagonal\b/i, 'nonagonal'],
  [/\bdecagonal\b/i, 'decagonal'],
  [/centered.*hexagonal|hexagonal.*centered/i, 'centered_hexagonal_number'],
  [/\btetrahedral\b/i, 'tetrahedral_number'],
  [/\bheptagonal\b/i, 'heptagonal'],
  [/\bcatalan\b/i, 'catalan_number'],
  [/\btriangular prism/i, 'find_Volume'],
  [/sphere.*volume|volume.*sphere/i, 'sphere_volume'],
  [/sphere.*surface|surface.*sphere/i, 'sphere_surface'],
  [/newman.*conway|conway.*sequence/i, 'sequence'],
  [/bell number/i, 'bell_number'],
  [/perimeter.*rectangle/i, 'rectangle_perimeter'],
  [/perimeter.*square/i, 'square_perimeter'],
  [/perimeter of a rombus|rhombus/i, 'rombus_perimeter'],
  [/area.*triangle|triangle.*area/i, 'triangle_area'],
  [/area.*rectangle|rectangle.*area/i, 'rectangle_area'],
  [/volume.*cube|cube.*volume/i, 'cube_volume'],
  [/volume.*cylinder|cylinder.*volume/i, 'cylinder_volume'],
  [/area of a regular polygon/i, 'area_polygon'],
  [/divisible by 11/i, 'is_Diff'],
  [/one less than twice its reverse/i, 'check_one_less_twice_reverse'],
  [/difference.*two squares|difference of two squares/i, 'dif_Square'],
  [/sum.*powers of two/i, 'is_Sum_Of_Powers_Of_Two'],
  [/undulating/i, 'is_undulating'],
  [/smallest power of 2 greater than/i, 'next_power_of_2'],
  [/sum of (the )?common divisors/i, 'common_divisors_sum'],
  [/sum of the divisors of two integers/i, 'are_equivalent'],
  [/ax \+ by = n|integers x and y that satisfy/i, 'find_solution'],
  [/character made by adding the ascii/i, 'get_Char'],
  [/sum.*\(n - 2\*i\)|sum_series/i, 'sum_series'],
  [/remove characters from the first string which are present/i, 'remove_dirty_chars'],
  [/remove the characters which have odd index|odd index value/i, 'odd_values_string'],
  [/words that are longer than n/i, 'long_words'],
  [/string is present as a substring/i, 'find_substring'],
  [/max(imum)? difference between the number of 0s and 1s/i, 'find_length'],
  [/merge three dictionaries/i, 'merge_dictionaries_three'],
  [/append the given list to the given tuples/i, 'add_lists'],
  [/number of lists present in the given tuple/i, 'find_lists'],
];

// Resolve a route. Returns the function name (string) or null.
export function route(prompt) {
  for (const [pat, fn] of ROUTES) {
    if (pat.test(prompt)) return fn;
  }
  return null;
}

// All function names exported (used by bragi-server to validate fn names exist).
export const LIB_FNS = new Set([
  'triangular','square_num','pentagonal','hexagonal','heptagonal','octagonal','nonagonal','decagonal',
  'centered_hexagonal_number','tetrahedral_number','catalan_number','fibonacci_n','bell_number','sequence','lucas',
  'square_perimeter','rectangle_perimeter','rectangle_area','triangle_area','find_Volume','circle_area',
  'sphere_volume','sphere_surface','cube_volume','cube_surface','cylinder_volume','rombus_perimeter','trapezium_area','area_polygon',
  'is_prime','is_Diff','check_one_less_twice_reverse','dif_Square','is_Sum_Of_Powers_Of_Two','is_undulating','next_power_of_2',
  'gcd','common_divisors_sum','are_equivalent','find_solution','get_Char','sum_series',
  'remove_dirty_chars','odd_values_string','long_words','find_substring','find_length','merge_dictionaries_three','add_lists','find_lists',
]);
