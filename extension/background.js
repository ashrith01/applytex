chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id || !tab.url || !providerAllowed(tab.url)) {
    return;
  }
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ["panel.js"],
  });
  await chrome.tabs.sendMessage(tab.id, { type: "SMARTJOBAPPLY_OPEN" }).catch(() => {});
});

function providerAllowed(url) {
  try {
    const hostname = new URL(url).hostname;
    return (
      hostname === "www.linkedin.com" ||
      hostname === "linkedin.com" ||
      hostname.endsWith("greenhouse.io") ||
      hostname.endsWith("lever.co") ||
      hostname.endsWith("ashbyhq.com")
    );
  } catch {
    return false;
  }
}
