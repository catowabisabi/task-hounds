import '@testing-library/jest-dom/vitest';

// jsdom does not implement matchMedia or IntersectionObserver; React
// and some libraries (lucide-react) reach for these at module load
// time. Stub them so the component imports do not crash.
if (typeof window !== 'undefined' && !window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}
