(() => {
  /**
   * Fill helpers for ApplyTeX ATS.
   * fillReviewedFields still lives in panel.js and uses these shared utilities.
   */
  function setNativeValue(element, value) {
    const text = value == null ? "" : String(value);
    const previous = element && "value" in element ? element.value : "";
    let setter = null;
    let proto = element ? Object.getPrototypeOf(element) : null;
    while (proto && !setter) {
      const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
      if (descriptor?.set) setter = descriptor.set;
      proto = Object.getPrototypeOf(proto);
    }
    try {
      if (setter) setter.call(element, text);
      else if (element && "value" in element) element.value = text;
    } catch {
      try {
        if (element) element.value = text;
      } catch {
        /* ignore */
      }
    }
    try {
      const tracker = element && element._valueTracker;
      if (tracker && typeof tracker.setValue === "function") tracker.setValue(previous);
    } catch {
      /* ignore */
    }
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
