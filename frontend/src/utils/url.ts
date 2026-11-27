/**
 * Safely validate an external URL before rendering it inside an anchor href.
 * Rejects dangerous protocols such as javascript:, data:, or vbscript:.
 */
export function getSafeExternalUrl(rawUrl: string | null | undefined): string | null {
  if (!rawUrl || typeof rawUrl !== 'string') {
    return null;
  }
  const trimmed = rawUrl.trim();
  if (!trimmed) {
    return null;
  }
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol === 'https:' || parsed.protocol === 'http:') {
      return parsed.href;
    }
    return null;
  } catch {
    return null;
  }
}
