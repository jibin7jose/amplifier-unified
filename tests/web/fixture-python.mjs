// Managed test environments prepare Python outside the source checkout. Pass
// the existing local fallback (including any older override) without quoting
// or splitting it: child_process receives one executable path on every OS.
export function fixturePython(fallback) {
 return process.env.AMPLIFIER_TEST_PYTHON || fallback;
}
