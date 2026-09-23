import "@testing-library/jest-dom";

// jsdom 未实现 scrollIntoView，移动导航自动滚动用到，给空实现避免测试报错
if (typeof window !== "undefined" && !window.HTMLElement.prototype.scrollIntoView) {
  window.HTMLElement.prototype.scrollIntoView = () => {};
}
