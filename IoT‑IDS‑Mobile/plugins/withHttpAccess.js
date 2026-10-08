const { withAndroidManifest, withInfoPlist } = require('expo/config-plugins');

module.exports = function withHttpAccess(config) {
  config = withAndroidManifest(config, (result) => {
    const application = result.modResults.manifest.application?.[0];
    if (!application) throw new Error('Android application manifest is missing');
    application.$ = { ...application.$, 'android:usesCleartextTraffic': 'true' };
    return result;
  });
  return withInfoPlist(config, (result) => {
    result.modResults.NSAppTransportSecurity = {
      ...result.modResults.NSAppTransportSecurity,
      NSAllowsArbitraryLoads: true,
    };
    return result;
  });
};
