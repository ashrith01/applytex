(() => {
  /**
   * Workday My Experience identity helpers.
   * Orchestration remains in panel.js; these pure helpers are shared.
   */
  function normalizedIdentity(value) {
    return String(value || "")
      .replace(/\s+/g, " ")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  function normalizedEducationSchool(value) {
    const normalized = normalizedIdentity(value);
    if (/\bamrita\b/.test(normalized) && /\b(?:vishwa vidyapeetham|school of engineering)\b/.test(normalized)) {
      return "amrita vishwa vidyapeetham";
    }
    return normalized;
  }

  function workdayIdentityMatches(identity, record, kind) {
    if (kind === "education") {
      return Boolean(identity.school)
        && normalizedEducationSchool(identity.school) === normalizedEducationSchool(record.school);
    }
    const companyMatches = identity.company
      && normalizedIdentity(identity.company) === normalizedIdentity(record.company);
    const titleMatches = identity.title
      && normalizedIdentity(identity.title) === normalizedIdentity(record.job_title);
    return Boolean(companyMatches && (!identity.title || titleMatches));
  }

  function workdayIdentityIsBlank(identity, kind) {
    return kind === "education"
      ? !identity.school
      : !identity.company && !identity.title;
  }

  function workdayIdentityCompatible(identity, record, kind) {
    if (kind === "education") {
      return !identity.school
        || normalizedEducationSchool(identity.school) === normalizedEducationSchool(record.school);
    }
    const companyCompatible = !identity.company
      || normalizedIdentity(identity.company) === normalizedIdentity(record.company);
    const titleCompatible = !identity.title
      || normalizedIdentity(identity.title) === normalizedIdentity(record.job_title);
    return companyCompatible && titleCompatible;
  }

  globalThis.ApplyTexPanelWorkday = Object.freeze({
    normalizedIdentity,
    normalizedEducationSchool,
    workdayIdentityMatches,
    workdayIdentityIsBlank,
    workdayIdentityCompatible,
  });
})();
