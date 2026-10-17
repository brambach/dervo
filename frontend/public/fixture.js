(({
    fallback,
    identityOverrideKey,
    relayUrl
  }) => {
    // If seedActiveIdentity() ran before this script (the normal ordering),
    // use its pubkey so the community matches the active identity and
    // migrateMachineOnboardingCompletion's strict voucher accepts it.
    // Mirror readStoredIdentityOverride()'s shape validation: require all
    // three fields to be non-empty strings; fall through to fallback on
    // malformed JSON, stored null, or partial objects.
    let overridePubkey;
    try {
      const overrideRaw = window.localStorage.getItem(identityOverrideKey);
      if (overrideRaw !== null) {
        const parsed = JSON.parse(overrideRaw);
        if (parsed !== null && typeof parsed === "object" && typeof parsed.pubkey === "string" && typeof parsed.privateKey === "string" && typeof parsed.username === "string") {
          overridePubkey = parsed.pubkey;
        }
      }
    } catch {
      // malformed entry — fall through to fallback
    }
    const communityId = "e2e-default-community";
    const community = {
      id: communityId,
      name: "E2E Test",
      relayUrl,
      pubkey: overridePubkey !== null && overridePubkey !== void 0 ? overridePubkey : fallback,
      addedAt: new Date().toISOString()
    };
    window.localStorage.setItem("buzz-communities", JSON.stringify([community]));
    window.localStorage.setItem("buzz-active-community-id", communityId);
  })({"fallback":"deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef","identityOverrideKey":"buzz:e2e-identity-override.v1","relayUrl":"ws://localhost:3000"});
(({
    onboardingPrefix,
    pubkeys: pubkeysToSeed,
    relayUrl,
    welcomePrefix
  }) => {
    const welcomeScope = encodeURIComponent(relayUrl);
    for (const pubkey of pubkeysToSeed) {
      window.localStorage.setItem(`${onboardingPrefix}${pubkey}`, "true");
      window.localStorage.setItem(`${welcomePrefix}${welcomeScope}:${pubkey}`, "true");
    }
  })({"onboardingPrefix":"buzz-onboarding-complete.v1:","pubkeys":["deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef","e5ebc6cdb579be112e336cc319b5989b4bb6af11786ea90dbe52b5f08d741b34","953d3363262e86b770419834c53d2446409db6d918a57f8f339d495d54ab001f","bb22a5299220cad76ffd46190ccbeede8ab5dc260faa28b6e5a2cb31b9aff260","554cef57437abac34522ac2c9f0490d685b72c80478cf9f7ed6f9570ee8624ea","df8e91b86fda13a9a67896df77232f7bdab2ba9c3e165378e1ba3d24c13a328e"],"relayUrl":"ws://localhost:3000","welcomePrefix":"buzz-welcome-channel-ensured.v2:"});
(({
    key,
    ids
  }) => {
    const overrides = {};
    for (const id of ids) overrides[id] = true;
    window.localStorage.setItem(key, JSON.stringify(overrides));
  })({"key":"buzz-feature-overrides-v1","ids":["workflows","threadScopedAcpSessions","projects","pulse","forum","agentManagedProfiles"]});
(({
    identity: bridgeIdentity,
    mock,
    mode,
    relayHttpUrl,
    relayWsUrl,
    autoConnectDefaultRelay
  }) => {
    var _testWindow$__BUZZ_E;
    const notificationLog = [];
    const notificationInstances = [];
    class MockNotification extends EventTarget {
      static async requestPermission() {
        return MockNotification.permission;
      }
      constructor(title, options) {
        var _options$body;
        super();
        this.body = void 0;
        this.onclick = null;
        this.title = void 0;
        this.title = title;
        this.body = (_options$body = options === null || options === void 0 ? void 0 : options.body) !== null && _options$body !== void 0 ? _options$body : null;
        notificationInstances.push(this);
        notificationLog.push({
          body: this.body,
          title: this.title
        });
      }
      close() {}
    }
    MockNotification.permission = "granted";
    Object.defineProperty(window, "Notification", {
      configurable: true,
      value: MockNotification,
      writable: true
    });
    const testWindow = window;
    const currentConfig = (_testWindow$__BUZZ_E = testWindow.__BUZZ_E2E__) !== null && _testWindow$__BUZZ_E !== void 0 ? _testWindow$__BUZZ_E : {};
    testWindow.__BUZZ_E2E__ = {
      ...currentConfig,
      identity: bridgeIdentity !== null && bridgeIdentity !== void 0 ? bridgeIdentity : currentConfig.identity,
      mock,
      mode,
      relayHttpUrl: relayHttpUrl !== null && relayHttpUrl !== void 0 ? relayHttpUrl : currentConfig.relayHttpUrl,
      relayWsUrl: relayWsUrl !== null && relayWsUrl !== void 0 ? relayWsUrl : currentConfig.relayWsUrl,
      autoConnectDefaultRelay: autoConnectDefaultRelay !== null && autoConnectDefaultRelay !== void 0 ? autoConnectDefaultRelay : currentConfig.autoConnectDefaultRelay
    };
    testWindow.__BUZZ_E2E_APP_BADGE_COUNT__ = 0;
    testWindow.__BUZZ_E2E_APP_BADGE_STATE__ = "none";
    testWindow.__BUZZ_E2E_CLICK_NOTIFICATION__ = index => {
      var _notification$onclick;
      const notification = notificationInstances[index];
      if (!notification) {
        return false;
      }
      const event = new Event("click");
      notification.dispatchEvent(event);
      (_notification$onclick = notification.onclick) === null || _notification$onclick === void 0 || _notification$onclick.call(notification, event);
      return true;
    };
    testWindow.__BUZZ_E2E_NOTIFICATIONS__ = notificationLog;
  })({"mode":"mock"});