(() => {
  const root = globalThis;
  if (root.ApplyTexProviders) return;

  const genericDescriptionSelectors = [
    "[data-testid*='description' i]",
    "[class*='description' i]",
    "[class*='job-description' i]",
    "[class*='posting' i]",
    "article",
    "main",
    "body",
  ];

  const providers = {
    linkedin: {
      label: "LinkedIn",
      depth: "capture",
      hosts: ["linkedin.com", "www.linkedin.com"],
      hostPermissions: ["https://www.linkedin.com/*"],
      selectors: {
        title: [".job-details-jobs-unified-top-card__job-title", ".jobs-unified-top-card__job-title", "h1"],
        company: [".job-details-jobs-unified-top-card__company-name", ".jobs-unified-top-card__company-name", ".company-name", "[data-testid='company-name']"],
        location: [".job-details-jobs-unified-top-card__primary-description-container", ".jobs-unified-top-card__bullet"],
        description: [".jobs-description__content", "#job-details"],
      },
    },
    greenhouse: {
      label: "Greenhouse",
      depth: "deep",
      suffixes: ["greenhouse.io"],
      hostPermissions: ["https://*.greenhouse.io/*"],
      selectors: {
        title: [".app-title", "h1"],
        company: ["meta[property='og:site_name']", ".company-name", "[data-mapped='true'] .company"],
        location: [".location", "[class*='location' i]"],
        description: ["#content", ".job__description", "main", "body"],
      },
      stopMarkers: [
        "PLEASE NOTE: We collect, retain and use personal data",
        "We collect, retain and use personal data",
        "Create a Job Alert",
        "Apply for this job",
        "Voluntary Self-Identification",
      ],
    },
    lever: {
      label: "Lever",
      depth: "deep",
      suffixes: ["lever.co"],
      hostPermissions: ["https://*.lever.co/*"],
      selectors: {
        title: [".posting-headline h2", "h1"],
        company: [".main-header-logo img", "meta[property='og:site_name']"],
        location: [".posting-categories .location", ".location"],
        description: [".posting-page .content", ".posting-description", "main"],
      },
    },
    ashby: {
      label: "Ashby",
      depth: "deep",
      suffixes: ["ashbyhq.com"],
      hostPermissions: ["https://*.ashbyhq.com/*"],
      beforeCaptureTab: "Overview",
      afterCaptureTab: "Application",
      selectors: {
        title: ["h1", "[data-testid='job-title']", "[class*='job-title' i]"],
        company: ["meta[property='og:site_name']", "[class*='company' i]", "header"],
        location: ["[class*='location' i]", "[data-testid='location']"],
        description: ["main", "[data-testid='job-description']", "[class*='description' i]"],
      },
      stopMarkers: ["Apply for this Job", "Powered by Ashby"],
    },
    workday: {
      label: "Workday",
      depth: "deep",
      suffixes: ["myworkdayjobs.com", "myworkdaysite.com"],
      hostPermissions: ["https://*.myworkdayjobs.com/*", "https://*.myworkdaysite.com/*"],
      selectors: {
        title: ["h1", "[data-automation-id='jobPostingHeader']", "[data-automation-id='jobPostingTitle']", "[data-automation-id='jobTitle']"],
        company: ["meta[property='og:site_name']", "[data-automation-id='company']", "[data-automation-id='businessSite']"],
        location: ["[data-automation-id='locations']", "[data-automation-id='location']", "[class*='location' i]"],
        description: ["[data-automation-id='jobPostingDescription']", "[data-automation-id='jobDescription']", ...genericDescriptionSelectors],
      },
      stopMarkers: ["Apply now", "Apply for this job", "Similar Jobs", "Already applied?", "Privacy Statement"],
    },
    icims: {
      label: "iCIMS",
      depth: "capture",
      suffixes: ["icims.com"],
      hostPermissions: ["https://*.icims.com/*"],
      selectors: {
        title: ["h1", ".iCIMS_Header h1", "[class*='job-title' i]", "[data-testid='job-title']"],
        company: ["meta[property='og:site_name']", "[class*='company' i]", "header"],
        location: ["[class*='location' i]", ".iCIMS_JobHeaderData"],
        description: [".iCIMS_JobContent", "#job-description", "[class*='description' i]", "main"],
      },
      stopMarkers: ["Options", "Apply for this job online", "Share", "Need help finding the right job?"],
    },
    smartrecruiters: {
      label: "SmartRecruiters",
      depth: "capture",
      suffixes: ["smartrecruiters.com"],
      hostPermissions: ["https://*.smartrecruiters.com/*"],
      selectors: {
        title: ["h1", "[data-testid='job-title']", "[class*='job-title' i]"],
        company: ["meta[property='og:site_name']", "[class*='company' i]", "header"],
        location: ["[data-testid='job-location']", "[class*='location' i]"],
        description: ["[data-testid='job-description']", "#job-description", "[class*='job-description' i]", "main"],
      },
      stopMarkers: ["Apply now", "I'm interested", "Privacy Policy"],
    },
    workable: {
      label: "Workable",
      depth: "capture",
      suffixes: ["workable.com"],
      hosts: ["apply.workable.com"],
      hostPermissions: ["https://apply.workable.com/*", "https://*.workable.com/*"],
      selectors: {
        title: ["h1", "[data-ui='job-title']", "[class*='job-title' i]"],
        company: ["meta[property='og:site_name']", "[data-ui='company-name']", "[class*='company' i]", "header"],
        location: ["[data-ui='job-location']", "[class*='location' i]"],
        description: ["[data-ui='job-description']", "[class*='job-description' i]", "main"],
      },
      stopMarkers: ["Apply for this job", "Powered by Workable"],
    },
    indeed: {
      label: "Indeed",
      depth: "experimental",
      hosts: ["indeed.com", "www.indeed.com"],
      hostPermissions: ["https://www.indeed.com/*"],
      selectors: {
        title: ["h1", "[data-testid='jobsearch-JobInfoHeader-title']", "[class*='jobsearch-JobInfoHeader-title' i]"],
        company: ["[data-testid='inlineHeader-companyName']", "[data-company-name='true']", "[class*='company' i]", "meta[property='og:site_name']"],
        location: ["[data-testid='job-location']", "[class*='jobsearch-JobInfoHeader-subtitle' i]", "[class*='location' i]"],
        description: ["#jobDescriptionText", "[data-testid='jobsearch-JobComponent-description']", "[class*='jobsearch-jobDescriptionText' i]", "main"],
      },
      stopMarkers: ["Apply now", "Report job", "Job activity"],
    },
    ziprecruiter: {
      label: "ZipRecruiter",
      depth: "experimental",
      hosts: ["ziprecruiter.com", "www.ziprecruiter.com"],
      hostPermissions: ["https://www.ziprecruiter.com/*"],
      selectors: {
        title: ["h1", "[data-testid='job-title']", "[class*='job_title' i]", "[class*='job-title' i]"],
        company: ["[data-testid='company-name']", "[class*='company' i]", "meta[property='og:site_name']"],
        location: ["[data-testid='job-location']", "[class*='location' i]"],
        description: ["[data-testid='job-description']", "[class*='job_description' i]", "[class*='job-description' i]", "main"],
      },
      stopMarkers: ["Apply Now", "Report Job", "Similar Jobs"],
    },
    glassdoor: {
      label: "Glassdoor",
      depth: "experimental",
      hosts: ["glassdoor.com", "www.glassdoor.com"],
      hostPermissions: ["https://www.glassdoor.com/*"],
      selectors: {
        title: ["h1", "[data-test='job-title']", "[class*='job-title' i]"],
        company: ["[data-test='employer-name']", "[class*='employer' i]", "[class*='company' i]", "meta[property='og:site_name']"],
        location: ["[data-test='location']", "[class*='location' i]"],
        description: ["#JobDescriptionContainer", "[data-test='jobDescription']", "[class*='jobDescription' i]", "main"],
      },
      stopMarkers: ["Apply on employer site", "Glassdoor", "Report job"],
    },
    wellfound: {
      label: "Wellfound",
      depth: "experimental",
      hosts: ["wellfound.com", "www.wellfound.com"],
      hostPermissions: ["https://wellfound.com/*"],
      selectors: {
        title: ["h1", "[data-test='JobTitle']", "[class*='job-title' i]"],
        company: ["[data-test='StartupName']", "[class*='company' i]", "meta[property='og:site_name']"],
        location: ["[class*='location' i]", "[data-test='JobLocation']"],
        description: ["[data-test='JobDescription']", "[class*='description' i]", "main"],
      },
      stopMarkers: ["Apply now", "Save", "Share"],
    },
    dice: {
      label: "Dice",
      depth: "experimental",
      hosts: ["dice.com", "www.dice.com"],
      hostPermissions: ["https://www.dice.com/*"],
      selectors: {
        title: ["h1", "[data-cy='jobTitle']", "[data-testid='job-title']", "[class*='job-title' i]"],
        company: ["[data-cy='companyName']", "[data-testid='company-name']", "[class*='company' i]", "meta[property='og:site_name']"],
        location: ["[data-cy='location']", "[data-testid='job-location']", "[class*='location' i]"],
        description: ["[data-cy='jobDescription']", "[data-testid='job-description']", "[class*='job-description' i]", "main"],
      },
      stopMarkers: ["Apply now", "Report this job", "Dice Id"],
    },
  };

  function hostnameFor(url) {
    try {
      return new URL(url || "https://invalid.local").hostname.toLowerCase();
    } catch {
      return "";
    }
  }

  function hostMatches(hostname, config) {
    const exactHosts = config.hosts || [];
    const suffixes = config.suffixes || [];
    return exactHosts.includes(hostname) || suffixes.some((suffix) => hostname === suffix || hostname.endsWith(`.${suffix}`));
  }

  function providerForUrl(url) {
    const hostname = hostnameFor(url);
    const match = Object.entries(providers).find(([, config]) => hostMatches(hostname, config));
    return match ? match[0] : "unknown";
  }

  function configFor(provider) {
    return providers[provider] || null;
  }

  function depthFor(provider) {
    return configFor(provider)?.depth || "experimental";
  }

  function allowed(url) {
    return providerForUrl(url) !== "unknown";
  }

  root.ApplyTexProviders = {
    providers,
    providerForUrl,
    configFor,
    depthFor,
    allowed,
  };
})();
