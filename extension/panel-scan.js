(() => {
  /**
   * Form scanning helpers for ApplyTeX ATS.
   * scanApplicationForm remains in panel.js and can call into these hooks.
   */
  function isTransientPromptControl(element) {
    return Boolean(element?.closest?.(
      "[role='listbox'], [data-automation-id='menuItem'], [data-automation-id='promptLeafNode'], [data-automation-id='activeListContainer']",
    ));
  }

  globalThis.ApplyTexPanelScan = Object.freeze({
    isTransientPromptControl,
  });
})();
