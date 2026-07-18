(() => {
  /**
   * Fill helpers for ApplyTeX ATS.
   * fillReviewedFields still lives in panel.js and uses these shared utilities.
   */
  function setNativeValue(element, value) {
    const prototype = element instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
    if (descriptor?.set) descriptor.set.call(element, value);
    else element.value = value;
  }

  function dispatchInputEvents(element) {
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }

  globalThis.ApplyTexPanelFill = Object.freeze({
    setNativeValue,
    dispatchInputEvents,
  });
})();
