import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';

if (typeof window !== 'undefined' && window.HTMLElement) {
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
}

afterEach(() => {
  if (typeof window !== 'undefined') {
    window.localStorage?.clear();
    window.sessionStorage?.clear();
  }
});
