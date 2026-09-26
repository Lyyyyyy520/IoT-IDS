import '@testing-library/jest-dom/vitest';

// jsdom intentionally does not implement pseudo-element style lookup. Ant Design
// asks for it only while measuring the scrollbar around an open modal.
const jsdomGetComputedStyle = window.getComputedStyle.bind(window);
window.getComputedStyle = (element: Element) => jsdomGetComputedStyle(element);
